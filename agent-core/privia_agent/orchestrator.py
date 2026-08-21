"""The agent state graph.

    INPUT -> CLASSIFY -> PLAN -> POLICY_CHECK -> TOOL_SELECTION
          -> EXECUTION -> VERIFY -> RESPOND

Every phase is timed and recorded on the :class:`~privia_shared.agent.AgentRun`,
which is persisted whole. That record is what the Activity screen renders and
what makes "what did it just do, and why?" answerable after the fact.

The graph is deterministic in structure: the model influences *which* tools are
proposed, never whether the permission check runs.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Sequence
from contextlib import suppress
from typing import Any

from privia_llm.base import ChatMessage, GenerationOptions, LLMProvider
from privia_llm.providers.heuristic import compose_response
from privia_llm.router import LLMRouter
from privia_llm.rules import build_plan as rule_plan
from privia_llm.rules import classify as rule_classify
from privia_memory.service import MemoryService
from privia_security.injection import scan_user_input, wrap_untrusted
from privia_shared.agent import (
    AgentRun,
    Classification,
    Entity,
    PhaseRecord,
    Plan,
    PlanStep,
)
from privia_shared.enums import (
    AgentPhase,
    AuditAction,
    Intent,
    MessageRole,
    ProcessingLocation,
    RunStatus,
)
from privia_shared.errors import (
    ConfirmationRequiredError,
    LLMInvalidOutputError,
    LLMUnavailableError,
    PriviaError,
)
from privia_shared.ids import utcnow
from privia_shared.tools import ConfirmationRequest, ToolCall, ToolResult
from privia_storage.repositories import Repositories
from privia_tools.context import ToolContext
from privia_tools.runtime import ToolRuntime

from .prompts import (
    CLASSIFY_SCHEMA,
    PLAN_SCHEMA,
    classify_prompt,
    history_messages,
    memory_block,
    plan_prompt,
    respond_prompt,
)
from .references import ReferenceError, has_reference, resolve_arguments
from .verification import verify

MAX_PLAN_STEPS = 6
MAX_INPUT_CHARS = 16_000


class Agent:
    """Runs one request end to end."""

    def __init__(
        self,
        router: LLMRouter,
        runtime: ToolRuntime,
        repositories: Repositories,
        memory: MemoryService,
        *,
        logger: Any = None,
    ) -> None:
        self.router = router
        self.runtime = runtime
        self.repositories = repositories
        self.memory = memory
        self.logger = logger

    # -----------------------------------------------------------------------
    # Public entry point
    # -----------------------------------------------------------------------

    async def run(
        self,
        text: str,
        ctx: ToolContext,
        *,
        request_id: str,
        prefer: ProcessingLocation | None = None,
    ) -> AgentRun:
        run = AgentRun(
            request_id=request_id,
            session_id=ctx.session_id,
            input_text=text[:MAX_INPUT_CHARS],
            status=RunStatus.RUNNING,
        )
        ctx.run_id = run.id
        started = time.perf_counter()
        phases: list[PhaseRecord] = []

        ctx.audit.record(
            AuditAction.RUN_STARTED,
            session_id=ctx.session_id,
            run_id=run.id,
            request_id=request_id,
            outcome="pending",
            detail={"characters": len(text)},
        )

        try:
            # -- INPUT ------------------------------------------------------
            with PhaseTimer(phases, AgentPhase.INPUT) as record:
                cleaned = self._normalise(text)
                injection = scan_user_input(cleaned)
                if injection.flags:
                    record.detail = f"input flags: {', '.join(injection.flags)}"
                cleaned = injection.sanitized_text or cleaned
                context = await self.memory.build_context(ctx.session_id, cleaned)
                context["workspace_root"] = _first_root(ctx)
                context["last_draft_id"] = self._last_draft_id(ctx)

            # The run row must exist before any tool call is recorded: tool_calls
            # carries a foreign key into runs. Saving a skeleton here also means a
            # crash mid-run still leaves an inspectable record.
            self.repositories.runs.save(run)

            route = await self.router.route(session_id=ctx.session_id, prefer=prefer)
            provider = route.provider
            run = run.model_copy(
                update={
                    "processing_location": route.location,
                    "model_used": f"{provider.name}:{provider.model}",
                }
            )
            ctx.processing_location = route.location
            if route.degraded:
                context["no_model"] = True

            # -- CLASSIFY ---------------------------------------------------
            with PhaseTimer(phases, AgentPhase.CLASSIFY) as record:
                classification = await self._classify(provider, cleaned, context)
                record.detail = f"{classification.intent} ({classification.confidence})"
            run = run.model_copy(
                update={"classification": classification, "phase": AgentPhase.PLAN}
            )

            # -- PLAN -------------------------------------------------------
            with PhaseTimer(phases, AgentPhase.PLAN) as record:
                plan = await self._plan(provider, cleaned, classification, context)
                record.detail = f"{len(plan.steps)} step(s)"
            run = run.model_copy(update={"plan": plan, "phase": AgentPhase.TOOL_SELECTION})

            # -- TOOL_SELECTION ---------------------------------------------
            with PhaseTimer(phases, AgentPhase.TOOL_SELECTION) as record:
                calls = self._select_tools(plan)
                record.detail = ", ".join(c.tool_name for c in calls) or "no tools"
            run = run.model_copy(
                update={
                    "selected_tools": tuple(c.tool_name for c in calls),
                    "phase": AgentPhase.POLICY_CHECK,
                }
            )

            # -- POLICY_CHECK + EXECUTION -----------------------------------
            results: list[ToolResult] = []
            pending: ConfirmationRequest | None = None
            executed_calls: list[ToolCall] = []
            if calls:
                with PhaseTimer(phases, AgentPhase.EXECUTION) as record:
                    results, executed_calls, pending = await self._execute(calls, ctx)
                    record.detail = f"{len(results)} result(s)"
                    record.ok = all(r.success for r in results)

            run = run.model_copy(
                update={
                    "tool_calls": tuple(executed_calls),
                    "tool_results": tuple(results),
                    "policy_results": tuple(
                        v for k, v in ctx.scratch.items() if k.startswith("policy:")
                    ),
                    "accessed_resources": tuple(ctx.accessed_resources),
                }
            )

            if pending is not None:
                self.repositories.confirmations.create(pending, ctx.session_id)
                summary = f"{pending.summary}\n\nApprove it and I will continue."
                run = run.model_copy(
                    update={
                        "status": RunStatus.AWAITING_CONFIRMATION,
                        "phase": AgentPhase.AWAITING_CONFIRMATION,
                        "pending_confirmation": pending,
                        "response_text": summary,
                        "phases": tuple(phases),
                        "duration_ms": int((time.perf_counter() - started) * 1000),
                    }
                )
                self._persist(run, ctx)
                return run

            # -- RESPOND ----------------------------------------------------
            with PhaseTimer(phases, AgentPhase.RESPOND):
                response = await self._respond(
                    provider, cleaned, classification, results, context, route.degraded
                )

            # -- VERIFY -----------------------------------------------------
            with PhaseTimer(phases, AgentPhase.VERIFY) as record:
                verification = verify(
                    plan,
                    executed_calls,
                    results,
                    approved_confirmations=ctx.approved_confirmations,
                    response_text=response,
                )
                record.ok = verification.passed
                record.detail = "; ".join(
                    f"{c.name}={'ok' if c.passed else 'FAIL'}" for c in verification.checks
                )

            if not verification.passed:
                response = self._append_verification_notice(response, verification)

            run = run.model_copy(
                update={
                    "verification": verification,
                    "response_text": response,
                    "status": RunStatus.COMPLETED,
                    "phase": AgentPhase.RESPOND,
                    "phases": tuple(phases),
                    "duration_ms": int((time.perf_counter() - started) * 1000),
                    "accessed_resources": tuple(ctx.accessed_resources),
                }
            )
            ctx.audit.record(
                AuditAction.RUN_COMPLETED,
                session_id=ctx.session_id,
                run_id=run.id,
                request_id=request_id,
                detail={
                    "intent": str(classification.intent),
                    "tools": list(run.selected_tools),
                    "duration_ms": run.duration_ms,
                    "verified": verification.passed,
                },
            )
            self._persist(run, ctx)
            return run

        except PriviaError as exc:
            return self._fail(run, ctx, phases, started, exc.message, str(exc.code))
        except Exception as exc:
            if self.logger is not None:
                self.logger.error(
                    "agent.unhandled",
                    error=type(exc).__name__,
                    detail=str(exc)[:300],
                    request_id=request_id,
                )
            ctx.audit.record(
                AuditAction.RUN_FAILED,
                session_id=ctx.session_id,
                run_id=run.id,
                request_id=request_id,
                outcome="failure",
                detail={"exception": type(exc).__name__, "message": str(exc)[:500]},
            )
            return self._fail(
                run,
                ctx,
                phases,
                started,
                "Something went wrong while handling that. Nothing was changed.",
                "INTERNAL_ERROR",
            )

    # -----------------------------------------------------------------------
    # Phases
    # -----------------------------------------------------------------------

    @staticmethod
    def _normalise(text: str) -> str:
        collapsed = " ".join((text or "").split())
        return collapsed[:MAX_INPUT_CHARS]

    async def _classify(
        self, provider: LLMProvider, text: str, context: dict[str, Any]
    ) -> Classification:
        """Model classification with a rule-engine floor.

        The rules always run. When a model is available its answer is preferred
        for the intent label, but the entities the rules extracted are merged in,
        because deterministic date and path parsing beats model guessing.
        """
        baseline = rule_classify(text)
        try:
            payload = await provider.structured_output(
                classify_prompt(text, {"memory_enabled": context.get("memory_enabled", True)}),
                CLASSIFY_SCHEMA,
                GenerationOptions(temperature=0.0, max_tokens=400),
            )
        except (LLMUnavailableError, LLMInvalidOutputError):
            return baseline

        try:
            intent = Intent(str(payload.get("intent", baseline.intent)))
        except ValueError:
            intent = baseline.intent
        confidence = float(payload.get("confidence", baseline.confidence) or 0.0)
        model_entities = tuple(
            Entity(
                type=str(e.get("type", "unknown"))[:40],
                value=str(e.get("value", ""))[:400],
                normalized=str(e.get("normalized") or e.get("value") or "")[:400],
                confidence=0.7,
            )
            for e in payload.get("entities", [])[:20]
            if isinstance(e, dict) and e.get("value")
        )
        merged = list(baseline.entities)
        known = {(e.type, (e.normalized or e.value).lower()) for e in merged}
        for entity in model_entities:
            key = (entity.type, (entity.normalized or entity.value).lower())
            if key not in known:
                merged.append(entity)
        return Classification(
            intent=intent,
            confidence=round(min(max(confidence, 0.0), 0.99), 2),
            entities=tuple(merged),
            rationale=str(payload.get("rationale", baseline.rationale))[:400],
            alternatives=baseline.alternatives,
        )

    async def _plan(
        self,
        provider: LLMProvider,
        text: str,
        classification: Classification,
        context: dict[str, Any],
    ) -> Plan:
        baseline = rule_plan(text, classification, context)
        specs = self.runtime.specs()
        plan_context = {
            "intent": str(classification.intent),
            "entities": [
                {"type": e.type, "value": e.normalized or e.value} for e in classification.entities
            ],
            "workspace_root": context.get("workspace_root", ""),
            "last_draft_id": context.get("last_draft_id"),
            "allowed_directories": context.get("allowed_directories", []),
        }
        try:
            payload = await provider.structured_output(
                plan_prompt(text, specs, plan_context),
                PLAN_SCHEMA,
                GenerationOptions(temperature=0.0, max_tokens=900),
            )
        except (LLMUnavailableError, LLMInvalidOutputError):
            return baseline

        steps: list[PlanStep] = []
        for index, raw in enumerate(payload.get("steps", [])[:MAX_PLAN_STEPS]):
            if not isinstance(raw, dict):
                continue
            tool_name = raw.get("tool_name") or None
            if tool_name and not self.runtime.registry.has(str(tool_name)):
                # The model hallucinated a tool. Drop the step rather than
                # failing the whole run; the rule plan is still available.
                continue
            arguments = raw.get("arguments")
            steps.append(
                PlanStep(
                    index=index,
                    description=str(raw.get("description", ""))[:300],
                    tool_name=str(tool_name) if tool_name else None,
                    arguments=arguments if isinstance(arguments, dict) else {},
                    depends_on=(
                        tuple(range(index))
                        if isinstance(arguments, dict)
                        and any(has_reference(v) for v in arguments.values())
                        else ()
                    ),
                )
            )
        if not steps and not payload.get("direct_answer"):
            return baseline
        return Plan(
            steps=tuple(steps),
            summary=str(payload.get("summary", baseline.summary))[:300],
            direct_answer=(
                str(payload["direct_answer"])[:4000] if payload.get("direct_answer") else None
            ),
        )

    def _select_tools(self, plan: Plan) -> list[ToolCall]:
        calls: list[ToolCall] = []
        for step in plan.steps[:MAX_PLAN_STEPS]:
            if not step.tool_name or not self.runtime.registry.has(step.tool_name):
                continue
            spec = self.runtime.describe(step.tool_name)
            calls.append(
                ToolCall(
                    tool_name=step.tool_name,
                    arguments=dict(step.arguments),
                    risk_level=spec.risk_level,
                    justification=step.description[:400],
                    requires_confirmation=spec.requires_confirmation,
                )
            )
        return calls

    async def _execute(
        self, calls: Sequence[ToolCall], ctx: ToolContext
    ) -> tuple[list[ToolResult], list[ToolCall], ConfirmationRequest | None]:
        results: list[ToolResult] = []
        executed: list[ToolCall] = []
        for call in calls:
            try:
                arguments = resolve_arguments(call.arguments, results)
            except ReferenceError as exc:
                results.append(
                    ToolResult.fail(
                        f"I could not continue to '{call.tool_name}' because {exc}.",
                        error_code="STEP_REFERENCE_UNRESOLVED",
                        call_id=call.id,
                        tool_name=call.tool_name,
                    )
                )
                break
            resolved = call.model_copy(update={"arguments": arguments})
            executed.append(resolved)
            self.repositories.tool_calls.record_call(ctx.run_id, ctx.session_id, resolved)
            try:
                result = await self.runtime.execute(resolved, ctx)
            except ConfirmationRequiredError as exc:
                confirmation = exc.details.get("confirmation")
                if confirmation:
                    return results, executed, ConfirmationRequest.model_validate(confirmation)
                raise
            results.append(result)
            self.repositories.tool_calls.record_result(ctx.run_id, result)
            if not result.success:
                break
        return results, executed, None

    async def _respond(
        self,
        provider: LLMProvider,
        text: str,
        classification: Classification,
        results: Sequence[ToolResult],
        context: dict[str, Any],
        degraded: bool,
    ) -> str:
        payload = {
            "intent": str(classification.intent),
            "no_model": degraded,
            "tool_results": [
                {
                    "tool_name": r.tool_name,
                    "success": r.success,
                    "error": r.error,
                    "data": _trim_for_prompt(r.data),
                }
                for r in results
            ],
        }
        if degraded:
            return compose_response(text, payload)

        untrusted: list[str] = []
        for result in results:
            if not result.success or not isinstance(result.data, dict):
                continue
            block = result.data.get("quarantined_text")
            if isinstance(block, str):
                untrusted.append(block)
            elif result.metadata.get("untrusted") and isinstance(result.data.get("text"), str):
                untrusted.append(wrap_untrusted(result.data["text"], source=result.tool_name))

        messages = respond_prompt(text, payload, untrusted_blocks=untrusted)
        memories = memory_block(context.get("memories", []))
        if memories:
            messages.insert(1, ChatMessage("system", memories))
        for message in reversed(history_messages(context.get("history", []))[:-1]):
            messages.insert(1, message)
        try:
            generated = await provider.generate(
                messages, GenerationOptions(temperature=0.3, max_tokens=1200)
            )
        except (LLMUnavailableError, LLMInvalidOutputError):
            return compose_response(text, payload)
        answer = generated.text.strip()
        return answer or compose_response(text, payload)

    async def stream_response(
        self,
        text: str,
        ctx: ToolContext,
        *,
        request_id: str,
        prefer: ProcessingLocation | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Run the graph, emitting progress events then the final answer.

        Tool execution is not streamed token by token: the user needs to see
        *which* tool is running and be able to stop it, which is a discrete
        event, not a token stream.
        """
        yield {"type": "status", "phase": "classify", "message": "Understanding the request"}
        run = await self.run(text, ctx, request_id=request_id, prefer=prefer)
        for call in run.tool_calls:
            yield {"type": "tool", "tool": call.tool_name, "justification": call.justification}
        if run.pending_confirmation is not None:
            yield {
                "type": "confirmation",
                "confirmation": run.pending_confirmation.model_dump(mode="json"),
            }
        for chunk in _chunk_text(run.response_text, 64):
            yield {"type": "token", "text": chunk}
        yield {
            "type": "done",
            "run_id": run.id,
            "status": str(run.status),
            "accessed_resources": list(run.accessed_resources),
            "processing_location": str(run.processing_location),
            "model_used": run.model_used,
            "duration_ms": run.duration_ms,
        }

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _persist(self, run: AgentRun, ctx: ToolContext) -> None:
        """Write the final run record plus the two conversation messages."""
        self.repositories.runs.save(run)
        self.repositories.messages.add(
            ctx.session_id, MessageRole.USER, run.input_text, run_id=run.id
        )
        self.repositories.messages.add(
            ctx.session_id,
            MessageRole.ASSISTANT,
            run.response_text,
            run_id=run.id,
            metadata={
                "intent": str(run.classification.intent),
                "tools": list(run.selected_tools),
                "processing_location": str(run.processing_location),
            },
        )
        self.repositories.sessions.touch(ctx.session_id)
        if self.repositories.messages.count(ctx.session_id) <= 2:
            self.repositories.sessions.rename(ctx.session_id, run.input_text[:60])

    def _fail(
        self,
        run: AgentRun,
        ctx: ToolContext,
        phases: list[PhaseRecord],
        started: float,
        message: str,
        code: str,
    ) -> AgentRun:
        failed = run.model_copy(
            update={
                "status": RunStatus.FAILED,
                "phase": AgentPhase.FAILED,
                "error": message,
                "error_code": code,
                "response_text": message,
                "phases": tuple(phases),
                "duration_ms": int((time.perf_counter() - started) * 1000),
                "accessed_resources": tuple(ctx.accessed_resources),
            }
        )
        ctx.audit.record(
            AuditAction.RUN_FAILED,
            session_id=ctx.session_id,
            run_id=failed.id,
            request_id=failed.request_id,
            outcome="failure",
            detail={"error_code": code, "message": message},
        )
        # Persisting the failure is best effort: if the database is the thing
        # that broke, the user still needs the original error, not a second one.
        with suppress(Exception):
            self._persist(failed, ctx)
        return failed

    def _last_draft_id(self, ctx: ToolContext) -> str | None:
        drafts = self.repositories.drafts.list("draft", 1)
        return drafts[0].id if drafts else None

    @staticmethod
    def _append_verification_notice(response: str, verification: Any) -> str:
        failures = [c for c in verification.checks if not c.passed]
        if not failures:
            return response
        notes = "\n".join(f"- {c.detail}" for c in failures)
        return f"{response}\n\nNote on this run:\n{notes}"


def _first_root(ctx: ToolContext) -> str:
    roots = ctx.providers.path_guard.roots
    return str(roots[0]) if roots else ""


def _trim_for_prompt(data: Any, limit: int = 4000) -> Any:
    if isinstance(data, str):
        return data[:limit]
    if isinstance(data, dict):
        return {k: _trim_for_prompt(v, limit) for k, v in list(data.items())[:40]}
    if isinstance(data, list):
        return [_trim_for_prompt(v, limit) for v in data[:20]]
    return data


def _chunk_text(text: str, size: int) -> list[str]:
    if not text:
        return []
    return [text[i : i + size] for i in range(0, len(text), size)]


class PhaseTimer:
    """Context manager that appends a :class:`PhaseRecord` with timing."""

    def __init__(self, sink: list[PhaseRecord], phase: AgentPhase) -> None:
        self.sink = sink
        self.phase = phase
        self.detail = ""
        self.ok = True
        self._started = 0.0
        self._record: PhaseRecord | None = None

    def __enter__(self) -> PhaseTimer:
        self._started = time.perf_counter()
        return self

    def __exit__(self, exc_type: type[BaseException] | None, *_: object) -> None:
        self.sink.append(
            PhaseRecord(
                phase=self.phase,
                started_at=utcnow(),
                duration_ms=int((time.perf_counter() - self._started) * 1000),
                ok=self.ok and exc_type is None,
                detail=self.detail,
            )
        )
