"""The deterministic tool runtime.

This is the component the whole security model rests on. The language model
produces a :class:`~privia_shared.tools.ToolCall`; nothing else. The runtime:

* looks the tool up in a fixed registry,
* validates the arguments against a schema,
* checks capabilities,
* asks for confirmation when the action has consequences,
* runs it with a timeout,
* records everything.

There is no code path from model output to execution that skips this class.
"""

from __future__ import annotations

import time
from typing import Any

from privia_security.policy import PermissionEngine
from privia_shared.errors import ConfirmationRequiredError, PriviaError, ToolError
from privia_shared.tools import ToolCall, ToolResult, ToolSpec

from .context import ToolContext
from .middleware import (
    Handler,
    Middleware,
    chain,
    confirmation_middleware,
    observability_middleware,
    output_limit_middleware,
    policy_middleware,
    rate_limit_middleware,
    retry_middleware,
    timeout_middleware,
    validation_middleware,
)
from .registry import Tool, ToolRegistry


class ToolRuntime:
    """Validates and executes tool calls."""

    def __init__(
        self,
        registry: ToolRegistry,
        permissions: PermissionEngine,
        *,
        max_output_bytes: int = 256 * 1024,
        logger: Any = None,
        extra_middleware: list[Middleware] | None = None,
    ) -> None:
        self.registry = registry
        self.permissions = permissions
        self.max_output_bytes = max_output_bytes
        self.logger = logger
        middlewares: list[Middleware] = [
            observability_middleware(logger),
            rate_limit_middleware(),
            validation_middleware(),
            policy_middleware(permissions),
            confirmation_middleware(),
            output_limit_middleware(max_output_bytes),
            timeout_middleware(),
            retry_middleware(),
        ]
        if extra_middleware:
            middlewares.extend(extra_middleware)
        self._handler: Handler = chain(_terminal_handler, middlewares)

    # -- public API -----------------------------------------------------------

    def specs(self) -> list[ToolSpec]:
        return self.registry.specs()

    def describe(self, name: str) -> ToolSpec:
        return self.registry.get(name).spec()

    async def execute(self, call: ToolCall, ctx: ToolContext) -> ToolResult:
        """Execute one call. Never raises; failures come back as a ``ToolResult``.

        The single exception is :class:`ConfirmationRequiredError`, which the
        agent must see so it can pause the run and ask the user.
        """
        started = time.perf_counter()
        try:
            tool = self.registry.get(call.tool_name)
        except PriviaError as exc:
            return _failure(call, exc, started)

        try:
            result = await self._handler(tool, call, ctx)
        except ConfirmationRequiredError:
            raise
        except PriviaError as exc:
            return _failure(call, exc, started)
        except Exception as exc:
            wrapped = ToolError(
                f"'{call.tool_name}' failed unexpectedly ({type(exc).__name__}).",
                details={"tool": call.tool_name},
            )
            if self.logger is not None:
                self.logger.error(
                    "tool.unhandled",
                    tool=call.tool_name,
                    error=type(exc).__name__,
                    request_id=ctx.request_id,
                )
            return _failure(call, wrapped, started)

        for resource in result.accessed_resources:
            ctx.note_resource(resource)
        return result

    async def execute_all(self, calls: list[ToolCall], ctx: ToolContext) -> list[ToolResult]:
        """Run calls in order, stopping at the first failure.

        Sequential by design: step two of a plan usually depends on step one, and
        parallel side effects are much harder for a person to review.
        """
        results: list[ToolResult] = []
        for call in calls:
            result = await self.execute(call, ctx)
            results.append(result)
            if not result.success:
                break
        return results

    def preview(self, call: ToolCall, ctx: ToolContext) -> dict[str, Any] | None:
        """Build the confirmation preview without executing anything."""
        tool = self.registry.get(call.tool_name)
        args = tool.parse_args(call.arguments)
        request = tool.confirmation(args, ctx)
        return request.model_dump(mode="json") if request else None


async def _terminal_handler(tool: Tool[Any], call: ToolCall, ctx: ToolContext) -> ToolResult:
    """Innermost handler: actually run the tool."""
    args = ctx.scratch.get(f"args:{call.id}")
    if args is None:  # pragma: no cover - validation middleware always sets this
        args = tool.parse_args(call.arguments)
    result = await tool.execute(args, ctx)
    return result.model_copy(update={"call_id": call.id, "tool_name": call.tool_name})


def _failure(call: ToolCall, exc: PriviaError, started: float) -> ToolResult:
    return ToolResult(
        call_id=call.id,
        tool_name=call.tool_name,
        success=False,
        error=exc.message,
        error_code=str(exc.code),
        duration_ms=int((time.perf_counter() - started) * 1000),
        metadata={"details": exc.details} if exc.details else {},
    )
