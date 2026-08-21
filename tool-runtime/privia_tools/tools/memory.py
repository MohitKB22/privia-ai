"""Memory tools.

Memory is opt-in, inspectable and deletable. PRIVIA remembers a fact only when
the user asks it to, or when it asks and the user says yes. Forgetting shows the
exact scope before it happens.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from privia_shared.enums import AuditAction, MemoryKind, RiskLevel, Scope
from privia_shared.tools import ConfirmationRequest, RetryPolicy, ToolResult

from ..context import ToolContext
from ..registry import Tool

#: Content that must never be stored as a memory, whatever the user says.
_REFUSED_PATTERNS = (
    "password",
    "passphrase",
    "api key",
    "api_key",
    "secret key",
    "private key",
    "credit card",
    "card number",
    "cvv",
    "social security",
    "ssn",
    "passport number",
    "bank account",
    "routing number",
    "pin code",
)


class RecallArgs(BaseModel):
    query: str = Field(default="", max_length=300)
    limit: int = Field(default=10, ge=1, le=50)


class MemoryRecallTool(Tool[RecallArgs]):
    name = "memory.recall"
    family = "memory"
    description = "Look up what PRIVIA remembers, optionally filtered by a query."
    scopes = (Scope.MEMORY_READ,)
    risk_level = RiskLevel.NONE
    Args = RecallArgs

    async def execute(self, args: RecallArgs, ctx: ToolContext) -> ToolResult:
        if not ctx.settings.memory_enabled:
            return ToolResult.ok(
                {"count": 0, "memories": [], "note": "Memory is switched off in Settings."}
            )
        service = ctx.scratch.get("memory_service")
        if service is not None and args.query:
            records = await service.search(args.query, limit=args.limit)
        elif args.query:
            records = ctx.repositories.memories.search_text(args.query, args.limit)
        else:
            records = ctx.repositories.memories.list(limit=args.limit)
        for record in records:
            ctx.repositories.memories.mark_used(record.id)
        return ToolResult.ok(
            {
                "count": len(records),
                "memories": [r.model_dump(mode="json") for r in records],
            }
        )


class RememberArgs(BaseModel):
    content: str = Field(min_length=2, max_length=2000)
    kind: MemoryKind = MemoryKind.FACT
    tags: list[str] = Field(default_factory=list, max_length=10)
    pinned: bool = False


class MemoryRememberTool(Tool[RememberArgs]):
    name = "memory.remember"
    family = "memory"
    description = "Store something PRIVIA should remember. Refuses credentials and other secrets."
    scopes = (Scope.MEMORY_WRITE,)
    risk_level = RiskLevel.LOW
    Args = RememberArgs

    async def execute(self, args: RememberArgs, ctx: ToolContext) -> ToolResult:
        if not ctx.settings.memory_enabled:
            return ToolResult.fail(
                "Memory is switched off in Settings, so nothing was stored.",
                error_code="MEMORY_DISABLED",
            )
        lowered = args.content.lower()
        for pattern in _REFUSED_PATTERNS:
            if pattern in lowered:
                return ToolResult.fail(
                    "PRIVIA does not store credentials or financial identifiers in memory. "
                    "Use your password manager for that.",
                    error_code="MEMORY_REFUSED_SECRET",
                )
        from privia_security.redaction import contains_secret

        if contains_secret(args.content):
            return ToolResult.fail(
                "That text looks like a live credential, so it was not stored.",
                error_code="MEMORY_REFUSED_SECRET",
            )
        service = ctx.scratch.get("memory_service")
        if service is not None:
            record = await service.remember(
                args.content,
                kind=args.kind,
                tags=args.tags,
                session_id=ctx.session_id,
                provenance=f"run:{ctx.run_id}" if ctx.run_id else "user:explicit",
                pinned=args.pinned,
            )
        else:
            record = ctx.repositories.memories.add(
                args.kind,
                args.content,
                tags=args.tags,
                session_id=ctx.session_id,
                pinned=args.pinned,
            )
        ctx.audit.record(
            AuditAction.MEMORY_WRITTEN,
            tool_name=self.name,
            target=record.id,
            session_id=ctx.session_id,
            run_id=ctx.run_id,
            request_id=ctx.request_id,
            detail={"kind": str(record.kind), "characters": len(record.content)},
        )
        return ToolResult.ok(
            record.model_dump(mode="json"), accessed_resources=(f"memory:{record.id}",)
        )


class ForgetArgs(BaseModel):
    memory_id: str | None = Field(default=None, description="Delete one memory by id.")
    all_memories: bool = Field(default=False, description="Delete everything PRIVIA remembers.")
    keep_pinned: bool = Field(default=False, description="Keep pinned memories when clearing all.")


class MemoryForgetTool(Tool[ForgetArgs]):
    name = "memory.forget"
    family = "memory"
    description = (
        "Delete one memory or everything PRIVIA remembers. Always shows the exact scope "
        "and requires confirmation."
    )
    scopes = (Scope.MEMORY_WRITE,)
    risk_level = RiskLevel.HIGH
    requires_confirmation = True
    retry_policy = RetryPolicy(max_attempts=1)
    Args = ForgetArgs

    def confirmation(self, args: ForgetArgs, ctx: ToolContext) -> ConfirmationRequest:
        if args.all_memories:
            total = ctx.repositories.memories.count()
            pinned = len([m for m in ctx.repositories.memories.list(limit=10_000) if m.pinned])
            affected = total - pinned if args.keep_pinned else total
            return ConfirmationRequest(
                id=self.confirmation_id(args, ctx),
                run_id=ctx.run_id,
                tool_name=self.name,
                title="Delete everything PRIVIA remembers?",
                summary=f"This deletes {affected} of {total} stored memories. It cannot be undone.",
                risk_level=RiskLevel.HIGH,
                details={
                    "Memories stored": str(total),
                    "Will be deleted": str(affected),
                    "Pinned kept": "yes" if args.keep_pinned else "no",
                    "Also deleted": "the semantic index built from them",
                },
                target="all-memories",
                destructive=True,
            )
        record = ctx.repositories.memories.get(args.memory_id or "")
        preview = record.content[:160] if record else "(not found)"
        return ConfirmationRequest(
            id=self.confirmation_id(args, ctx),
            run_id=ctx.run_id,
            tool_name=self.name,
            title="Forget this?",
            summary=f"Delete the memory: {preview}",
            risk_level=RiskLevel.MEDIUM,
            details={"Memory": preview, "Id": args.memory_id or ""},
            target=args.memory_id,
            destructive=True,
        )

    async def execute(self, args: ForgetArgs, ctx: ToolContext) -> ToolResult:
        if args.all_memories:
            deleted = ctx.repositories.memories.delete_all(keep_pinned=args.keep_pinned)
            ctx.audit.record(
                AuditAction.MEMORY_DELETED,
                tool_name=self.name,
                target="all",
                session_id=ctx.session_id,
                run_id=ctx.run_id,
                request_id=ctx.request_id,
                detail={"deleted": deleted, "kept_pinned": args.keep_pinned},
            )
            return ToolResult.ok({"deleted": deleted, "scope": "all"})
        if not args.memory_id:
            return ToolResult.fail(
                "Say which memory to forget, or ask to forget everything.",
                error_code="VALIDATION_ERROR",
            )
        removed = ctx.repositories.memories.delete(args.memory_id)
        ctx.audit.record(
            AuditAction.MEMORY_DELETED,
            tool_name=self.name,
            target=args.memory_id,
            session_id=ctx.session_id,
            run_id=ctx.run_id,
            request_id=ctx.request_id,
            outcome="success" if removed else "failure",
        )
        return ToolResult.ok({"deleted": 1 if removed else 0, "memory_id": args.memory_id})


MEMORY_TOOLS = [MemoryRecallTool(), MemoryRememberTool(), MemoryForgetTool()]
