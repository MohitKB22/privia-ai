"""Terminal tools.

``terminal.inspect`` is deliberately separate from ``terminal.run`` so the model
(and the user) can find out whether something is permitted without running it.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from privia_shared.enums import AuditAction, RiskLevel, Scope
from privia_shared.errors import CommandNotAllowedError
from privia_shared.tools import ConfirmationRequest, RetryPolicy, ToolResult

from ..context import ToolContext
from ..registry import Tool


class InspectArgs(BaseModel):
    command: str = Field(min_length=1, max_length=2000)


class TerminalInspectTool(Tool[InspectArgs]):
    name = "terminal.inspect"
    family = "terminal"
    description = (
        "Explain whether a command is allowed, whether it needs confirmation, and why. "
        "Runs nothing."
    )
    scopes = ()
    risk_level = RiskLevel.NONE
    Args = InspectArgs

    async def execute(self, args: InspectArgs, ctx: ToolContext) -> ToolResult:
        guard = ctx.providers.command_guard
        try:
            decision = guard.inspect(args.command)
        except CommandNotAllowedError as exc:
            return ToolResult.ok(
                {
                    "command": args.command,
                    "allowed": False,
                    "requires_confirmation": False,
                    "reason": exc.message,
                    "argv": [],
                }
            )
        return ToolResult.ok(decision.to_inspection(args.command).model_dump(mode="json"))


class RunArgs(BaseModel):
    command: str = Field(min_length=1, max_length=2000, description="The command to run.")
    cwd: str = Field(description="Absolute path of an allowed workspace folder.", max_length=4096)
    timeout_seconds: float | None = Field(default=None, gt=0, le=300)


class TerminalRunTool(Tool[RunArgs]):
    name = "terminal.run"
    family = "terminal"
    description = (
        "Run an allowlisted command inside one of your workspace folders. Commands are "
        "parsed into arguments and executed without a shell; output and runtime are capped."
    )
    scopes = (Scope.TERMINAL_EXEC,)
    risk_level = RiskLevel.HIGH
    requires_confirmation = True
    retry_policy = RetryPolicy(max_attempts=1)
    timeout_seconds = 120.0
    returns_untrusted_content = True
    Args = RunArgs

    def resources(self, args: RunArgs, ctx: ToolContext) -> tuple[str, ...]:
        try:
            decision = ctx.providers.command_guard.inspect(args.command)
        except CommandNotAllowedError:
            return (args.cwd,)
        return (decision.program, args.cwd)

    def confirmation(self, args: RunArgs, ctx: ToolContext) -> ConfirmationRequest:
        # Validate everything *before* building the dialog. Asking the user to
        # approve a command that would then be refused trains them to click
        # through prompts, which is exactly what a confirmation gate must not do.
        guard = ctx.providers.command_guard
        decision = guard.inspect(args.command)
        if not decision.allowed:
            raise CommandNotAllowedError(
                decision.reason, details={"command": args.command, "program": decision.program}
            )
        guard.validate_cwd(args.cwd)
        return ConfirmationRequest(
            id=self.confirmation_id(args, ctx),
            run_id=ctx.run_id,
            tool_name=self.name,
            title="Run this command?",
            summary=f"Run `{' '.join(decision.argv)}` in {args.cwd}.",
            risk_level=RiskLevel.HIGH if decision.requires_confirmation else RiskLevel.MEDIUM,
            details={
                "Program": decision.program,
                "Arguments": " ".join(decision.argv[1:]) or "none",
                "Working directory": args.cwd,
                "Timeout": f"{args.timeout_seconds or ctx.settings.command_timeout_seconds:.0f}s",
                "Changes state": "yes" if decision.requires_confirmation else "no",
            },
            target=" ".join(decision.argv),
            destructive=decision.requires_confirmation,
        )

    async def execute(self, args: RunArgs, ctx: ToolContext) -> ToolResult:
        guard = ctx.providers.command_guard
        decision = guard.inspect(args.command)
        argv = decision.raise_for_status()
        result = await ctx.providers.terminal.run(
            argv, Path(args.cwd), timeout_seconds=args.timeout_seconds
        )
        ctx.audit.record(
            AuditAction.COMMAND_EXECUTED,
            tool_name=self.name,
            target=" ".join(argv),
            session_id=ctx.session_id,
            run_id=ctx.run_id,
            request_id=ctx.request_id,
            outcome="success" if result.exit_code == 0 else "failure",
            detail={
                "exit_code": result.exit_code,
                "cwd": result.cwd,
                "duration_ms": result.duration_ms,
            },
        )
        return ToolResult.ok(
            result.model_dump(mode="json"),
            accessed_resources=(f"command:{decision.program}", result.cwd),
            truncated=result.truncated,
            metadata={"untrusted": True, "exit_code": result.exit_code},
        )


class ListAllowedArgs(BaseModel):
    pass


class TerminalListAllowedTool(Tool[ListAllowedArgs]):
    name = "terminal.list_allowed"
    family = "terminal"
    description = "List the commands PRIVIA is permitted to run and the folders it may run them in."
    scopes = ()
    risk_level = RiskLevel.NONE
    Args = ListAllowedArgs

    async def execute(self, args: ListAllowedArgs, ctx: ToolContext) -> ToolResult:
        guard = ctx.providers.command_guard
        return ToolResult.ok(
            {
                "workspace_roots": [str(r) for r in guard.workspace_roots],
                "allowed": [
                    {
                        "program": rule.program,
                        "description": rule.description,
                        "always_confirms": rule.always_confirm,
                        "subcommands": sorted(rule.subcommands) or None,
                    }
                    for rule in sorted(guard.allowlist.values(), key=lambda r: r.program)
                ],
                "blocked_count": len(guard.denied),
            }
        )


TERMINAL_TOOLS = [TerminalInspectTool(), TerminalRunTool(), TerminalListAllowedTool()]
