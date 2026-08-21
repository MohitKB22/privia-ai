"""The tool execution middleware chain.

Order matters and is deliberate. From outermost to innermost:

1. **observability** - times everything, emits structured logs, records audit
   events. Wraps the whole chain so failures anywhere are still recorded.
2. **rate limit** - a runaway loop stops here, before any work happens.
3. **validation** - arguments are parsed into the tool's Pydantic model. An
   invalid call never reaches the permission engine, let alone the disk.
4. **policy** - capability check against granted scopes and the concrete
   resources the parsed arguments resolve to.
5. **confirmation** - high-impact calls stop here and return a preview.
6. **timeout** - the tool gets a hard wall-clock budget.
7. **retry** - transient failures only; permission and validation errors never
   retry.
8. **execute**.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from privia_security.limits import clamp_output
from privia_security.policy import PermissionEngine
from privia_security.redaction import redact_arguments
from privia_shared.enums import AuditAction, PermissionDecision
from privia_shared.errors import (
    ConfirmationRequiredError,
    PermissionDeniedError,
    PriviaError,
    ToolError,
    ToolTimeoutError,
)
from privia_shared.permissions import PolicyRequest
from privia_shared.tools import ToolCall, ToolResult

from .context import ToolContext
from .registry import Tool

#: A middleware wraps ``(tool, call, ctx) -> ToolResult``.
Handler = Callable[[Tool[Any], ToolCall, ToolContext], Awaitable[ToolResult]]
Middleware = Callable[[Handler], Handler]


def chain(handler: Handler, middlewares: list[Middleware]) -> Handler:
    """Compose middlewares so the first in the list is the outermost."""
    for middleware in reversed(middlewares):
        handler = middleware(handler)
    return handler


# ---------------------------------------------------------------------------
# Middlewares
# ---------------------------------------------------------------------------


def observability_middleware(logger: Any = None) -> Middleware:
    def factory(next_handler: Handler) -> Handler:
        async def handle(tool: Tool[Any], call: ToolCall, ctx: ToolContext) -> ToolResult:
            spec = tool.spec()
            started = time.perf_counter()
            safe_args = redact_arguments(call.arguments, spec.redact_input_keys)
            ctx.audit.record(
                AuditAction.TOOL_INVOKED,
                tool_name=call.tool_name,
                session_id=ctx.session_id,
                run_id=ctx.run_id,
                request_id=ctx.request_id,
                outcome="pending",
                detail={"arguments": safe_args, "risk": str(spec.risk_level)},
            )
            if logger is not None:
                logger.info(
                    "tool.start",
                    tool=call.tool_name,
                    request_id=ctx.request_id,
                    run_id=ctx.run_id,
                    arguments=safe_args,
                )
            try:
                result = await next_handler(tool, call, ctx)
            except PriviaError as exc:
                duration_ms = int((time.perf_counter() - started) * 1000)
                ctx.audit.tool_failed(
                    call.tool_name,
                    str(exc.code),
                    session_id=ctx.session_id,
                    run_id=ctx.run_id,
                    request_id=ctx.request_id,
                    detail={"message": exc.message, "duration_ms": duration_ms},
                )
                if logger is not None:
                    logger.warning(
                        "tool.error",
                        tool=call.tool_name,
                        error_code=str(exc.code),
                        duration_ms=duration_ms,
                        request_id=ctx.request_id,
                    )
                raise
            duration_ms = int((time.perf_counter() - started) * 1000)
            result = result.model_copy(
                update={
                    "duration_ms": duration_ms or result.duration_ms,
                    "call_id": result.call_id or call.id,
                    "tool_name": result.tool_name or call.tool_name,
                }
            )
            if result.success:
                ctx.audit.tool_succeeded(
                    call.tool_name,
                    duration_ms,
                    session_id=ctx.session_id,
                    run_id=ctx.run_id,
                    request_id=ctx.request_id,
                    target=result.accessed_resources[0] if result.accessed_resources else None,
                )
            else:
                ctx.audit.tool_failed(
                    call.tool_name,
                    result.error_code,
                    session_id=ctx.session_id,
                    run_id=ctx.run_id,
                    request_id=ctx.request_id,
                    detail={"message": result.error or ""},
                )
            if logger is not None:
                logger.info(
                    "tool.done",
                    tool=call.tool_name,
                    success=result.success,
                    duration_ms=duration_ms,
                    request_id=ctx.request_id,
                )
            return result

        return handle

    return factory


def rate_limit_middleware() -> Middleware:
    def factory(next_handler: Handler) -> Handler:
        async def handle(tool: Tool[Any], call: ToolCall, ctx: ToolContext) -> ToolResult:
            ctx.rate_limiter.check(f"tool:{ctx.session_id}")
            return await next_handler(tool, call, ctx)

        return handle

    return factory


def validation_middleware() -> Middleware:
    def factory(next_handler: Handler) -> Handler:
        async def handle(tool: Tool[Any], call: ToolCall, ctx: ToolContext) -> ToolResult:
            parsed = tool.parse_args(call.arguments)
            ctx.scratch[f"args:{call.id}"] = parsed
            return await next_handler(tool, call, ctx)

        return handle

    return factory


def policy_middleware(engine: PermissionEngine) -> Middleware:
    def factory(next_handler: Handler) -> Handler:
        async def handle(tool: Tool[Any], call: ToolCall, ctx: ToolContext) -> ToolResult:
            spec = tool.spec()
            args = ctx.scratch.get(f"args:{call.id}")
            resources = tool.resources(args, ctx) if args is not None else ()
            request = PolicyRequest(
                session_id=ctx.session_id,
                tool_name=call.tool_name,
                scopes=spec.scopes,
                risk_level=spec.risk_level,
                requires_confirmation=spec.requires_confirmation or call.requires_confirmation,
                resources=tuple(resources),
            )
            result = engine.evaluate(request)
            ctx.scratch[f"policy:{call.id}"] = result

            if result.decision is PermissionDecision.DENY:
                ctx.audit.permission_denied(
                    ",".join(s.value for s in spec.scopes),
                    result.reason,
                    tool_name=call.tool_name,
                    session_id=ctx.session_id,
                    run_id=ctx.run_id,
                    request_id=ctx.request_id,
                )
                raise PermissionDeniedError(
                    result.reason,
                    details={
                        "tool": call.tool_name,
                        "scopes": [s.value for s in spec.scopes],
                        "decision": "deny",
                    },
                )
            if result.decision is PermissionDecision.PROMPT:
                ctx.audit.record(
                    AuditAction.PERMISSION_REQUESTED,
                    tool_name=call.tool_name,
                    session_id=ctx.session_id,
                    run_id=ctx.run_id,
                    request_id=ctx.request_id,
                    outcome="pending",
                    detail={
                        "scopes": [s.value for s in result.missing_scopes]
                        or [s.value for s in spec.scopes],
                        "reason": result.reason,
                    },
                )
                raise PermissionDeniedError(
                    result.reason,
                    details={
                        "tool": call.tool_name,
                        "decision": "prompt",
                        "missing_scopes": [s.value for s in result.missing_scopes],
                        "out_of_scope_resources": list(result.out_of_scope_resources),
                        "resources": list(resources),
                    },
                )
            return await next_handler(tool, call, ctx)

        return handle

    return factory


def confirmation_middleware() -> Middleware:
    def factory(next_handler: Handler) -> Handler:
        async def handle(tool: Tool[Any], call: ToolCall, ctx: ToolContext) -> ToolResult:
            policy = ctx.scratch.get(f"policy:{call.id}")
            needs = bool(policy.requires_confirmation) if policy is not None else False
            needs = needs or tool.requires_confirmation or call.requires_confirmation
            if not needs:
                return await next_handler(tool, call, ctx)

            args = ctx.scratch.get(f"args:{call.id}")
            request = tool.confirmation(args, ctx) if args is not None else None
            if request is None:
                raise ToolError(
                    f"'{call.tool_name}' requires confirmation but did not describe the action.",
                    details={"tool": call.tool_name},
                )
            if request.id in ctx.approved_confirmations:
                ctx.audit.record(
                    AuditAction.CONFIRMATION_APPROVED,
                    tool_name=call.tool_name,
                    session_id=ctx.session_id,
                    run_id=ctx.run_id,
                    request_id=ctx.request_id,
                    target=request.target,
                )
                return await next_handler(tool, call, ctx)

            ctx.audit.record(
                AuditAction.CONFIRMATION_REQUESTED,
                tool_name=call.tool_name,
                session_id=ctx.session_id,
                run_id=ctx.run_id,
                request_id=ctx.request_id,
                outcome="pending",
                target=request.target,
                detail={"summary": request.summary},
            )
            raise ConfirmationRequiredError(
                request.summary,
                details={"confirmation": request.model_dump(mode="json")},
            )

        return handle

    return factory


def timeout_middleware() -> Middleware:
    def factory(next_handler: Handler) -> Handler:
        async def handle(tool: Tool[Any], call: ToolCall, ctx: ToolContext) -> ToolResult:
            timeout = tool.spec().timeout_seconds
            try:
                return await asyncio.wait_for(next_handler(tool, call, ctx), timeout=timeout)
            except asyncio.TimeoutError as exc:
                raise ToolTimeoutError(
                    f"'{call.tool_name}' did not finish within {timeout:.0f} seconds and was "
                    "stopped.",
                    details={"tool": call.tool_name, "timeout_seconds": timeout},
                ) from exc

        return handle

    return factory


#: Errors that are never worth retrying.
NON_RETRYABLE = (
    "PermissionDeniedError",
    "ConfirmationRequiredError",
    "ToolInvalidArgumentsError",
    "PathNotAllowedError",
    "PathTraversalError",
    "CommandNotAllowedError",
    "UrlNotAllowedError",
    "SsrfBlockedError",
    "PromptInjectionError",
    "ValidationError",
    "NotFoundError",
    "ConflictError",
    "RateLimitedError",
    "CloudDisabledError",
)


def retry_middleware() -> Middleware:
    def factory(next_handler: Handler) -> Handler:
        async def handle(tool: Tool[Any], call: ToolCall, ctx: ToolContext) -> ToolResult:
            policy = tool.spec().retry_policy
            delay = policy.backoff_seconds
            last_error: Exception | None = None
            for attempt in range(1, policy.max_attempts + 1):
                try:
                    return await next_handler(tool, call, ctx)
                except PriviaError as exc:
                    name = type(exc).__name__
                    if name in NON_RETRYABLE or name not in policy.retry_on:
                        raise
                    last_error = exc
                    if attempt >= policy.max_attempts:
                        raise
                    await asyncio.sleep(delay)
                    delay *= policy.backoff_multiplier
            raise last_error if last_error else ToolError("The tool failed without an error.")

        return handle

    return factory


def output_limit_middleware(max_bytes: int) -> Middleware:
    """Clamp any string payload so a huge file or page cannot blow up the UI."""

    def factory(next_handler: Handler) -> Handler:
        async def handle(tool: Tool[Any], call: ToolCall, ctx: ToolContext) -> ToolResult:
            result = await next_handler(tool, call, ctx)
            data = result.data
            if isinstance(data, str):
                clipped, truncated = clamp_output(data, max_bytes)
                if truncated:
                    return result.model_copy(update={"data": clipped, "truncated": True})
            elif isinstance(data, dict):
                changed = False
                new_data = dict(data)
                for key, value in data.items():
                    if isinstance(value, str) and len(value.encode("utf-8")) > max_bytes:
                        new_data[key], _ = clamp_output(value, max_bytes)
                        changed = True
                if changed:
                    return result.model_copy(update={"data": new_data, "truncated": True})
            return result

        return handle

    return factory
