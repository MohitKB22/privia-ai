"""Enumerations shared across every PRIVIA layer.

These values are part of the public API contract (they are serialised over the
REST API and consumed by the desktop client), so they are stable strings rather
than integers.
"""

from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    """A ``str``-backed enum that serialises to its value.

    Python 3.11 ships ``enum.StrEnum``; PRIVIA supports 3.10 so we define a
    minimal equivalent instead of branching on the interpreter version.
    """

    def __str__(self) -> str:  # pragma: no cover - trivial
        return str(self.value)


class RiskLevel(StrEnum):
    """How much damage a tool call could do if it were wrong or malicious."""

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return _RISK_ORDER[self]

    def __ge__(self, other: object) -> bool:  # type: ignore[override]
        if isinstance(other, RiskLevel):
            return self.rank >= other.rank
        return NotImplemented

    def __gt__(self, other: object) -> bool:  # type: ignore[override]
        if isinstance(other, RiskLevel):
            return self.rank > other.rank
        return NotImplemented

    def __le__(self, other: object) -> bool:  # type: ignore[override]
        if isinstance(other, RiskLevel):
            return self.rank <= other.rank
        return NotImplemented

    def __lt__(self, other: object) -> bool:  # type: ignore[override]
        if isinstance(other, RiskLevel):
            return self.rank < other.rank
        return NotImplemented


_RISK_ORDER = {
    RiskLevel.NONE: 0,
    RiskLevel.LOW: 1,
    RiskLevel.MEDIUM: 2,
    RiskLevel.HIGH: 3,
    RiskLevel.CRITICAL: 4,
}


class Scope(StrEnum):
    """Capability scopes. A tool declares the scopes it needs; the permission
    engine decides whether the current session holds them."""

    FILES_READ = "files:read"
    FILES_WRITE = "files:write"
    FILES_DELETE = "files:delete"
    NOTES_READ = "notes:read"
    NOTES_WRITE = "notes:write"
    CALENDAR_READ = "calendar:read"
    CALENDAR_WRITE = "calendar:write"
    CALENDAR_DELETE = "calendar:delete"
    EMAIL_READ = "email:read"
    EMAIL_DRAFT = "email:draft"
    EMAIL_SEND = "email:send"
    BROWSER_READ = "browser:read"
    TERMINAL_EXEC = "terminal:exec"
    MEMORY_READ = "memory:read"
    MEMORY_WRITE = "memory:write"
    CLOUD_INFERENCE = "cloud:inference"

    @property
    def family(self) -> str:
        return self.value.split(":", 1)[0]


class PermissionDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    PROMPT = "prompt"


class PermissionGrantState(StrEnum):
    GRANTED = "granted"
    DENIED = "denied"
    NOT_REQUESTED = "not_requested"
    EXPIRED = "expired"


class AgentPhase(StrEnum):
    """Nodes of the deterministic agent state graph."""

    INPUT = "input"
    CLASSIFY = "classify"
    PLAN = "plan"
    POLICY_CHECK = "policy_check"
    TOOL_SELECTION = "tool_selection"
    EXECUTION = "execution"
    VERIFY = "verify"
    RESPOND = "respond"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    FAILED = "failed"


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    COMPLETED = "completed"
    FAILED = "failed"
    DENIED = "denied"


class Intent(StrEnum):
    """Coarse intent taxonomy produced by CLASSIFY."""

    CHITCHAT = "chitchat"
    QUESTION = "question"
    FILE_SEARCH = "file_search"
    FILE_READ = "file_read"
    FILE_WRITE = "file_write"
    FILE_DELETE = "file_delete"
    SUMMARIZE = "summarize"
    NOTE_CREATE = "note_create"
    NOTE_SEARCH = "note_search"
    NOTE_UPDATE = "note_update"
    CALENDAR_VIEW = "calendar_view"
    CALENDAR_CREATE = "calendar_create"
    CALENDAR_CANCEL = "calendar_cancel"
    EMAIL_SEARCH = "email_search"
    EMAIL_DRAFT = "email_draft"
    EMAIL_SEND = "email_send"
    WEB_SEARCH = "web_search"
    WEB_READ = "web_read"
    TERMINAL_RUN = "terminal_run"
    MEMORY_RECALL = "memory_recall"
    MEMORY_SAVE = "memory_save"
    MEMORY_FORGET = "memory_forget"
    PRIVACY_CONTROL = "privacy_control"
    ACTIVITY_REVIEW = "activity_review"
    UNKNOWN = "unknown"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class MemoryKind(StrEnum):
    SHORT_TERM = "short_term"
    PREFERENCE = "preference"
    FACT = "fact"
    NOTE_REF = "note_ref"
    TASK_STATE = "task_state"


class ProcessingLocation(StrEnum):
    LOCAL = "local"
    CLOUD = "cloud"
    NONE = "none"


class AuditAction(StrEnum):
    RUN_STARTED = "run.started"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"
    TOOL_INVOKED = "tool.invoked"
    TOOL_SUCCEEDED = "tool.succeeded"
    TOOL_FAILED = "tool.failed"
    PERMISSION_REQUESTED = "permission.requested"
    PERMISSION_GRANTED = "permission.granted"
    PERMISSION_DENIED = "permission.denied"
    PERMISSION_REVOKED = "permission.revoked"
    CONFIRMATION_REQUESTED = "confirmation.requested"
    CONFIRMATION_APPROVED = "confirmation.approved"
    CONFIRMATION_REJECTED = "confirmation.rejected"
    FILE_ACCESSED = "file.accessed"
    FILE_MODIFIED = "file.modified"
    FILE_DELETED = "file.deleted"
    EMAIL_DRAFTED = "email.drafted"
    EMAIL_SENT = "email.sent"
    CALENDAR_EVENT_CREATED = "calendar.event_created"
    CALENDAR_EVENT_CANCELLED = "calendar.event_cancelled"
    COMMAND_EXECUTED = "command.executed"
    URL_FETCHED = "url.fetched"
    MEMORY_WRITTEN = "memory.written"
    MEMORY_DELETED = "memory.deleted"
    SETTINGS_CHANGED = "settings.changed"
    CLOUD_REQUEST = "cloud.request"
    INJECTION_DETECTED = "security.injection_detected"
    POLICY_VIOLATION = "security.policy_violation"
    DATA_EXPORTED = "data.exported"
    DATA_PURGED = "data.purged"


class IntegrationStatus(StrEnum):
    READY = "ready"
    NOT_CONFIGURED = "not_configured"
    UNAVAILABLE = "unavailable"
    AUTH_REQUIRED = "auth_required"
    ERROR = "error"


class ErrorCode(StrEnum):
    """Stable machine-readable error codes returned in the API error envelope."""

    BAD_REQUEST = "BAD_REQUEST"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    UNAUTHORIZED = "UNAUTHORIZED"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    RATE_LIMITED = "RATE_LIMITED"
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
    INTERNAL_ERROR = "INTERNAL_ERROR"

    TOOL_NOT_FOUND = "TOOL_NOT_FOUND"
    TOOL_PERMISSION_DENIED = "TOOL_PERMISSION_DENIED"
    TOOL_INVALID_ARGUMENTS = "TOOL_INVALID_ARGUMENTS"
    TOOL_TIMEOUT = "TOOL_TIMEOUT"
    TOOL_EXECUTION_FAILED = "TOOL_EXECUTION_FAILED"
    TOOL_OUTPUT_TOO_LARGE = "TOOL_OUTPUT_TOO_LARGE"
    CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"

    PATH_NOT_ALLOWED = "PATH_NOT_ALLOWED"
    PATH_TRAVERSAL = "PATH_TRAVERSAL"
    COMMAND_NOT_ALLOWED = "COMMAND_NOT_ALLOWED"
    URL_NOT_ALLOWED = "URL_NOT_ALLOWED"
    SSRF_BLOCKED = "SSRF_BLOCKED"
    PROMPT_INJECTION_DETECTED = "PROMPT_INJECTION_DETECTED"
    POLICY_VIOLATION = "POLICY_VIOLATION"

    LLM_UNAVAILABLE = "LLM_UNAVAILABLE"
    LLM_INVALID_OUTPUT = "LLM_INVALID_OUTPUT"
    CLOUD_DISABLED = "CLOUD_DISABLED"
    STT_UNAVAILABLE = "STT_UNAVAILABLE"
    TTS_UNAVAILABLE = "TTS_UNAVAILABLE"
    INTEGRATION_UNAVAILABLE = "INTEGRATION_UNAVAILABLE"
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"
