"""Identifiers, errors, tool specs, embeddings, speech and the ICS calendar."""

from __future__ import annotations

import io
import math
import struct
import wave
from pathlib import Path

import pytest

from privia_embeddings.base import cosine_similarity, normalize
from privia_embeddings.local import LocalHashEmbedder
from privia_integrations.calendar.ics import IcsCalendarProvider
from privia_integrations.email.base import parse_address, parse_addresses, validate_subject
from privia_integrations.files.local import summarize_text
from privia_security.audit import AuditLogger, InMemoryAuditSink
from privia_security.secrets import SecretStore
from privia_shared.domain import CalendarEvent
from privia_shared.enums import ErrorCode, RiskLevel, Scope
from privia_shared.errors import PathNotAllowedError, PriviaError, ValidationError
from privia_shared.ids import new_id, ulid, utcnow
from privia_speech import EnergyVad, decode_wav, normalise_transcript
from privia_speech.audio import AudioError
from privia_tools.registry import ToolRegistry
from privia_tools.tools import build_registry

# --- ids --------------------------------------------------------------------


def test_ulids_are_sortable_and_unique() -> None:
    """Ids must sort in creation order even inside a single millisecond."""
    values = [ulid() for _ in range(500)]
    assert len(set(values)) == 500
    assert values == sorted(values)
    assert all(len(v) == 26 for v in values)


def test_prefixed_ids() -> None:
    assert new_id("run").startswith("run_")


def test_utcnow_is_timezone_aware() -> None:
    assert utcnow().tzinfo is not None


# --- errors -----------------------------------------------------------------


def test_error_envelope_shape() -> None:
    payload = PathNotAllowedError("nope", details={"path": "/x"}).to_dict("req_1")
    assert payload["error"]["code"] == "PATH_NOT_ALLOWED"
    assert payload["error"]["request_id"] == "req_1"
    assert payload["error"]["details"] == {"path": "/x"}


def test_every_error_code_is_a_string_enum() -> None:
    assert all(isinstance(code.value, str) for code in ErrorCode)


def test_risk_levels_are_ordered() -> None:
    assert RiskLevel.CRITICAL > RiskLevel.HIGH > RiskLevel.MEDIUM > RiskLevel.LOW > RiskLevel.NONE


def test_base_error_defaults() -> None:
    error = PriviaError()
    assert error.http_status == 500
    assert error.code is ErrorCode.INTERNAL_ERROR


# --- tool registry ----------------------------------------------------------


def test_registry_contains_every_family() -> None:
    registry = build_registry()
    families = {spec.family for spec in registry.specs()}
    assert {
        "files",
        "notes",
        "calendar",
        "email",
        "browser",
        "terminal",
        "memory",
        "system",
    } <= families


def test_every_tool_declares_a_coherent_spec() -> None:
    for spec in build_registry().specs():
        assert spec.name.startswith(f"{spec.family}.")
        assert len(spec.description) >= 10
        assert spec.timeout_seconds > 0
        assert "properties" in spec.input_schema or spec.input_schema.get("type") == "object"
        # Anything that can change the world outside PRIVIA must confirm.
        if spec.risk_level >= RiskLevel.HIGH:
            assert spec.requires_confirmation, f"{spec.name} is high risk but does not confirm"


def test_destructive_tools_never_retry() -> None:
    for spec in build_registry().specs():
        if spec.name in {"email.send", "files.delete", "calendar.cancel_event", "memory.forget"}:
            assert spec.retry_policy.max_attempts == 1, spec.name


def test_send_and_delete_require_the_right_scopes() -> None:
    registry = build_registry()
    assert Scope.EMAIL_SEND in registry.get("email.send").spec().scopes
    assert Scope.FILES_DELETE in registry.get("files.delete").spec().scopes
    assert Scope.TERMINAL_EXEC in registry.get("terminal.run").spec().scopes


def test_duplicate_registration_is_refused() -> None:
    registry = ToolRegistry()
    tool = build_registry().get("files.search")
    registry.register(tool)
    with pytest.raises(ValueError, match="already registered"):
        registry.register(tool)


def test_unknown_tool_lookup() -> None:
    from privia_shared.errors import ToolNotFoundError

    with pytest.raises(ToolNotFoundError):
        build_registry().get("nope.nope")


# --- embeddings -------------------------------------------------------------


async def test_local_embeddings_are_deterministic_and_normalised() -> None:
    embedder = LocalHashEmbedder()
    first = await embedder.embed_one("the quarterly revenue report")
    second = await embedder.embed_one("the quarterly revenue report")
    assert first == second
    assert math.isclose(math.sqrt(sum(v * v for v in first)), 1.0, rel_tol=1e-6)


async def test_related_text_scores_higher_than_unrelated() -> None:
    embedder = LocalHashEmbedder()
    query = await embedder.embed_one("quarterly revenue report")
    related = await embedder.embed_one("the revenue report for this quarter")
    unrelated = await embedder.embed_one("recipe for banana bread")
    assert cosine_similarity(query, related) > cosine_similarity(query, unrelated)


def test_cosine_similarity_edges() -> None:
    assert cosine_similarity([], []) == 0.0
    assert cosine_similarity([0, 0], [1, 1]) == 0.0
    assert math.isclose(cosine_similarity([1, 0], [1, 0]), 1.0)
    assert math.isclose(cosine_similarity([1, 0], [-1, 0]), -1.0)


def test_normalize_handles_a_zero_vector() -> None:
    assert normalize([0.0, 0.0]) == [0.0, 0.0]


# --- speech -----------------------------------------------------------------


def make_wav(
    seconds: float = 0.5, rate: int = 44_100, amplitude: float = 0.4, channels: int = 2
) -> bytes:
    frames = int(seconds * rate)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(
            b"".join(
                struct.pack(
                    "<" + "h" * channels,
                    *([int(amplitude * 32767 * math.sin(2 * math.pi * 220 * i / rate))] * channels),
                )
                for i in range(frames)
            )
        )
    return buffer.getvalue()


def test_wav_is_resampled_to_mono_16k() -> None:
    clip = decode_wav(make_wav())
    assert clip.sample_rate == 16_000
    assert clip.channels == 1
    assert 0.4 < clip.duration_seconds < 0.6


def test_non_wav_audio_is_refused_rather_than_guessed() -> None:
    with pytest.raises(AudioError, match="Only WAV"):
        decode_wav(b"ID3\x03fake mp3 data")
    with pytest.raises(AudioError):
        decode_wav(b"")


def test_vad_distinguishes_speech_from_silence() -> None:
    vad = EnergyVad()
    assert vad.analyse(decode_wav(make_wav(amplitude=0.4))).speech_detected
    assert not vad.analyse(decode_wav(make_wav(amplitude=0.0))).speech_detected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  um, find   my resume ,  you know ", "Find my resume"),
        ("uh schedule a meeting", "Schedule a meeting"),
        ("I mean, send the email.", "Send the email."),
        ("", ""),
    ],
)
def test_transcript_normalisation(raw: str, expected: str) -> None:
    assert normalise_transcript(raw) == expected


# --- email helpers ----------------------------------------------------------


def test_address_parsing() -> None:
    parsed = parse_address("Rahul Kumar <rahul@example.com>")
    assert parsed.address == "rahul@example.com"
    assert parsed.name == "Rahul Kumar"
    assert parse_address("a@b.co").name is None


@pytest.mark.parametrize("bad", ["not-an-email", "@example.com", "a@", "a b@c.com", "a@b", ""])
def test_invalid_addresses_are_refused(bad: str) -> None:
    with pytest.raises(ValidationError):
        parse_address(bad)


def test_header_injection_via_address_is_refused() -> None:
    with pytest.raises(ValidationError, match="newline"):
        parse_address("a@b.com\nBcc: victim@evil.test")


def test_header_injection_via_subject_is_refused() -> None:
    with pytest.raises(ValidationError, match="newline"):
        validate_subject("Hello\nBcc: victim@evil.test")


def test_recipient_cap() -> None:
    with pytest.raises(ValidationError, match="at most"):
        parse_addresses([f"user{i}@example.com" for i in range(30)])


# --- calendar ---------------------------------------------------------------


async def test_ics_round_trip_preserves_fields(tmp_path: Path) -> None:
    provider = IcsCalendarProvider(tmp_path)
    event = CalendarEvent(
        id="evt_1",
        title="Sync; with, Rahul",
        start=utcnow(),
        end=utcnow(),
        location="Room 2, Floor 3",
        description="Line one\nLine two",
        participants=("rahul@example.com",),
    )
    event = event.model_copy(update={"end": event.start.replace(microsecond=0)})
    event = event.model_copy(
        update={"end": event.start + __import__("datetime").timedelta(hours=1)}
    )
    await provider.create_event(event)

    reread = await provider.list_events()
    assert len(reread) == 1
    stored = reread[0]
    assert stored.title == "Sync; with, Rahul"
    assert stored.location == "Room 2, Floor 3"
    assert stored.description == "Line one\nLine two"
    assert stored.participants == ("rahul@example.com",)


async def test_ics_file_is_valid_icalendar(tmp_path: Path) -> None:
    provider = IcsCalendarProvider(tmp_path)
    now = utcnow()
    await provider.create_event(
        CalendarEvent(
            id="evt_2",
            title="Standup",
            start=now,
            end=now + __import__("datetime").timedelta(minutes=15),
        )
    )
    # Read bytes: read_text() applies universal newlines and would hide the
    # CRLF line endings RFC 5545 requires.
    raw = next(tmp_path.glob("*.ics")).read_bytes()  # noqa: ASYNC240
    assert raw.startswith(b"BEGIN:VCALENDAR")
    assert b"VERSION:2.0" in raw
    assert raw.rstrip().endswith(b"END:VCALENDAR")
    assert b"\r\n" in raw


async def test_cancelled_events_are_hidden_by_default(tmp_path: Path) -> None:
    provider = IcsCalendarProvider(tmp_path)
    now = utcnow()
    await provider.create_event(
        CalendarEvent(
            id="evt_3", title="X", start=now, end=now + __import__("datetime").timedelta(hours=1)
        )
    )
    await provider.cancel_event("evt_3")
    assert await provider.list_events() == []
    assert len(await provider.list_events(include_cancelled=True)) == 1


# --- summarisation ----------------------------------------------------------


def test_extractive_summary_is_shorter_and_deterministic() -> None:
    text = " ".join(
        [
            "Revenue grew twelve percent this quarter driven by enterprise deals.",
            "Costs held flat against the plan for the year.",
            "The team shipped four features including the ingest pipeline.",
            "Churn fell to two percent which is the lowest recorded.",
            "Hiring continues for two backend roles in the platform team.",
            "Revenue guidance for the next quarter is unchanged from before.",
            "The board reviewed the revenue plan and approved it.",
        ]
    )
    summary = summarize_text(text, max_sentences=3)
    assert len(summary) < len(text)
    assert summary == summarize_text(text, max_sentences=3)


def test_summary_of_empty_text() -> None:
    assert "empty" in summarize_text("")


# --- secrets ----------------------------------------------------------------


def test_secret_store_round_trip_and_encryption_at_rest(tmp_path: Path) -> None:
    store = SecretStore(tmp_path)
    reference = store.set("smtp_password", "hunter2-very-secret")
    assert store.get("smtp_password") == "hunter2-very-secret"
    assert reference.backend in {"encrypted_file", "keychain"}

    if reference.backend == "encrypted_file":
        blob = (tmp_path / "privia_secrets.enc").read_bytes()
        assert b"hunter2-very-secret" not in blob

    described = store.describe()
    assert "smtp_password" in described["stored_keys"]
    assert "hunter2-very-secret" not in str(described)

    store.delete("smtp_password")
    assert store.get("smtp_password") is None


def test_secret_store_falls_back_to_environment(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SOME_KEY", "from-env")
    assert SecretStore(tmp_path).get("some_key") == "from-env"


# --- audit ------------------------------------------------------------------


def test_audit_redacts_and_truncates() -> None:
    sink = InMemoryAuditSink()
    logger = AuditLogger([sink])
    logger.record(
        "tool.invoked",
        tool_name="email.draft",
        detail={"api_key": "sk-abcdefghijklmnopqrst", "body": "x" * 5000},
    )
    event = sink.events[0]
    assert event.detail["api_key"] == "***redacted***"
    assert len(event.detail["body"]) <= 2100


def test_a_broken_sink_cannot_break_a_request() -> None:
    class Broken:
        def append(self, event):
            raise RuntimeError("disk on fire")

    sink = InMemoryAuditSink()
    logger = AuditLogger([Broken(), sink])
    logger.record("tool.invoked")
    assert len(sink.events) == 1
