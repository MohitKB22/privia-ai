"""Tool contracts.

The LLM never executes anything. It emits a :class:`ToolCall`, which a
deterministic runtime validates against the tool's declared JSON schema, checks
against the permission engine, and only then executes. The result comes back as
a :class:`ToolResult`.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator

from .base import PriviaModel
from .enums import RiskLevel, Scope
from .ids import tool_call_id

#: Maximum characters we accept for a model-authored justification string.
MAX_JUSTIFICATION_CHARS = 400


class RetryPolicy(PriviaModel):
    """How the runtime retries a failing tool."""

    max_attempts: int = Field(default=1, ge=1, le=5)
    backoff_seconds: float = Field(default=0.25, ge=0.0, le=30.0)
    backoff_multiplier: float = Field(default=2.0, ge=1.0, le=10.0)
    #: Only errors whose class name appears here are retried. Permission and
    #: validation failures are never retried.
    retry_on: tuple[str, ...] = ("ToolError", "IntegrationUnavailableError", "ToolTimeoutError")


class ToolSpec(PriviaModel):
    """Static declaration of a tool. Registered once at startup."""

    name: str = Field(pattern=r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
    family: str
    description: str = Field(min_length=10, max_length=600)
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    scopes: tuple[Scope, ...]
    risk_level: RiskLevel
    #: Confirmation is a property of the *tool*, not of the model's opinion.
    requires_confirmation: bool = False
    timeout_seconds: float = Field(default=20.0, gt=0, le=600)
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    #: Keys of the input that must never be written to logs or audit records.
    redact_input_keys: tuple[str, ...] = ()
    #: Human-readable summary template used in confirmation dialogs.
    confirmation_template: str | None = None
    #: Whether the tool's output may contain untrusted third-party content.
    returns_untrusted_content: bool = False
    audit_metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _family_matches(cls, v: str) -> str:
        return v


class ToolCall(PriviaModel):
    """A structured, validated request to run one tool."""

    id: str = Field(default_factory=tool_call_id)
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    risk_level: RiskLevel = RiskLevel.LOW
    justification: str = Field(default="", max_length=MAX_JUSTIFICATION_CHARS)
    requires_confirmation: bool = False

    @field_validator("justification")
    @classmethod
    def _trim(cls, v: str) -> str:
        return v.strip()[:MAX_JUSTIFICATION_CHARS]


class ToolResult(PriviaModel):
    """The outcome of a tool call. Always produced, even on failure."""

    call_id: str = ""
    tool_name: str = ""
    success: bool
    data: Any = None
    error: str | None = None
    error_code: str | None = None
    duration_ms: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)
    #: Set when the tool touched files/URLs/commands; surfaced in the UI so the
    #: user can always answer "what did it just touch?".
    accessed_resources: tuple[str, ...] = ()
    truncated: bool = False

    @classmethod
    def ok(cls, data: Any = None, **kw: Any) -> ToolResult:
        return cls(success=True, data=data, **kw)

    @classmethod
    def fail(cls, error: str, error_code: str | None = None, **kw: Any) -> ToolResult:
        return cls(success=False, error=error, error_code=error_code, **kw)


class ConfirmationRequest(PriviaModel):
    """Presented to the user before a high-impact action runs."""

    id: str
    run_id: str
    tool_name: str
    title: str
    summary: str
    risk_level: RiskLevel
    #: Field-by-field preview, e.g. {"To": "rahul@example.com", "Subject": "..."}
    details: dict[str, str] = Field(default_factory=dict)
    #: The exact resource the action will affect (absolute path, URL, event id).
    target: str | None = None
    destructive: bool = False
    expires_at: str | None = None


class ConfirmationResponse(PriviaModel):
    id: str
    approved: bool
    #: When true, remember the decision for this tool for the rest of the session.
    remember_for_session: bool = False
