"""Agent run models: the full, auditable record of one request."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from .base import PriviaModel
from .enums import AgentPhase, Intent, ProcessingLocation, RunStatus
from .ids import run_id, utcnow
from .permissions import PolicyResult
from .tools import ConfirmationRequest, ToolCall, ToolResult


class Entity(PriviaModel):
    """A span of the user's utterance with a resolved meaning."""

    type: str
    value: str
    normalized: str | None = None
    start: int | None = None
    end: int | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class Classification(PriviaModel):
    intent: Intent = Intent.UNKNOWN
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    entities: tuple[Entity, ...] = ()
    rationale: str = ""
    #: When the classifier is unsure it lists runners-up so PLAN can hedge.
    alternatives: tuple[Intent, ...] = ()


class PlanStep(PriviaModel):
    index: int
    description: str
    tool_name: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    depends_on: tuple[int, ...] = ()


class Plan(PriviaModel):
    steps: tuple[PlanStep, ...] = ()
    summary: str = ""
    #: Set when the assistant can answer without any tool at all.
    direct_answer: str | None = None


class PhaseRecord(PriviaModel):
    phase: AgentPhase
    started_at: datetime
    duration_ms: int = 0
    ok: bool = True
    detail: str = ""


class VerificationCheck(PriviaModel):
    name: str
    passed: bool
    detail: str = ""


class Verification(PriviaModel):
    passed: bool = True
    checks: tuple[VerificationCheck, ...] = ()

    @property
    def failures(self) -> tuple[VerificationCheck, ...]:
        return tuple(c for c in self.checks if not c.passed)


class AgentRun(PriviaModel):
    """One end-to-end pass of the agent graph. Persisted and auditable."""

    id: str = Field(default_factory=run_id)
    request_id: str
    session_id: str
    timestamp: datetime = Field(default_factory=utcnow)
    input_text: str
    status: RunStatus = RunStatus.PENDING
    phase: AgentPhase = AgentPhase.INPUT

    classification: Classification = Field(default_factory=Classification)
    plan: Plan = Field(default_factory=Plan)
    policy_results: tuple[PolicyResult, ...] = ()
    selected_tools: tuple[str, ...] = ()
    tool_calls: tuple[ToolCall, ...] = ()
    tool_results: tuple[ToolResult, ...] = ()
    verification: Verification = Field(default_factory=Verification)

    pending_confirmation: ConfirmationRequest | None = None
    response_text: str = ""
    processing_location: ProcessingLocation = ProcessingLocation.LOCAL
    model_used: str | None = None
    phases: tuple[PhaseRecord, ...] = ()
    accessed_resources: tuple[str, ...] = ()
    error: str | None = None
    error_code: str | None = None
    duration_ms: int = 0

    @property
    def succeeded(self) -> bool:
        return self.status is RunStatus.COMPLETED


class ChatRequest(PriviaModel):
    """Body of ``POST /api/v1/chat``."""

    message: str = Field(min_length=1, max_length=16_000)
    session_id: str | None = None
    #: Resolves a pending confirmation from a previous turn.
    confirmation_id: str | None = None
    confirm: bool | None = None
    #: Force a processing location for this turn (still subject to permissions).
    prefer: ProcessingLocation | None = None
    speak: bool = False


class ChatResponse(PriviaModel):
    run_id: str
    request_id: str
    session_id: str
    response: str
    status: RunStatus
    intent: Intent
    processing_location: ProcessingLocation
    model_used: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    tool_results: tuple[ToolResult, ...] = ()
    pending_confirmation: ConfirmationRequest | None = None
    accessed_resources: tuple[str, ...] = ()
    permission_prompt: dict[str, Any] | None = None
    duration_ms: int = 0
    audio_base64: str | None = None
