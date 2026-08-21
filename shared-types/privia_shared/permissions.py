"""Permission and policy models (capability based)."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from .base import PriviaModel
from .enums import PermissionDecision, PermissionGrantState, RiskLevel, Scope


class PermissionGrant(PriviaModel):
    """A capability the user has granted, optionally narrowed to resources."""

    scope: Scope
    state: PermissionGrantState = PermissionGrantState.NOT_REQUESTED
    #: Absolute paths / domains / command names this grant is limited to.
    #: Empty means "the scope's configured default constraint applies".
    resources: tuple[str, ...] = ()
    granted_at: datetime | None = None
    expires_at: datetime | None = None
    #: True for grants that only last until the app restarts.
    session_only: bool = False
    note: str | None = None

    def is_active(self, now: datetime) -> bool:
        if self.state is not PermissionGrantState.GRANTED:
            return False
        return not (self.expires_at is not None and self.expires_at <= now)


class PolicyRequest(PriviaModel):
    """What the policy engine is being asked to authorise."""

    session_id: str
    tool_name: str
    scopes: tuple[Scope, ...]
    risk_level: RiskLevel
    requires_confirmation: bool = False
    #: Concrete resources the call will touch, resolved *before* the check.
    resources: tuple[str, ...] = ()


class PolicyResult(PriviaModel):
    decision: PermissionDecision
    reason: str
    missing_scopes: tuple[Scope, ...] = ()
    #: Scopes that exist but do not cover the requested resource.
    out_of_scope_resources: tuple[str, ...] = ()
    requires_confirmation: bool = False

    @property
    def allowed(self) -> bool:
        return self.decision is PermissionDecision.ALLOW


class PermissionPrompt(PriviaModel):
    """Sent to the UI when a scope must be requested interactively."""

    id: str
    session_id: str
    tool_name: str
    scopes: tuple[Scope, ...]
    resources: tuple[str, ...] = ()
    rationale: str
    risk_level: RiskLevel = RiskLevel.LOW


class PermissionUpdate(PriviaModel):
    """Body of ``POST /api/v1/permissions``."""

    scope: Scope
    grant: bool
    resources: tuple[str, ...] = ()
    session_only: bool = False
    ttl_seconds: int | None = Field(default=None, ge=60, le=60 * 60 * 24 * 365)
