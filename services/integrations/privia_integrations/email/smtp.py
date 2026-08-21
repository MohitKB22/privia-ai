"""SMTP/IMAP email adapter.

Used only when ``EMAIL_PROVIDER=smtp``. Sending still goes through the same
draft -> confirm -> send flow; this adapter only changes what "send" means at
the very last step.

Credentials are read from the :class:`privia_security.SecretStore`, never from
the database and never from the child-process environment.
"""

from __future__ import annotations

import asyncio
import imaplib
import smtplib
import ssl
from collections.abc import Sequence
from email import message_from_bytes
from email.header import decode_header, make_header

from privia_shared.domain import EmailAddress, EmailDraft, EmailMessage, IntegrationInfo
from privia_shared.errors import IntegrationUnavailableError, NotFoundError, ToolError
from privia_shared.ids import utcnow
from privia_storage.repositories import EmailDraftRepository

from .local import LocalEmailProvider, build_message


class SmtpEmailProvider(LocalEmailProvider):
    """Drafts locally, sends via SMTP, reads via IMAP."""

    name = "smtp"
    display_name = "SMTP / IMAP mailbox"
    requires_network = True

    def __init__(
        self,
        repository: EmailDraftRepository,
        store_dir,
        *,
        smtp_host: str,
        smtp_port: int = 587,
        smtp_username: str = "",
        smtp_password: str = "",
        use_starttls: bool = True,
        imap_host: str = "",
        imap_port: int = 993,
        imap_username: str = "",
        imap_password: str = "",
        from_address: str = "",
        timeout_seconds: float = 30.0,
    ) -> None:
        super().__init__(repository, store_dir, from_address=from_address or smtp_username)
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.smtp_username = smtp_username
        self.smtp_password = smtp_password
        self.use_starttls = use_starttls
        self.imap_host = imap_host
        self.imap_port = imap_port
        self.imap_username = imap_username or smtp_username
        self.imap_password = imap_password or smtp_password
        self.timeout_seconds = timeout_seconds

    @property
    def authenticated(self) -> bool:
        return bool(self.smtp_host and self.smtp_username and self.smtp_password)

    def capabilities(self) -> tuple[str, ...]:
        caps = ["draft", "read_drafts", "send"]
        if self.imap_host:
            caps.extend(["search_remote", "read_remote"])
        return tuple(caps)

    async def health_check(self) -> IntegrationInfo:
        if not self.smtp_host:
            return self.not_configured("SMTP_HOST is not set.")
        if not self.authenticated:
            return self.auth_required("SMTP credentials are missing.")
        try:
            await asyncio.to_thread(self._probe_smtp)
        except Exception as exc:
            return self.errored(f"SMTP connection failed: {type(exc).__name__}")
        return self.ok(f"connected to {self.smtp_host}:{self.smtp_port}")

    def _probe_smtp(self) -> None:
        with self._smtp_connection() as server:
            server.noop()

    def _smtp_connection(self) -> smtplib.SMTP:
        context = ssl.create_default_context()
        if self.smtp_port == 465:
            server: smtplib.SMTP = smtplib.SMTP_SSL(
                self.smtp_host, self.smtp_port, timeout=self.timeout_seconds, context=context
            )
        else:
            server = smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=self.timeout_seconds)
            server.ehlo()
            if self.use_starttls:
                server.starttls(context=context)
                server.ehlo()
        if self.smtp_username:
            server.login(self.smtp_username, self.smtp_password)
        return server

    def _send_sync(self, draft: EmailDraft) -> EmailMessage:
        if not draft.to:
            raise ToolError("The draft has no recipients.")
        if not self.authenticated:
            raise IntegrationUnavailableError(
                "SMTP is selected but not fully configured, so the message was kept as a draft.",
                details={"draft_id": draft.id},
            )
        message = build_message(draft, self.from_address)
        recipients = [a.address for a in (*draft.to, *draft.cc, *draft.bcc)]
        try:
            with self._smtp_connection() as server:
                server.send_message(message, from_addr=self.from_address, to_addrs=recipients)
        except smtplib.SMTPAuthenticationError as exc:
            self.repository.mark_failed(draft.id)
            raise IntegrationUnavailableError(
                "The mail server rejected the credentials. The draft was kept.",
                details={"draft_id": draft.id},
            ) from exc
        except (smtplib.SMTPException, OSError) as exc:
            self.repository.mark_failed(draft.id)
            raise IntegrationUnavailableError(
                f"The message could not be sent ({type(exc).__name__}). The draft was kept.",
                details={"draft_id": draft.id},
            ) from exc
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

    # -- IMAP reading ---------------------------------------------------------

    async def search(
        self, query: str, *, limit: int = 25, folder: str = "INBOX"
    ) -> list[EmailMessage]:
        if not self.imap_host:
            return await super().search(query, limit=limit, folder="sent")
        return await asyncio.to_thread(self._imap_search, query, limit, folder)

    def _imap_search(self, query: str, limit: int, folder: str) -> list[EmailMessage]:
        try:
            with imaplib.IMAP4_SSL(
                self.imap_host, self.imap_port, timeout=self.timeout_seconds
            ) as client:
                client.login(self.imap_username, self.imap_password)
                client.select(_imap_folder(folder), readonly=True)
                criteria = _imap_criteria(query)
                status, data = client.search(None, *criteria)
                if status != "OK":
                    return []
                ids = data[0].split()[-limit:]
                messages: list[EmailMessage] = []
                for raw_id in reversed(ids):
                    # imaplib's stubs (and its documented contract) want str here.
                    message_number = raw_id.decode("ascii", errors="ignore")
                    status, payload = client.fetch(message_number, "(RFC822)")
                    if status != "OK" or not payload or not isinstance(payload[0], tuple):
                        continue
                    messages.append(_parse_imap(payload[0][1], raw_id.decode(), folder))
                return messages
        except imaplib.IMAP4.error as exc:
            raise IntegrationUnavailableError(
                f"The mail server refused the request ({type(exc).__name__}).",
            ) from exc
        except OSError as exc:
            raise IntegrationUnavailableError(
                "The mail server could not be reached.", details={"host": self.imap_host}
            ) from exc

    async def read(self, message_id: str, folder: str = "INBOX") -> EmailMessage:
        if not self.imap_host:
            return await super().read(message_id, folder="sent")
        messages = await asyncio.to_thread(self._imap_fetch_one, message_id, folder)
        if not messages:
            raise NotFoundError(f"No message with id {message_id}.")
        return messages

    def _imap_fetch_one(self, message_id: str, folder: str) -> EmailMessage | None:
        try:
            with imaplib.IMAP4_SSL(
                self.imap_host, self.imap_port, timeout=self.timeout_seconds
            ) as client:
                client.login(self.imap_username, self.imap_password)
                client.select(_imap_folder(folder), readonly=True)
                status, payload = client.fetch(message_id, "(RFC822)")
                if status != "OK" or not payload or not isinstance(payload[0], tuple):
                    return None
                return _parse_imap(payload[0][1], message_id, folder)
        except (imaplib.IMAP4.error, OSError) as exc:
            raise IntegrationUnavailableError("The mail server could not be reached.") from exc


def _imap_folder(name: str) -> str:
    safe = "".join(c for c in name if c.isalnum() or c in "._-/ ")[:64] or "INBOX"
    return f'"{safe}"'


def _imap_criteria(query: str) -> Sequence[str]:
    cleaned = "".join(c for c in query if c.isalnum() or c in " .@_-")[:120].strip()
    if not cleaned:
        return ("ALL",)
    return ("TEXT", f'"{cleaned}"')


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _parse_imap(raw: bytes, identifier: str, folder: str) -> EmailMessage:
    parsed = message_from_bytes(raw)
    body = ""
    if parsed.is_multipart():
        for part in parsed.walk():
            if part.get_content_type() == "text/plain" and not part.get_filename():
                payload = part.get_payload(decode=True)
                if isinstance(payload, bytes):
                    body = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
                break
    else:
        payload = parsed.get_payload(decode=True)
        if isinstance(payload, bytes):
            body = payload.decode(parsed.get_content_charset() or "utf-8", errors="replace")
    sender_raw = _decode(parsed.get("From"))
    return EmailMessage(
        id=identifier,
        folder=folder,
        subject=_decode(parsed.get("Subject")),
        sender=EmailAddress(address=_extract_address(sender_raw) or "unknown@unknown"),
        to=tuple(
            EmailAddress(address=addr)
            for addr in (_extract_address(a) for a in _decode(parsed.get("To")).split(","))
            if addr
        ),
        snippet=" ".join(body.split())[:200],
        body=body,
        has_attachments=(
            any(p.get_filename() for p in parsed.walk()) if parsed.is_multipart() else False
        ),
    )


def _extract_address(value: str) -> str:
    import re

    match = re.search(r"[\w.!#$%&'*+/=?^`{|}~-]+@[\w-]+(?:\.[\w-]+)+", value or "")
    return match.group(0).lower() if match else ""
