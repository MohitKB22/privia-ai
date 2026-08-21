"""PRIVIA integration adapters.

Every external system is reached through an adapter that implements a small,
provider-neutral interface. Swapping a provider (local mailbox to SMTP, ICS to
mock) never changes a line of tool or agent code.
"""

from __future__ import annotations

from .base import (
    BrowserProvider,
    CalendarProvider,
    EmailProvider,
    FilesystemProvider,
    NotesProvider,
    Provider,
    TerminalProvider,
)
from .browser.extract import extract_text
from .browser.http import HttpBrowserProvider, MockBrowserProvider
from .calendar.ics import IcsCalendarProvider, MockCalendarProvider
from .email.base import parse_address, parse_addresses, validate_body, validate_subject
from .email.local import LocalEmailProvider, MockEmailProvider, build_message
from .email.smtp import SmtpEmailProvider
from .files.local import LocalFilesystemProvider, summarize_text
from .notes.local import LocalNotesProvider
from .registry import ProviderSet, build_providers
from .terminal.local import LocalTerminalProvider

__all__ = [
    "BrowserProvider",
    "CalendarProvider",
    "EmailProvider",
    "FilesystemProvider",
    "HttpBrowserProvider",
    "IcsCalendarProvider",
    "LocalEmailProvider",
    "LocalFilesystemProvider",
    "LocalNotesProvider",
    "LocalTerminalProvider",
    "MockBrowserProvider",
    "MockCalendarProvider",
    "MockEmailProvider",
    "NotesProvider",
    "Provider",
    "ProviderSet",
    "SmtpEmailProvider",
    "TerminalProvider",
    "build_message",
    "build_providers",
    "extract_text",
    "parse_address",
    "parse_addresses",
    "summarize_text",
    "validate_body",
    "validate_subject",
]
