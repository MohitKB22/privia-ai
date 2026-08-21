"""System tools: the ones that let the user interrogate and control PRIVIA itself."""

from __future__ import annotations

from datetime import timedelta

from pydantic import BaseModel, Field

from privia_shared.enums import AuditAction, RiskLevel
from privia_shared.ids import utcnow
from privia_shared.tools import ConfirmationRequest, ToolResult

from ..context import ToolContext
from ..registry import Tool


class ActivityArgs(BaseModel):
    limit: int = Field(default=25, ge=1, le=200)
    run_id: str | None = None
    minutes: int | None = Field(default=None, ge=1, le=60 * 24 * 30)


class SystemActivityTool(Tool[ActivityArgs]):
    name = "system.activity"
    family = "system"
    description = (
        "Show what PRIVIA has done recently: which files it read, which commands it ran, "
        "which pages it fetched, and every permission decision."
    )
    scopes = ()
    risk_level = RiskLevel.NONE
    Args = ActivityArgs

    async def execute(self, args: ActivityArgs, ctx: ToolContext) -> ToolResult:
        since = utcnow() - timedelta(minutes=args.minutes) if args.minutes else None
        events = ctx.repositories.audit.query(limit=args.limit, run_id=args.run_id, since=since)
        return ToolResult.ok(
            {
                "count": len(events),
                "events": [e.model_dump(mode="json") for e in events],
                "accessed_this_run": list(ctx.accessed_resources),
            }
        )


class PrivacyArgs(BaseModel):
    cloud_processing: bool | None = Field(default=None, description="Turn cloud AI on or off.")
    memory_enabled: bool | None = Field(default=None, description="Turn memory on or off.")


class SystemPrivacyTool(Tool[PrivacyArgs]):
    name = "system.privacy"
    family = "system"
    description = (
        "Read the current privacy posture, or change it. Turning cloud processing ON always "
        "requires confirmation; turning it OFF never does."
    )
    scopes = ()
    risk_level = RiskLevel.MEDIUM
    Args = PrivacyArgs

    def confirmation(self, args: PrivacyArgs, ctx: ToolContext) -> ConfirmationRequest | None:
        if args.cloud_processing is not True:
            return None
        provider = ctx.settings.cloud_llm_provider or "an external provider"
        return ConfirmationRequest(
            id=self.confirmation_id(args, ctx),
            run_id=ctx.run_id,
            tool_name=self.name,
            title="Send your data to the cloud?",
            summary=(
                f"Turning cloud processing on means your messages, and any file text you ask "
                f"about, are sent to {provider}."
            ),
            risk_level=RiskLevel.HIGH,
            details={
                "Provider": provider,
                "What is sent": "your message, conversation context, and content you ask about",
                "What is not sent": "your files themselves, credentials, or the audit log",
                "Reversible": "yes, turn it off at any time",
            },
            target="cloud_processing",
        )

    def _requires_confirmation_now(self, args: PrivacyArgs) -> bool:
        return args.cloud_processing is True

    async def execute(self, args: PrivacyArgs, ctx: ToolContext) -> ToolResult:
        changed: dict[str, object] = {}
        if args.cloud_processing is not None:
            ctx.repositories.settings.set("cloud_processing_enabled", args.cloud_processing)
            changed["cloud_processing_enabled"] = args.cloud_processing
        if args.memory_enabled is not None:
            ctx.repositories.settings.set("memory_enabled", args.memory_enabled)
            changed["memory_enabled"] = args.memory_enabled
        if changed:
            ctx.audit.record(
                AuditAction.SETTINGS_CHANGED,
                tool_name=self.name,
                session_id=ctx.session_id,
                run_id=ctx.run_id,
                request_id=ctx.request_id,
                detail=changed,
            )
        overrides = ctx.repositories.settings.all()
        return ToolResult.ok(
            {
                "changed": changed,
                "cloud_processing_enabled": bool(
                    overrides.get("cloud_processing_enabled", ctx.settings.cloud_processing_enabled)
                ),
                "memory_enabled": bool(
                    overrides.get("memory_enabled", ctx.settings.memory_enabled)
                ),
                "local_llm": f"{ctx.settings.local_llm_provider}:{ctx.settings.local_llm_model}",
                "telemetry_enabled": ctx.settings.telemetry_enabled,
                "allowed_directories": [str(p) for p in ctx.providers.path_guard.roots],
            }
        )


SYSTEM_TOOLS = [SystemActivityTool(), SystemPrivacyTool()]
