"""Local-first email adapter.

The default email provider does not talk to any server. Drafts live in the local
database; "sent" messages are written to a local maildir-style folder as ``.eml``
files. This means the whole draft -> preview -> confirm -> send -> audit flow is
real and testable on a machine with no mail account configured, and nothing is
silently transmitted.

Configure ``EMAIL_PROVIDER=smtp`` to send for real; see :mod:`.smtp`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from email.message import EmailMessage as PyEmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

from privia_shared.domain import (
    EmailAddress,
    EmailAttachment,
    EmailDraft,
    EmailMessage,
    IntegrationInfo,
)
from privia_shared.errors import NotFoundError, ToolError
from privia_shared.ids import utcnow
from privia_storage.repositories import EmailDraftRepository

from ..base import EmailProvider
from .base import format_recipients, validate_body, validate_subject


class LocalEmailProvider(EmailProvider):
    name = "local"
    display_name = "Local mailbox"

    def __init__(
        self,
        repository: EmailDraftRepository,
        store_dir: Path,
        *,
        from_address: str = "me@localhost",
    ) -> None:
        self.repository = repository
        self.store_dir = Path(store_dir).expanduser()
        self.from_address = from_address

    def capabilities(self) -> tuple[str, ...]:
        return ("draft", "read_drafts", "search_local", "send_local", "reply")

    async def health_check(self) -> IntegrationInfo:
        try:
            self.store_dir.mkdir(parents=True, exist_ok=True)
            (self.store_dir / "sent").mkdir(exist_ok=True)
        except OSError as exc:
            return self.errored(f"The local mailbox folder is not writable: {exc}")
        drafts = await asyncio.to_thread(self.repository.list, "draft", 500)
        return self.ok(
            f"local mailbox at {self.store_dir}; {len(drafts)} draft(s). "
            "Nothing is transmitted until you configure SMTP."
        )

    # -- drafts ---------------------------------------------------------------

    async def draft(
        self,
        to: Sequence[EmailAddress],
        subject: str,
        body: str,
        *,
        cc: Sequence[EmailAddress] = (),
        bcc: Sequence[EmailAddress] = (),
        in_reply_to: str | None = None,
        attachments: Sequence[EmailAttachment] = (),
    ) -> EmailDraft:
        return await asyncio.to_thread(
            self.repository.create,
            list(to),
            validate_subject(subject),
            validate_body(body),
            cc=list(cc),
            bcc=list(bcc),
            in_reply_to=in_reply_to,
            attachments=list(attachments),
        )

    async def get_draft(self, draft_id: str) -> EmailDraft:
        draft = await asyncio.to_thread(self.repository.get, draft_id)
        if draft is None:
            raise NotFoundError(f"No draft with id {draft_id}.", details={"draft_id": draft_id})
        return draft

    async def list_drafts(self, limit: int = 50) -> list[EmailDraft]:
        return await asyncio.to_thread(self.repository.list, "draft", limit)

    async def update_draft(
        self, draft_id: str, *, subject: str | None = None, body: str | None = None
    ) -> EmailDraft:
        return await asyncio.to_thread(
            self.repository.update_body,
            draft_id,
            validate_subject(subject) if subject is not None else None,
            validate_body(body) if body is not None else None,
        )

    async def discard_draft(self, draft_id: str) -> bool:
        return await asyncio.to_thread(self.repository.delete, draft_id)

    # -- reading --------------------------------------------------------------

    async def search(
        self, query: str, *, limit: int = 25, folder: str = "sent"
    ) -> list[EmailMessage]:
        """Search the local mailbox. Remote folders require the SMTP/IMAP provider."""
        return await asyncio.to_thread(self._search_sync, query, limit, folder)

    def _search_sync(self, query: str, limit: int, folder: str) -> list[EmailMessage]:
        directory = self.store_dir / folder
        if not directory.is_dir():
            return []
        needle = query.lower().strip()
        found: list[EmailMessage] = []
        for path in sorted(directory.glob("*.eml"), reverse=True):
            try:
                raw = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if needle and needle not in raw.lower():
                continue
            found.append(self._parse_eml(raw, path.stem, folder))
            if len(found) >= limit:
                break
        return found

    @staticmethod
    def _parse_eml(raw: str, identifier: str, folder: str) -> EmailMessage:
        from email import message_from_string

        parsed = message_from_string(raw)
        body_part = parsed.get_payload(decode=False)
        body = body_part if isinstance(body_part, str) else ""
        return EmailMessage(
            id=identifier,
            folder=folder,
            subject=parsed.get("Subject", ""),
            sender=EmailAddress(address=parsed.get("From", "unknown@localhost")),
            to=tuple(
                EmailAddress(address=a.strip())
                for a in (parsed.get("To", "") or "").split(",")
                if a.strip()
            ),
            snippet=" ".join(body.split())[:200],
            body=body,
        )

    async def read(self, message_id: str, folder: str = "sent") -> EmailMessage:
        path = self.store_dir / folder / f"{message_id}.eml"
        if not path.exists():
            raise NotFoundError(f"No message with id {message_id}.")
        raw = await asyncio.to_thread(path.read_text, "utf-8")
        return self._parse_eml(raw, message_id, folder)

    # -- sending --------------------------------------------------------------

    async def send(self, draft: EmailDraft) -> EmailMessage:
        """'Send' by writing an RFC 5322 message to the local sent folder."""
        return await asyncio.to_thread(self._send_sync, draft)

    def _send_sync(self, draft: EmailDraft) -> EmailMessage:
        if not draft.to:
            raise ToolError("The draft has no recipients.")
        message = build_message(draft, self.from_address)
        sent_dir = self.store_dir / "sent"
        sent_dir.mkdir(parents=True, exist_ok=True)
        path = sent_dir / f"{draft.id}.eml"
        try:
            path.write_text(message.as_string(), encoding="utf-8")
        except OSError as exc:
            raise ToolError(f"The message could not be written locally: {exc}") from exc
        self.repository.mark_sent(draft.id)
        return EmailMessage(
            id=draft.id,
            folder="sent",
            subject=draft.subject,
            sender=EmailAddress(address=self.from_address),
            to=draft.to,
            cc=draft.cc,
            date=utcnow(),
            snippet=" ".join(draft.body.split())[:200],
            body=draft.body,
            has_attachments=bool(draft.attachments),
            attachments=draft.attachments,
        )


def build_message(draft: EmailDraft, from_address: str) -> PyEmailMessage:
    """Build an RFC 5322 message. Header values are validated upstream."""
    message = PyEmailMessage()
    message["From"] = from_address
    message["To"] = format_recipients(draft.to)
    if draft.cc:
        message["Cc"] = format_recipients(draft.cc)
    message["Subject"] = draft.subject
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain="privia.local")
    if draft.in_reply_to:
        message["In-Reply-To"] = draft.in_reply_to
        message["References"] = draft.in_reply_to
    message["X-Mailer"] = "PRIVIA 1.0 (local personal assistant)"
    message.set_content(draft.body or "")
    for attachment in draft.attachments:
        if not attachment.path:
            continue
        source = Path(attachment.path)
        if not source.is_file():
            raise ToolError(
                f"Attachment '{attachment.filename}' was not found.",
                details={"path": attachment.path},
            )
        maintype, _, subtype = (attachment.mime_type or "application/octet-stream").partition("/")
        message.add_attachment(
            source.read_bytes(),
            maintype=maintype or "application",
            subtype=subtype or "octet-stream",
            filename=attachment.filename,
        )
    return message


class MockEmailProvider(LocalEmailProvider):
    """Identical to the local provider but never touches the filesystem."""

    name = "mock"
    display_name = "Mock mailbox"

    def __init__(self, repository: EmailDraftRepository) -> None:
        super().__init__(repository, Path("/nonexistent"), from_address="mock@localhost")
        self.sent: list[EmailDraft] = []

    async def health_check(self) -> IntegrationInfo:
        return self.ok("mock provider; nothing is written or transmitted")

    def _send_sync(self, draft: EmailDraft) -> EmailMessage:
        self.sent.append(draft)
        self.repository.mark_sent(draft.id)
        return EmailMessage(
            id=draft.id,
            folder="sent",
            subject=draft.subject,
            sender=EmailAddress(address=self.from_address),
            to=draft.to,
            date=utcnow(),
            snippet=draft.body[:200],
            body=draft.body,
        )

    def _search_sync(self, query: str, limit: int, folder: str) -> list[EmailMessage]:
        needle = query.lower()
        return [
            EmailMessage(id=d.id, folder="sent", subject=d.subject, to=d.to, snippet=d.body[:200])
            for d in self.sent
            if needle in d.subject.lower() or needle in d.body.lower()
        ][:limit]
