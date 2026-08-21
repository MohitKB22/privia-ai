"""The rule engine, LLM plumbing and step references."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from privia_agent.references import ReferenceError, has_reference, resolve_arguments
from privia_llm.base import extract_json, validate_against_schema
from privia_llm.providers.heuristic import HeuristicProvider, compose_response
from privia_llm.rules import build_plan, classify, extract_datetime, extract_entities
from privia_shared.enums import Intent
from privia_shared.errors import LLMInvalidOutputError
from privia_shared.tools import ToolResult

NOW = datetime(2026, 8, 14, 10, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("utterance", "expected"),
    [
        ("Find my resume.", Intent.FILE_SEARCH),
        ("Summarize the latest project report.", Intent.SUMMARIZE),
        ("Create a note called interview preparation.", Intent.NOTE_CREATE),
        ("Show my meetings tomorrow.", Intent.CALENDAR_VIEW),
        ("Schedule a meeting with Rahul at 3 PM tomorrow.", Intent.CALENDAR_CREATE),
        ("Cancel the standup meeting.", Intent.CALENDAR_CANCEL),
        ("Draft an email to the recruiter.", Intent.EMAIL_DRAFT),
        ("Send the email.", Intent.EMAIL_SEND),
        ("Run the unit tests.", Intent.TERMINAL_RUN),
        ("What files did you access during this task?", Intent.ACTIVITY_REVIEW),
        ("Turn off cloud AI.", Intent.PRIVACY_CONTROL),
        ("Delete everything you remember about me.", Intent.MEMORY_FORGET),
        ("Remember that I prefer concise answers.", Intent.MEMORY_SAVE),
        ("What do you remember about me?", Intent.MEMORY_RECALL),
        ("hi", Intent.CHITCHAT),
        ("read https://example.com/pricing", Intent.WEB_READ),
    ],
)
def test_intent_classification(utterance: str, expected: Intent) -> None:
    result = classify(utterance)
    assert result.intent is expected, f"{utterance!r} -> {result.intent} ({result.rationale})"
    assert 0.0 < result.confidence <= 0.99


def test_note_creation_is_not_mistaken_for_a_calendar_event() -> None:
    """'interview' appears in both vocabularies; the object decides."""
    assert classify("Create a note called interview preparation.").intent is Intent.NOTE_CREATE
    assert classify("Schedule an interview with Sam on Friday").intent is Intent.CALENDAR_CREATE


@pytest.mark.parametrize(
    ("phrase", "expected_iso"),
    [
        ("tomorrow at 3 PM", "2026-08-15T15:00:00+00:00"),
        ("today at 9am", "2026-08-14T09:00:00+00:00"),
        ("2026-09-01T15:00", "2026-09-01T15:00:00+00:00"),
        ("in 3 days", "2026-08-17T09:00:00+00:00"),
        ("noon today", "2026-08-14T12:00:00+00:00"),
        ("day after tomorrow", "2026-08-16T09:00:00+00:00"),
    ],
)
def test_datetime_extraction(phrase: str, expected_iso: str) -> None:
    resolved = extract_datetime(phrase, now=NOW)
    assert resolved is not None
    assert resolved[0].isoformat() == expected_iso


def test_datetime_extraction_returns_none_when_absent() -> None:
    assert extract_datetime("find my resume", now=NOW) is None


def test_entity_extraction() -> None:
    entities = extract_entities(
        "Email rahul@example.com about /home/me/report.md and check https://example.com, "
        "run `pytest -q` for 45 minutes",
        now=NOW,
    )
    kinds = {entity.type for entity in entities}
    assert {"email", "path", "url", "command", "duration_minutes"} <= kinds
    duration = next(e for e in entities if e.type == "duration_minutes")
    assert duration.normalized == "45"


def test_plans_map_intents_to_the_right_tools() -> None:
    cases = {
        "Find my resume.": "files.search",
        "Create a note called interview prep.": "notes.create",
        "Show my meetings tomorrow.": "calendar.list_events",
        "Draft an email to a@b.com saying hello.": "email.draft",
        "Turn off cloud AI.": "system.privacy",
        "What files did you access?": "system.activity",
    }
    for utterance, tool in cases.items():
        plan = build_plan(utterance, classify(utterance), {})
        assert plan.steps[0].tool_name == tool, utterance


def test_chained_plan_declares_its_dependency() -> None:
    utterance = "Find the project report and summarise it"
    plan = build_plan(utterance, classify(utterance), {})
    assert [step.tool_name for step in plan.steps] == ["files.search", "files.summarize"]
    assert plan.steps[1].depends_on == (0,)
    assert plan.steps[1].arguments["path"].startswith("${0.")


def test_chitchat_produces_no_tool_calls() -> None:
    assert build_plan("hi", classify("hi"), {}).steps == ()


def test_email_plan_extracts_recipient_and_body() -> None:
    utterance = "Draft an email to rahul@example.com saying I'll send the report tomorrow"
    step = build_plan(utterance, classify(utterance), {}).steps[0]
    assert step.arguments["to"] == ["rahul@example.com"]
    assert "report tomorrow" in step.arguments["body"].lower()


def test_memory_recall_of_everything_uses_an_empty_query() -> None:
    utterance = "What do you remember about me?"
    step = build_plan(utterance, classify(utterance), {}).steps[0]
    assert step.arguments["query"] == ""


def test_memory_recall_of_a_subject_keeps_it() -> None:
    utterance = "What do you know about Rahul"
    step = build_plan(utterance, classify(utterance), {}).steps[0]
    assert step.arguments["query"] == "Rahul"


# --- JSON extraction --------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        '{"intent": "file_search"}',
        '```json\n{"intent": "file_search"}\n```',
        'Sure! Here it is:\n{"intent": "file_search"}\nHope that helps.',
        '```\n{"intent": "file_search"}\n```',
    ],
)
def test_extract_json_handles_model_prose_and_fences(raw: str) -> None:
    assert extract_json(raw)["intent"] == "file_search"


@pytest.mark.parametrize("raw", ["", "   ", "no json at all", "[1, 2"])
def test_extract_json_rejects_garbage(raw: str) -> None:
    with pytest.raises(LLMInvalidOutputError):
        extract_json(raw)


def test_schema_validation_checks_required_types_and_enums() -> None:
    schema = {
        "type": "object",
        "required": ["intent", "confidence"],
        "properties": {
            "intent": {"type": "string", "enum": ["file_search", "chitchat"]},
            "confidence": {"type": "number"},
        },
    }
    validate_against_schema({"intent": "file_search", "confidence": 0.9}, schema)
    with pytest.raises(LLMInvalidOutputError, match="Required field"):
        validate_against_schema({"intent": "file_search"}, schema)
    with pytest.raises(LLMInvalidOutputError, match="should be number"):
        validate_against_schema({"intent": "file_search", "confidence": "high"}, schema)
    with pytest.raises(LLMInvalidOutputError, match="must be one of"):
        validate_against_schema({"intent": "nope", "confidence": 1}, schema)


async def test_heuristic_provider_answers_its_own_task_markers() -> None:
    provider = HeuristicProvider()
    health = await provider.health_check()
    assert health.available
    assert "Not a language model" in health.detail

    payload = await provider.structured_output(
        [
            __import__("privia_llm.base", fromlist=["ChatMessage"]).ChatMessage(
                "system", "PRIVIA_TASK: classify"
            ),
            __import__("privia_llm.base", fromlist=["ChatMessage"]).ChatMessage(
                "user", "find my resume"
            ),
        ],
        {"type": "object", "required": ["intent"], "properties": {"intent": {"type": "string"}}},
    )
    assert payload["intent"] == "file_search"


def test_compose_response_renders_tool_results() -> None:
    rendered = compose_response(
        "find my resume",
        {
            "intent": "file_search",
            "tool_results": [
                {
                    "tool_name": "files.search",
                    "success": True,
                    "data": {"count": 1, "files": [{"name": "resume.md", "path": "/a/resume.md"}]},
                }
            ],
        },
    )
    assert "resume.md" in rendered


def test_compose_response_reports_failures_honestly() -> None:
    rendered = compose_response(
        "find my resume",
        {
            "tool_results": [
                {"tool_name": "files.search", "success": False, "error": "Permission required"}
            ]
        },
    )
    assert "did not work" in rendered
    assert "Permission required" in rendered


# --- step references --------------------------------------------------------


def test_reference_resolution() -> None:
    results = [
        ToolResult.ok(
            {"files": [{"path": "/a/report.md"}, {"path": "/a/other.md"}]}, tool_name="files.search"
        )
    ]
    resolved = resolve_arguments({"path": "${0.files.0.path}"}, results)
    assert resolved["path"] == "/a/report.md"


def test_inline_reference_interpolation() -> None:
    results = [ToolResult.ok({"count": 3}, tool_name="files.search")]
    resolved = resolve_arguments({"body": "I found ${0.count} files"}, results)
    assert resolved["body"] == "I found 3 files"


def test_nested_reference_resolution() -> None:
    results = [ToolResult.ok({"a": {"b": "value"}}, tool_name="t")]
    assert resolve_arguments({"x": {"y": ["${0.a.b}"]}}, results)["x"]["y"][0] == "value"


def test_reference_to_an_empty_list_fails_clearly() -> None:
    results = [ToolResult.ok({"files": []}, tool_name="files.search")]
    with pytest.raises(ReferenceError, match="index 0 does not exist"):
        resolve_arguments({"path": "${0.files.0.path}"}, results)


def test_reference_to_a_failed_step_fails() -> None:
    results = [ToolResult.fail("nope", tool_name="files.search")]
    with pytest.raises(ReferenceError, match="failed"):
        resolve_arguments({"path": "${0.files.0.path}"}, results)


def test_reference_to_a_future_step_fails() -> None:
    with pytest.raises(ReferenceError, match="has not run yet"):
        resolve_arguments({"path": "${3.files.0.path}"}, [])


def test_has_reference() -> None:
    assert has_reference("${0.a}")
    assert has_reference("prefix ${0.a} suffix")
    assert not has_reference("plain")
    assert not has_reference(42)
