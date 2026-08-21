"""End-to-end flows through the HTTP API."""

from __future__ import annotations

import io
import json
import math
import struct
import wave
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e


def grant(client, scope: str, resources: list[str] | None = None) -> None:
    response = client.post(
        "/api/v1/permissions", json={"scope": scope, "grant": True, "resources": resources or []}
    )
    assert response.status_code == 200, response.text


# ------------------------------------------------------------------- basics


def test_health_and_status(api_client) -> None:
    health = api_client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] in {"ok", "degraded"}
    assert health.headers["x-request-id"].startswith("req_")

    status = api_client.get("/api/v1/status").json()
    assert status["tools"] > 20
    assert status["privacy"]["cloud_processing_enabled"] is False
    assert status["privacy"]["data_leaving_device"] is False
    assert status["privacy"]["telemetry_enabled"] is False


def test_security_headers_are_present(api_client) -> None:
    headers = api_client.get("/health").headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in headers["content-security-policy"]


def test_openapi_documents_every_router(api_client) -> None:
    spec = api_client.get("/openapi.json").json()
    paths = set(spec["paths"])
    for expected in (
        "/health",
        "/api/v1/status",
        "/api/v1/chat",
        "/api/v1/chat/stream",
        "/api/v1/tools",
        "/api/v1/tools/execute",
        "/api/v1/permissions",
        "/api/v1/memory",
        "/api/v1/audit",
        "/api/v1/privacy",
        "/api/v1/integrations",
        "/api/v1/voice/transcribe",
        "/api/v1/voice/synthesize",
    ):
        assert expected in paths, expected


# ------------------------------------------------------------- error envelope


@pytest.mark.parametrize(
    ("method", "path", "body", "status", "code"),
    [
        ("post", "/api/v1/chat", {}, 422, "VALIDATION_ERROR"),
        ("get", "/api/v1/tools/nope.nope", None, 404, "TOOL_NOT_FOUND"),
        ("get", "/api/v1/sessions/ses_missing", None, 404, "NOT_FOUND"),
        ("get", "/api/v1/files/read?path=/etc/passwd", None, 403, "PATH_NOT_ALLOWED"),
    ],
)
def test_errors_use_one_envelope(api_client, method, path, body, status, code) -> None:
    response = getattr(api_client, method)(path, **({"json": body} if body is not None else {}))
    assert response.status_code == status
    payload = response.json()
    assert set(payload["error"]) == {"code", "message", "request_id", "details"}
    assert payload["error"]["code"] == code
    assert payload["error"]["request_id"]
    assert "Traceback" not in response.text


# ------------------------------------------------------------ permission flow


def test_chat_asks_for_permission_then_works(api_client, workspace: Path) -> None:
    first = api_client.post("/api/v1/chat", json={"message": "Find the project report"}).json()
    session_id = first["session_id"]
    assert first["permission_prompt"] is not None
    assert first["permission_prompt"]["missing_scopes"] == ["files:read"]

    grant(api_client, "files:read", [str(workspace)])
    second = api_client.post(
        "/api/v1/chat", json={"message": "Find the project report", "session_id": session_id}
    ).json()
    assert "project_report.md" in second["response"]
    assert any("project_report.md" in path for path in second["accessed_resources"])
    assert second["processing_location"] == "local"


def test_permissions_persist_across_a_restart(settings, tmp_path) -> None:
    """A grant is stored in the database, not just in memory."""
    from fastapi.testclient import TestClient

    from privia_api.app import create_app
    from privia_api.container import build_container

    with TestClient(
        create_app(
            settings, container=build_container(settings, offline=True, configure_logs=False)
        )
    ) as first:
        grant(first, "notes:read")

    with TestClient(
        create_app(
            settings, container=build_container(settings, offline=True, configure_logs=False)
        )
    ) as second:
        scopes = {
            s["scope"]: s["state"] for s in second.get("/api/v1/permissions").json()["scopes"]
        }
        assert scopes["notes:read"] == "granted"


# ---------------------------------------------------------- confirmation flow


def test_email_send_requires_approval_and_cannot_be_replayed(api_client) -> None:
    grant(api_client, "email:draft")
    grant(api_client, "email:send")
    session_id = api_client.post("/api/v1/sessions", json={"title": "t"}).json()["session_id"]

    api_client.post(
        "/api/v1/chat",
        json={
            "message": "Draft an email to rahul@example.com saying I'll send the report tomorrow.",
            "session_id": session_id,
        },
    )

    paused = api_client.post(
        "/api/v1/chat", json={"message": "Send the email.", "session_id": session_id}
    ).json()
    assert paused["status"] == "awaiting_confirmation"
    confirmation = paused["pending_confirmation"]
    assert confirmation["details"]["To"] == "rahul@example.com"
    assert "report tomorrow" in confirmation["details"]["Body"]

    rejected = api_client.post(
        "/api/v1/chat",
        json={
            "message": "Send the email.",
            "session_id": session_id,
            "confirmation_id": confirmation["id"],
            "confirm": False,
        },
    ).json()
    assert rejected["status"] == "denied"

    reasked = api_client.post(
        "/api/v1/chat", json={"message": "Send the email.", "session_id": session_id}
    ).json()
    confirmation_id = reasked["pending_confirmation"]["id"]

    approved = api_client.post(
        "/api/v1/chat",
        json={
            "message": "Send the email.",
            "session_id": session_id,
            "confirmation_id": confirmation_id,
            "confirm": True,
        },
    ).json()
    assert approved["status"] == "completed"
    assert "Sent" in approved["response"]

    replay = api_client.post(
        "/api/v1/chat",
        json={
            "message": "Send the email.",
            "session_id": session_id,
            "confirmation_id": confirmation_id,
            "confirm": True,
        },
    )
    assert replay.status_code == 409
    assert replay.json()["error"]["code"] == "CONFLICT"


def test_a_forged_confirmation_id_is_refused(api_client) -> None:
    response = api_client.post(
        "/api/v1/chat",
        json={"message": "Send the email.", "confirmation_id": "cfm_MADEUP", "confirm": True},
    )
    assert response.status_code == 404


def test_direct_tool_execution_still_confirms(api_client, workspace: Path) -> None:
    grant(api_client, "files:delete", [str(workspace)])
    victim = workspace / "victim.txt"
    victim.write_text("bye")

    response = api_client.post(
        "/api/v1/tools/execute",
        json={"tool_name": "files.delete", "arguments": {"path": str(victim)}},
    )
    assert response.status_code == 428
    assert response.json()["error"]["code"] == "CONFIRMATION_REQUIRED"
    assert victim.exists()

    confirmation = response.json()["error"]["details"]["confirmation"]
    approved = api_client.post(
        "/api/v1/tools/execute",
        json={
            "tool_name": "files.delete",
            "arguments": {"path": str(victim)},
            "confirmation_id": confirmation["id"],
        },
    )
    assert approved.status_code == 200
    assert approved.json()["success"] is True
    assert not victim.exists()


# ------------------------------------------------------------------- privacy


def test_cloud_cannot_be_enabled_without_a_provider(api_client) -> None:
    response = api_client.post("/api/v1/privacy", json={"cloud_processing": True})
    assert response.status_code == 400
    assert api_client.get("/api/v1/privacy").json()["cloud_processing"] is False


def test_export_contains_everything_except_secrets(api_client) -> None:
    api_client.post("/api/v1/notes", json={"title": "Exported", "body": "content"})
    api_client.post(
        "/api/v1/integrations/secrets", json={"key": "smtp_password", "value": "hunter2"}
    )

    export = api_client.get("/api/v1/privacy/export").json()
    assert {"sessions", "messages", "memories", "notes", "audit", "permissions"} <= set(export)
    assert any(note["title"] == "Exported" for note in export["notes"])
    assert "hunter2" not in json.dumps(export)


def test_purge_deletes_only_what_was_asked(api_client) -> None:
    api_client.post("/api/v1/notes", json={"title": "Keep me", "body": "x"})
    api_client.post("/api/v1/memory", json={"content": "Remember this"})
    api_client.post("/api/v1/chat", json={"message": "hi"})

    result = api_client.post("/api/v1/privacy/purge?conversations=true&memories=false").json()
    assert result["deleted"]["sessions"] >= 1
    assert api_client.get("/api/v1/memory").json()["count"] == 1
    assert api_client.get("/api/v1/notes").json()["count"] == 1


def test_directory_grant_and_revoke(api_client, tmp_path: Path) -> None:
    new_dir = tmp_path / "Extra"
    new_dir.mkdir()

    added = api_client.post("/api/v1/permissions/directories", json={"path": str(new_dir)})
    assert added.status_code == 200
    assert str(new_dir) in added.json()["allowed_directories"]

    removed = api_client.request(
        "DELETE", "/api/v1/permissions/directories", json={"path": str(new_dir)}
    )
    assert str(new_dir) not in removed.json()["allowed_directories"]


@pytest.mark.parametrize("path", ["/", "/etc", "/proc"])
def test_system_directories_cannot_be_allowed(api_client, path: str) -> None:
    response = api_client.post("/api/v1/permissions/directories", json={"path": path})
    assert response.status_code in (400, 403)


def test_a_missing_directory_is_refused(api_client, tmp_path: Path) -> None:
    response = api_client.post(
        "/api/v1/permissions/directories", json={"path": str(tmp_path / "nope")}
    )
    assert response.status_code == 400


# --------------------------------------------------------------------- audit


def test_everything_is_audited(api_client, workspace: Path) -> None:
    grant(api_client, "files:read", [str(workspace)])
    api_client.post("/api/v1/chat", json={"message": "Find the project report"})

    audit = api_client.get("/api/v1/audit?limit=100").json()
    actions = {event["action"] for event in audit["events"]}
    assert {"run.started", "run.completed", "tool.invoked", "tool.succeeded"} <= actions

    runs = api_client.get("/api/v1/audit/runs").json()["runs"]
    assert runs
    detail = api_client.get(f"/api/v1/audit/runs/{runs[0]['id']}").json()
    assert detail["run"]["input_text"]
    assert detail["tool_calls"]


def test_permission_denial_is_audited(api_client) -> None:
    api_client.post("/api/v1/chat", json={"message": "Find my resume"})
    events = api_client.get("/api/v1/audit?limit=200").json()["events"]
    assert any(event["action"] == "permission.requested" for event in events)


# --------------------------------------------------------------------- voice


def make_wav(seconds: float = 0.4, amplitude: float = 0.4) -> bytes:
    rate = 16_000
    frames = int(seconds * rate)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(
            b"".join(
                struct.pack("<h", int(amplitude * 32767 * math.sin(2 * math.pi * 220 * i / rate)))
                for i in range(frames)
            )
        )
    return buffer.getvalue()


def test_voice_status_describes_the_recording_policy(api_client) -> None:
    payload = api_client.get("/api/v1/voice/status").json()
    assert "never written to disk" in payload["recording_policy"]


def test_silence_is_reported_rather_than_hallucinated(api_client) -> None:
    response = api_client.post(
        "/api/v1/voice/transcribe", files={"audio": ("s.wav", make_wav(amplitude=0.0), "audio/wav")}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["speech_detected"] is False
    assert payload["text"] == ""


def test_non_wav_audio_is_refused(api_client) -> None:
    response = api_client.post(
        "/api/v1/voice/transcribe", files={"audio": ("s.mp3", b"ID3fake", "audio/mpeg")}
    )
    assert response.status_code == 400
    assert "WAV" in response.json()["error"]["message"]


def test_tts_unavailable_is_reported_cleanly(api_client) -> None:
    response = api_client.post("/api/v1/voice/synthesize", json={"text": "hello"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "TTS_UNAVAILABLE"


# ----------------------------------------------------------------- streaming


def test_streaming_produces_a_well_formed_event_sequence(api_client, workspace: Path) -> None:
    grant(api_client, "files:read", [str(workspace)])
    with api_client.stream(
        "POST", "/api/v1/chat/stream", json={"message": "Find the project report"}
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        events = [
            json.loads(line[6:])
            for line in response.iter_lines()
            if line.startswith("data: ") and line[6:].strip() not in ("", "{}")
        ]
    kinds = [event.get("type") for event in events]
    assert kinds[0] == "start"
    assert "tool" in kinds
    assert "token" in kinds
    assert "done" in kinds


# ------------------------------------------------------------------- secrets


def test_only_known_credentials_may_be_stored(api_client) -> None:
    assert (
        api_client.post(
            "/api/v1/integrations/secrets", json={"key": "arbitrary_key", "value": "x"}
        ).status_code
        == 400
    )

    stored = api_client.post(
        "/api/v1/integrations/secrets", json={"key": "openai_api_key", "value": "sk-secret-value"}
    )
    assert stored.status_code == 200

    listing = api_client.get("/api/v1/integrations/secrets").json()
    assert "openai_api_key" in listing["stored_keys"]
    assert "sk-secret-value" not in json.dumps(listing)


def test_metrics_are_local_only(api_client) -> None:
    api_client.get("/health")
    metrics = api_client.get("/api/v1/metrics").json()
    assert "counters" in metrics
    assert any(key.startswith("http.responses") for key in metrics["counters"])
