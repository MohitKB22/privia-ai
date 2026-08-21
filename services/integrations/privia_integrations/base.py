"""Provider adapter interfaces.

Each tool family talks to an *adapter*, never to a vendor SDK. Every adapter
implements :class:`Provider`, which guarantees four things the UI depends on:

* ``name`` and ``family`` for display,
* ``capabilities()`` so the UI can hide what is not supported,
* ``health_check()`` that never raises, so a broken integration degrades to a
  clear message instead of a stack trace,
* ``authenticated`` so the UI can prompt for credentials.

Adapters map their own errors onto :class:`~privia_shared.errors.PriviaError`
subclasses; nothing vendor-specific escapes this layer.
"""

from __future__ import annotations

import abc
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from privia_shared.domain import (
    CalendarEvent,
    EmailAddress,
    EmailAttachment,
    EmailDraft,
    EmailMessage,
    IntegrationInfo,
    PageContent,
    SearchResult,
)
from privia_shared.enums import IntegrationStatus
from privia_shared.ids import utcnow


class Provider(abc.ABC):
    """Base class for every integration adapter."""

    family: str = "generic"
    name: str = "provider"
    #: Human-readable, shown in the Privacy Center.
    display_name: str = "Provider"
    requires_network: bool = False

    @property
    def authenticated(self) -> bool:
        return True

    def capabilities(self) -> tuple[str, ...]:
        return ()

    @abc.abstractmethod
    async def health_check(self) -> IntegrationInfo:
        """Report status. Must never raise."""

    def _info(
        self,
        status: IntegrationStatus,
        detail: str = "",
        **extra: Any,
    ) -> IntegrationInfo:
        return IntegrationInfo(
            name=f"{self.family}.{self.name}",
            family=self.family,
            provider=self.name,
            status=status,
            capabilities=self.capabilities(),
            detail=detail,
            authenticated=self.authenticated,
            checked_at=utcnow(),
            **extra,
        )

    def ok(self, detail: str = "") -> IntegrationInfo:
        return self._info(IntegrationStatus.READY, detail)

    def not_configured(self, detail: str) -> IntegrationInfo:
        return self._info(IntegrationStatus.NOT_CONFIGURED, detail)

    def unavailable(self, detail: str) -> IntegrationInfo:
        return self._info(IntegrationStatus.UNAVAILABLE, detail)

    def auth_required(self, detail: str) -> IntegrationInfo:
        return self._info(IntegrationStatus.AUTH_REQUIRED, detail)

    def errored(self, detail: str) -> IntegrationInfo:
        return self._info(IntegrationStatus.ERROR, detail)


class FilesystemProvider(Provider):
    family = "files"


class NotesProvider(Provider):
    family = "notes"


class CalendarProvider(Provider):
    """Calendar operations.

    Declaring the methods here is not decoration: it is what makes "swap the
    provider and nothing upstream changes" a checked claim rather than a hope.
    A new adapter that forgets ``cancel_event`` fails type checking instead of
    failing at the moment a user tries to cancel a meeting.
    """

    family = "calendar"

    @abc.abstractmethod
    async def list_events(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        include_cancelled: bool = False,
        limit: int = 100,
    ) -> list[CalendarEvent]: ...

    @abc.abstractmethod
    async def search_events(self, query: str, limit: int = 25) -> list[CalendarEvent]: ...

    @abc.abstractmethod
    async def get_event(self, event_uid: str) -> CalendarEvent: ...

    @abc.abstractmethod
    async def create_event(self, event: CalendarEvent) -> CalendarEvent: ...

    @abc.abstractmethod
    async def update_event(self, event_uid: str, **changes: Any) -> CalendarEvent: ...

    @abc.abstractmethod
    async def cancel_event(self, event_uid: str, *, delete: bool = False) -> CalendarEvent: ...


class EmailProvider(Provider):
    """Email operations.

    Note the shape of this interface: ``send`` takes a stored :class:`EmailDraft`,
    never a subject and body. The bytes the user approved in the preview are the
    bytes that go out, and no adapter can be written that does otherwise.
    """

    family = "email"

    @abc.abstractmethod
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
    ) -> EmailDraft: ...

    @abc.abstractmethod
    async def get_draft(self, draft_id: str) -> EmailDraft: ...

    @abc.abstractmethod
    async def list_drafts(self, limit: int = 50) -> list[EmailDraft]: ...

    @abc.abstractmethod
    async def update_draft(
        self, draft_id: str, *, subject: str | None = None, body: str | None = None
    ) -> EmailDraft: ...

    @abc.abstractmethod
    async def discard_draft(self, draft_id: str) -> bool: ...

    @abc.abstractmethod
    async def search(
        self, query: str, *, limit: int = 25, folder: str = "INBOX"
    ) -> list[EmailMessage]: ...

    @abc.abstractmethod
    async def read(self, message_id: str, folder: str = "INBOX") -> EmailMessage: ...

    @abc.abstractmethod
    async def send(self, draft: EmailDraft) -> EmailMessage: ...


class BrowserProvider(Provider):
    """Read-only web access. There is deliberately no method that submits a form."""

    family = "browser"
    requires_network = True

    @abc.abstractmethod
    async def open_url(self, url: str, *, max_chars: int = 20_000) -> PageContent: ...

    @abc.abstractmethod
    async def search(self, query: str, *, limit: int = 8) -> list[SearchResult]: ...


class TerminalProvider(Provider):
    family = "terminal"
