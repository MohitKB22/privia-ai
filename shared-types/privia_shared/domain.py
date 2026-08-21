"""Domain payloads for each tool family.

These are the shapes that cross the API boundary and are rendered by the
desktop client, so they stay deliberately small and provider-neutral.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from .base import PriviaModel
from .enums import IntegrationStatus, MemoryKind

# --- Files -------------------------------------------------------------------


class FileEntry(PriviaModel):
    path: str
    name: str
    is_dir: bool = False
    size_bytes: int = 0
    modified_at: datetime | None = None
    extension: str = ""
    mime_type: str | None = None


class FileMetadata(PriviaModel):
    path: str
    name: str
    size_bytes: int
    created_at: datetime | None = None
    modified_at: datetime | None = None
    extension: str = ""
    mime_type: str | None = None
    sha256: str | None = None
    line_count: int | None = None
    word_count: int | None = None
    is_symlink: bool = False


class FileContent(PriviaModel):
    path: str
    text: str
    encoding: str = "utf-8"
    truncated: bool = False
    bytes_read: int = 0


# --- Notes -------------------------------------------------------------------


class Note(PriviaModel):
    id: str
    title: str
    body: str = ""
    tags: tuple[str, ...] = ()
    created_at: datetime
    updated_at: datetime
    pinned: bool = False


# --- Calendar ----------------------------------------------------------------


class CalendarEvent(PriviaModel):
    id: str
    title: str
    start: datetime
    end: datetime
    timezone: str = "UTC"
    all_day: bool = False
    location: str | None = None
    description: str | None = None
    participants: tuple[str, ...] = ()
    calendar: str = "default"
    cancelled: bool = False


# --- Email -------------------------------------------------------------------


class EmailAddress(PriviaModel):
    address: str
    name: str | None = None

    def __str__(self) -> str:
        return f"{self.name} <{self.address}>" if self.name else self.address


class EmailAttachment(PriviaModel):
    filename: str
    size_bytes: int
    mime_type: str
    #: Absolute path inside an allowed directory. Content is never inlined.
    path: str | None = None


class EmailMessage(PriviaModel):
    id: str
    thread_id: str | None = None
    folder: str = "INBOX"
    subject: str = ""
    sender: EmailAddress | None = None
    to: tuple[EmailAddress, ...] = ()
    cc: tuple[EmailAddress, ...] = ()
    bcc: tuple[EmailAddress, ...] = ()
    date: datetime | None = None
    snippet: str = ""
    body: str | None = None
    unread: bool = False
    has_attachments: bool = False
    attachments: tuple[EmailAttachment, ...] = ()


class EmailDraft(PriviaModel):
    id: str
    to: tuple[EmailAddress, ...] = ()
    cc: tuple[EmailAddress, ...] = ()
    bcc: tuple[EmailAddress, ...] = ()
    subject: str = ""
    body: str = ""
    in_reply_to: str | None = None
    attachments: tuple[EmailAttachment, ...] = ()
    created_at: datetime
    updated_at: datetime
    sent_at: datetime | None = None
    status: Literal["draft", "sent", "failed"] = "draft"


# --- Browser -----------------------------------------------------------------


class SearchResult(PriviaModel):
    title: str
    url: str
    snippet: str = ""


class PageContent(PriviaModel):
    url: str
    final_url: str
    title: str = ""
    text: str = ""
    #: Always true for web content: it is data, never instructions.
    untrusted: bool = True
    truncated: bool = False
    bytes_fetched: int = 0
    content_type: str = ""
    links: tuple[str, ...] = ()
    injection_flags: tuple[str, ...] = ()


# --- Terminal ----------------------------------------------------------------


class CommandInspection(PriviaModel):
    raw: str
    argv: tuple[str, ...]
    program: str
    allowed: bool
    requires_confirmation: bool
    reason: str = ""
    matched_rule: str | None = None


class CommandResult(PriviaModel):
    argv: tuple[str, ...]
    cwd: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    timed_out: bool = False
    truncated: bool = False


# --- Memory ------------------------------------------------------------------


class MemoryRecord(PriviaModel):
    id: str
    kind: MemoryKind
    content: str
    tags: tuple[str, ...] = ()
    #: Where this memory came from: "user:explicit", "run:<id>", "note:<id>".
    provenance: str = "user:explicit"
    session_id: str | None = None
    created_at: datetime
    updated_at: datetime
    last_used_at: datetime | None = None
    use_count: int = 0
    score: float | None = None
    pinned: bool = False


# --- Audit / privacy ---------------------------------------------------------


class AuditEvent(PriviaModel):
    id: str
    timestamp: datetime
    action: str
    session_id: str | None = None
    run_id: str | None = None
    request_id: str | None = None
    actor: str = "user"
    tool_name: str | None = None
    target: str | None = None
    outcome: Literal["success", "failure", "denied", "pending"] = "success"
    detail: dict[str, Any] = Field(default_factory=dict)


class IntegrationInfo(PriviaModel):
    name: str
    family: str
    provider: str
    status: IntegrationStatus
    capabilities: tuple[str, ...] = ()
    detail: str = ""
    authenticated: bool = False
    checked_at: datetime | None = None


class ModelInfo(PriviaModel):
    provider: str
    model: str
    available: bool
    location: str = "local"
    detail: str = ""
    latency_ms: int | None = None


class PrivacyState(PriviaModel):
    local_processing: bool = True
    cloud_processing: bool = False
    cloud_provider: str | None = None
    cloud_consent_given: bool = False
    current_llm: ModelInfo | None = None
    current_embedding_model: str = ""
    stt_available: bool = False
    tts_available: bool = False
    telemetry_enabled: bool = False
    memory_enabled: bool = True
    data_leaving_device: bool = False
    data_retention_days: int | None = None
    allowed_directories: tuple[str, ...] = ()
    terminal_roots: tuple[str, ...] = ()
    integrations: tuple[IntegrationInfo, ...] = ()
    grants: tuple[dict[str, Any], ...] = ()
    recent_activity: tuple[AuditEvent, ...] = ()
    database_path: str = ""


class HealthReport(PriviaModel):
    status: Literal["ok", "degraded", "error"] = "ok"
    version: str = "1.0.0"
    uptime_seconds: float = 0.0
    checks: dict[str, Any] = Field(default_factory=dict)
