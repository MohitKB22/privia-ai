"""Builds the concrete provider set from settings.

This is the only place that reads configuration to decide *which* adapter is
used. Everything downstream depends on the interfaces in :mod:`.base`.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from privia_security.commands import CommandGuard
from privia_security.paths import PathGuard
from privia_security.secrets import SecretStore
from privia_security.urls import UrlGuard
from privia_shared.config import Settings
from privia_shared.domain import IntegrationInfo
from privia_storage.repositories import Repositories

from .base import (
    BrowserProvider,
    CalendarProvider,
    EmailProvider,
    Provider,
)
from .browser.http import HttpBrowserProvider, MockBrowserProvider
from .calendar.ics import IcsCalendarProvider, MockCalendarProvider
from .email.local import LocalEmailProvider, MockEmailProvider
from .email.smtp import SmtpEmailProvider
from .files.local import LocalFilesystemProvider
from .notes.local import LocalNotesProvider
from .terminal.local import LocalTerminalProvider


@dataclass
class ProviderSet:
    """Every adapter the tool layer can reach, plus the guards they share."""

    files: LocalFilesystemProvider
    notes: LocalNotesProvider
    terminal: LocalTerminalProvider
    browser: BrowserProvider
    calendar: CalendarProvider
    email: EmailProvider
    path_guard: PathGuard
    command_guard: CommandGuard
    url_guard: UrlGuard
    secrets: SecretStore

    def all(self) -> tuple[Provider, ...]:
        return (self.files, self.notes, self.terminal, self.browser, self.calendar, self.email)

    async def health(self) -> list[IntegrationInfo]:
        results = await asyncio.gather(
            *(provider.health_check() for provider in self.all()), return_exceptions=True
        )
        infos: list[IntegrationInfo] = []
        for provider, result in zip(self.all(), results, strict=True):
            if isinstance(result, IntegrationInfo):
                infos.append(result)
            else:
                infos.append(provider.errored(f"Health check raised {type(result).__name__}"))
        return infos

    def update_allowed_directories(self, directories: list[Path]) -> None:
        """Re-point every guard when the user grants or revokes a folder."""
        self.path_guard.set_roots(directories)
        self.command_guard.workspace_roots = tuple(
            Path(d).expanduser().resolve(strict=False) for d in directories
        )


def build_providers(
    settings: Settings,
    repositories: Repositories,
    *,
    offline: bool = False,
) -> ProviderSet:
    path_guard = PathGuard(
        settings.allowed_directory_list,
        follow_symlinks=False,
        max_file_bytes=settings.max_file_read_bytes,
    )
    command_guard = CommandGuard(workspace_roots=settings.terminal_root_list)
    url_guard = UrlGuard(
        allowed_domains=settings.browser_allowed_domain_list,
        blocked_domains=settings.browser_blocked_domain_list,
    )
    secrets = SecretStore(settings.data_dir, preferred=settings.secrets_backend)

    files = LocalFilesystemProvider(path_guard)
    notes = LocalNotesProvider(repositories.notes)
    terminal = LocalTerminalProvider(
        command_guard,
        timeout_seconds=settings.command_timeout_seconds,
        max_output_bytes=settings.max_tool_output_bytes,
    )

    browser: BrowserProvider
    if offline:
        browser = MockBrowserProvider()
    else:
        browser = HttpBrowserProvider(
            url_guard,
            timeout_seconds=settings.http_timeout_seconds,
            max_bytes=settings.max_page_bytes,
        )

    calendar: CalendarProvider
    if settings.calendar_provider == "mock":
        calendar = MockCalendarProvider()
    else:
        calendar = IcsCalendarProvider(settings.calendar_dir)

    email: EmailProvider
    if settings.email_provider == "mock":
        email = MockEmailProvider(repositories.drafts)
    elif settings.email_provider == "smtp":
        email = SmtpEmailProvider(
            repositories.drafts,
            settings.email_store_dir,
            smtp_host=settings.smtp_host,
            smtp_port=settings.smtp_port,
            smtp_username=settings.smtp_username,
            smtp_password=secrets.get("smtp_password", settings.smtp_password) or "",
            use_starttls=settings.smtp_starttls,
            imap_host=settings.imap_host,
            imap_port=settings.imap_port,
            imap_username=settings.imap_username,
            imap_password=secrets.get("imap_password", settings.imap_password) or "",
            from_address=settings.email_from or settings.smtp_username,
            timeout_seconds=settings.http_timeout_seconds * 2,
        )
    else:
        email = LocalEmailProvider(
            repositories.drafts,
            settings.email_store_dir,
            from_address=settings.email_from or "me@localhost",
        )

    return ProviderSet(
        files=files,
        notes=notes,
        terminal=terminal,
        browser=browser,
        calendar=calendar,
        email=email,
        path_guard=path_guard,
        command_guard=command_guard,
        url_guard=url_guard,
        secrets=secrets,
    )
