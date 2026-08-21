"""Email tools.

The flow is fixed and cannot be short-circuited:

    draft -> validate recipients -> preview -> explicit confirmation -> send
          -> verify -> audit

``email.send`` takes a *draft id*, never a body. That means the exact bytes the
user approved in the preview are the bytes that go out; there is no window in
which the model can substitute different content after approval.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from privia_integrations.email.base import parse_addresses, validate_body, validate_subject
from privia_shared.enums import AuditAction, RiskLevel, Scope
from privia_shared.errors import ConflictError, ValidationError
from privia_shared.tools import ConfirmationRequest, RetryPolicy, ToolResult

from ..context import ToolContext
from ..registry import Tool


class EmailSearchArgs(BaseModel):
    query: str = Field(default="", max_length=200)
    folder: str = Field(default="INBOX", max_length=64)
    limit: int = Field(default=20, ge=1, le=100)


class EmailSearchTool(Tool[EmailSearchArgs]):
    name = "email.search"
    family = "email"
    description = "Search your mailbox for messages matching a query."
    scopes = (Scope.EMAIL_READ,)
    risk_level = RiskLevel.LOW
    returns_untrusted_content = True
    timeout_seconds = 45.0
    Args = EmailSearchArgs

    async def execute(self, args: EmailSearchArgs, ctx: ToolContext) -> ToolResult:
        messages = await ctx.providers.email.search(
            args.query, limit=args.limit, folder=args.folder
        )
        return ToolResult.ok(
            {
                "count": len(messages),
                "folder": args.folder,
                "messages": [m.model_dump(mode="json", exclude={"body"}) for m in messages],
            }
        )


class EmailReadArgs(BaseModel):
    message_id: str
    folder: str = Field(default="INBOX", max_length=64)


class EmailReadTool(Tool[EmailReadArgs]):
    name = "email.read"
    family = "email"
    description = "Read one email message in full."
    scopes = (Scope.EMAIL_READ,)
    risk_level = RiskLevel.LOW
    #: Message bodies are attacker-controlled text.
    returns_untrusted_content = True
    timeout_seconds = 45.0
    Args = EmailReadArgs

    async def execute(self, args: EmailReadArgs, ctx: ToolContext) -> ToolResult:
        message = await ctx.providers.email.read(args.message_id, folder=args.folder)
        return ToolResult.ok(
            message.model_dump(mode="json"),
            accessed_resources=(f"email:{message.id}",),
            metadata={"untrusted": True},
        )


class EmailDraftArgs(BaseModel):
    to: list[str] = Field(min_length=1, max_length=25, description="Recipient addresses.")
    subject: str = Field(default="", max_length=300)
    body: str = Field(default="", max_length=100_000)
    cc: list[str] = Field(default_factory=list, max_length=25)
    bcc: list[str] = Field(default_factory=list, max_length=25)
    in_reply_to: str | None = None


class EmailDraftTool(Tool[EmailDraftArgs]):
    name = "email.draft"
    family = "email"
    description = (
        "Write an email draft. This NEVER sends anything; it stores a draft locally and "
        "returns its id so it can be previewed and, if you approve, sent."
    )
    scopes = (Scope.EMAIL_DRAFT,)
    risk_level = RiskLevel.LOW
    Args = EmailDraftArgs

    async def execute(self, args: EmailDraftArgs, ctx: ToolContext) -> ToolResult:
        to = parse_addresses(args.to)
        cc = parse_addresses(args.cc)
        bcc = parse_addresses(args.bcc)
        draft = await ctx.providers.email.draft(
            to,
            validate_subject(args.subject),
            validate_body(args.body),
            cc=cc,
            bcc=bcc,
            in_reply_to=args.in_reply_to,
        )
        ctx.audit.record(
            AuditAction.EMAIL_DRAFTED,
            tool_name=self.name,
            target=draft.id,
            session_id=ctx.session_id,
            run_id=ctx.run_id,
            request_id=ctx.request_id,
            detail={"recipients": len(to), "subject_length": len(draft.subject)},
        )
        return ToolResult.ok(
            draft.model_dump(mode="json"),
            accessed_resources=(f"draft:{draft.id}",),
            metadata={"sent": False, "next_step": "Preview it, then confirm to send."},
        )


class EmailReplyArgs(BaseModel):
    message_id: str
    body: str = Field(min_length=1, max_length=100_000)
    folder: str = Field(default="INBOX", max_length=64)
    reply_all: bool = False


class EmailReplyTool(Tool[EmailReplyArgs]):
    name = "email.reply"
    family = "email"
    description = "Draft a reply to a message. Like email.draft, this never sends."
    scopes = (Scope.EMAIL_READ, Scope.EMAIL_DRAFT)
    risk_level = RiskLevel.LOW
    timeout_seconds = 45.0
    Args = EmailReplyArgs

    async def execute(self, args: EmailReplyArgs, ctx: ToolContext) -> ToolResult:
        original = await ctx.providers.email.read(args.message_id, folder=args.folder)
        recipients = [original.sender.address] if original.sender else []
        cc: list[str] = []
        if args.reply_all:
            cc = [a.address for a in original.to] + [a.address for a in original.cc]
        subject = original.subject or ""
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}".strip()
        draft = await ctx.providers.email.draft(
            parse_addresses(recipients),
            validate_subject(subject),
            validate_body(args.body),
            cc=parse_addresses([c for c in cc if c not in recipients]),
            in_reply_to=original.id,
        )
        ctx.audit.record(
            AuditAction.EMAIL_DRAFTED,
            tool_name=self.name,
            target=draft.id,
            session_id=ctx.session_id,
            run_id=ctx.run_id,
            request_id=ctx.request_id,
            detail={"in_reply_to": original.id},
        )
        return ToolResult.ok(
            draft.model_dump(mode="json"),
            accessed_resources=(f"draft:{draft.id}",),
            metadata={"sent": False},
        )


class EmailUpdateDraftArgs(BaseModel):
    draft_id: str
    subject: str | None = Field(default=None, max_length=300)
    body: str | None = Field(default=None, max_length=100_000)


class EmailUpdateDraftTool(Tool[EmailUpdateDraftArgs]):
    name = "email.update_draft"
    family = "email"
    description = "Edit the subject or body of an existing draft."
    scopes = (Scope.EMAIL_DRAFT,)
    risk_level = RiskLevel.LOW
    Args = EmailUpdateDraftArgs

    async def execute(self, args: EmailUpdateDraftArgs, ctx: ToolContext) -> ToolResult:
        draft = await ctx.providers.email.update_draft(
            args.draft_id, subject=args.subject, body=args.body
        )
        return ToolResult.ok(
            draft.model_dump(mode="json"), accessed_resources=(f"draft:{draft.id}",)
        )


class EmailSendArgs(BaseModel):
    draft_id: str = Field(description="Id of a draft created by email.draft or email.reply.")


class EmailSendTool(Tool[EmailSendArgs]):
    name = "email.send"
    family = "email"
    description = (
        "Send a previously written draft. Requires explicit confirmation every time and "
        "shows the exact recipients, subject and body first."
    )
    scopes = (Scope.EMAIL_SEND,)
    risk_level = RiskLevel.CRITICAL
    requires_confirmation = True
    #: Never retried: a timeout might mean the message went out.
    retry_policy = RetryPolicy(max_attempts=1)
    timeout_seconds = 60.0
    Args = EmailSendArgs

    def resources(self, args: EmailSendArgs, ctx: ToolContext) -> tuple[str, ...]:
        return (f"draft:{args.draft_id}",)

    def confirmation(self, args: EmailSendArgs, ctx: ToolContext) -> ConfirmationRequest:
        draft = ctx.repositories.drafts.get(args.draft_id)
        if draft is None:
            raise ValidationError(
                f"There is no draft with id {args.draft_id}.",
                details={"draft_id": args.draft_id},
            )
        if draft.status == "sent":
            raise ConflictError(
                "That draft has already been sent.", details={"draft_id": args.draft_id}
            )
        preview = draft.body if len(draft.body) <= 800 else draft.body[:800] + "\n... (truncated)"
        return ConfirmationRequest(
            id=self.confirmation_id(args, ctx),
            run_id=ctx.run_id,
            tool_name=self.name,
            title="Send this email?",
            summary=(
                f"Send \"{draft.subject or '(no subject)'}\" to "
                f"{', '.join(a.address for a in draft.to)}."
            ),
            risk_level=RiskLevel.CRITICAL,
            details={
                "To": ", ".join(str(a) for a in draft.to),
                "Cc": ", ".join(str(a) for a in draft.cc) or "none",
                "Bcc": ", ".join(str(a) for a in draft.bcc) or "none",
                "Subject": draft.subject or "(no subject)",
                "Body": preview,
                "Attachments": ", ".join(a.filename for a in draft.attachments) or "none",
                "Provider": ctx.providers.email.display_name,
            },
            target=args.draft_id,
            destructive=False,
        )

    async def execute(self, args: EmailSendArgs, ctx: ToolContext) -> ToolResult:
        draft = ctx.repositories.drafts.get(args.draft_id)
        if draft is None:
            raise ValidationError(f"There is no draft with id {args.draft_id}.")
        if draft.status == "sent":
            raise ConflictError("That draft has already been sent.")
        message = await ctx.providers.email.send(draft)

        # Verify: the draft must now be marked sent in local storage.
        stored = ctx.repositories.drafts.get(args.draft_id)
        verified = bool(stored and stored.status == "sent")
        ctx.audit.record(
            AuditAction.EMAIL_SENT,
            tool_name=self.name,
            target=args.draft_id,
            session_id=ctx.session_id,
            run_id=ctx.run_id,
            request_id=ctx.request_id,
            outcome="success" if verified else "failure",
            detail={
                "recipients": [a.address for a in draft.to],
                "subject_length": len(draft.subject),
                "provider": ctx.providers.email.name,
                "verified": verified,
            },
        )
        return ToolResult.ok(
            {
                "message_id": message.id,
                "to": [a.address for a in draft.to],
                "subject": draft.subject,
                "provider": ctx.providers.email.name,
                "verified": verified,
            },
            accessed_resources=(f"draft:{args.draft_id}",),
            metadata={"sent": True},
        )


class EmailListDraftsArgs(BaseModel):
    limit: int = Field(default=20, ge=1, le=100)


class EmailListDraftsTool(Tool[EmailListDraftsArgs]):
    name = "email.list_drafts"
    family = "email"
    description = "List email drafts that have not been sent."
    scopes = (Scope.EMAIL_DRAFT,)
    risk_level = RiskLevel.NONE
    Args = EmailListDraftsArgs

    async def execute(self, args: EmailListDraftsArgs, ctx: ToolContext) -> ToolResult:
        drafts = await ctx.providers.email.list_drafts(args.limit)
        return ToolResult.ok(
            {"count": len(drafts), "drafts": [d.model_dump(mode="json") for d in drafts]}
        )


EMAIL_TOOLS = [
    EmailSearchTool(),
    EmailReadTool(),
    EmailDraftTool(),
    EmailReplyTool(),
    EmailUpdateDraftTool(),
    EmailSendTool(),
    EmailListDraftsTool(),
]
