"""The agent graph, end to end, against the deterministic planner."""

from __future__ import annotations

import pytest

from privia_agent.orchestrator import Agent
from privia_embeddings.local import LocalHashEmbedder
from privia_llm.providers.heuristic import HeuristicProvider
from privia_llm.router import LLMRouter
from privia_memory.service import MemoryService
from privia_security.policy import PermissionEngine
from privia_shared.enums import AgentPhase, Intent, ProcessingLocation, RunStatus, Scope
from privia_tools.context import ToolContext


@pytest.fixture
def agent(runtime, repositories, settings, permissions) -> Agent:
    router = LLMRouter(settings, permissions, local=HeuristicProvider(), cloud=None)
    memory = MemoryService(
        repositories.memories, repositories.messages, LocalHashEmbedder(), settings
    )
    return Agent(router, runtime, repositories, memory)


async def ask(agent: Agent, context: ToolContext, text: str, approved: set[str] | None = None):
    context.approved_confirmations = approved or set()
    context.accessed_resources = []
    context.scratch = {}
    return await agent.run(text, context, request_id="req_test")


async def test_greeting_needs_no_tools(agent: Agent, context: ToolContext) -> None:
    run = await ask(agent, context, "hi")
    assert run.status is RunStatus.COMPLETED
    assert run.classification.intent is Intent.CHITCHAT
    assert run.tool_calls == ()
    assert run.response_text


async def test_records_every_phase(agent: Agent, context: ToolContext, grant_all) -> None:
    run = await ask(agent, context, "Find my resume")
    phases = [phase.phase for phase in run.phases]
    assert AgentPhase.INPUT in phases
    assert AgentPhase.CLASSIFY in phases
    assert AgentPhase.PLAN in phases
    assert AgentPhase.RESPOND in phases
    assert AgentPhase.VERIFY in phases
    assert all(phase.duration_ms >= 0 for phase in run.phases)


async def test_chained_plan_resolves_the_reference(
    agent: Agent, context: ToolContext, grant_all
) -> None:
    run = await ask(agent, context, "Find the project report and summarise it")
    assert [call.tool_name for call in run.tool_calls] == ["files.search", "files.summarize"]
    assert run.tool_results[1].success
    assert "${" not in str(run.tool_calls[1].arguments)
    assert "Revenue" in run.response_text or "revenue" in run.response_text


async def test_permission_failure_is_reported_not_hidden(
    agent: Agent, context: ToolContext
) -> None:
    run = await ask(agent, context, "Find my resume")
    assert run.status is RunStatus.COMPLETED
    assert not run.tool_results[0].success
    assert "permission" in run.response_text.lower()


async def test_confirmation_pauses_the_run(agent: Agent, context: ToolContext, grant_all) -> None:
    await ask(agent, context, "Draft an email to rahul@example.com saying hello")
    run = await ask(agent, context, "Send the email.")
    assert run.status is RunStatus.AWAITING_CONFIRMATION
    assert run.phase is AgentPhase.AWAITING_CONFIRMATION
    assert run.pending_confirmation is not None
    assert run.pending_confirmation.tool_name == "email.send"


async def test_approval_in_the_next_turn_completes_the_action(
    agent: Agent, context: ToolContext, grant_all
) -> None:
    await ask(agent, context, "Draft an email to rahul@example.com saying hello")
    paused = await ask(agent, context, "Send the email.")
    resumed = await ask(agent, context, "Send the email.", {paused.pending_confirmation.id})
    assert resumed.status is RunStatus.COMPLETED
    assert resumed.verification.passed
    assert any(r.tool_name == "email.send" and r.success for r in resumed.tool_results)


async def test_calendar_creation_round_trip(agent: Agent, context: ToolContext, grant_all) -> None:
    paused = await ask(agent, context, "Schedule a meeting with Rahul at 3 PM tomorrow")
    assert paused.pending_confirmation is not None
    details = paused.pending_confirmation.details
    assert "Time" in details and "Date" in details

    created = await ask(
        agent,
        context,
        "Schedule a meeting with Rahul at 3 PM tomorrow",
        {paused.pending_confirmation.id},
    )
    assert created.status is RunStatus.COMPLETED

    listed = await ask(agent, context, "Show my meetings tomorrow")
    assert "Meeting" in listed.response_text or "event" in listed.response_text.lower()


async def test_memory_round_trip(agent: Agent, context: ToolContext, grant_all) -> None:
    await ask(agent, context, "Remember that I prefer concise answers.")
    recalled = await ask(agent, context, "What do you remember about me?")
    assert "concise" in recalled.response_text.lower()


async def test_activity_review_lists_what_was_touched(
    agent: Agent, context: ToolContext, grant_all
) -> None:
    await ask(agent, context, "Find the project report and summarise it")
    review = await ask(agent, context, "What files did you access during this task?")
    assert review.status is RunStatus.COMPLETED
    assert "activity" in review.response_text.lower() or "tool" in review.response_text.lower()


async def test_run_is_persisted_with_its_messages(
    agent: Agent, context: ToolContext, repositories, grant_all
) -> None:
    run = await ask(agent, context, "Find my resume")
    stored = repositories.runs.get(run.id)
    assert stored is not None
    assert stored.input_text == "Find my resume"
    messages = repositories.messages.history(context.session_id)
    assert [m["role"] for m in messages][-2:] == ["user", "assistant"]


async def test_tool_calls_and_results_are_persisted(
    agent: Agent, context: ToolContext, repositories, grant_all
) -> None:
    run = await ask(agent, context, "Find my resume")
    rows = repositories.tool_calls.for_run(run.id)
    assert rows
    assert rows[0]["tool_name"] == "files.search"
    assert rows[0]["success"] == 1


async def test_verification_catches_a_failed_tool(agent: Agent, context: ToolContext) -> None:
    run = await ask(agent, context, "Find my resume")
    failed = [check for check in run.verification.checks if not check.passed]
    assert any(check.name == "tools_succeeded" for check in failed)


async def test_processing_location_is_reported(agent: Agent, context: ToolContext) -> None:
    run = await ask(agent, context, "hi")
    assert run.processing_location is ProcessingLocation.LOCAL
    assert run.model_used and "offline-planner" in run.model_used


async def test_oversized_input_is_truncated_not_rejected(
    agent: Agent, context: ToolContext
) -> None:
    run = await ask(agent, context, "hello " * 20_000)
    assert run.status is RunStatus.COMPLETED
    assert len(run.input_text) <= 16_000


async def test_streaming_emits_status_tools_and_a_terminator(
    agent: Agent, context: ToolContext, grant_all
) -> None:
    events = [
        event
        async for event in agent.stream_response("Find my resume", context, request_id="req_stream")
    ]
    kinds = [event["type"] for event in events]
    assert kinds[0] == "status"
    assert "tool" in kinds
    assert kinds[-1] == "done"
    assert events[-1]["processing_location"] == "local"


async def test_denied_scope_produces_a_clear_answer(
    agent: Agent, context: ToolContext, permissions: PermissionEngine
) -> None:
    permissions.deny(Scope.BROWSER_READ)
    run = await ask(agent, context, "search the web for python news")
    assert run.status is RunStatus.COMPLETED
    assert "denied" in run.response_text.lower() or "permission" in run.response_text.lower()
