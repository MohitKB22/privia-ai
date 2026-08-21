"""The permission engine.

Capability model
----------------
A tool declares the :class:`~privia_shared.enums.Scope` values it needs. A grant
is a scope plus an optional resource narrowing (paths for file scopes, domains
for browser scopes, program names for terminal scopes). The engine answers
ALLOW / PROMPT / DENY and never guesses in the permissive direction.

Confirmation is separate from permission. Holding ``email:send`` means the user
is willing to consider sending mail; it never means "send this specific message".
"""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta
from pathlib import Path

from privia_shared.enums import (
    PermissionDecision,
    PermissionGrantState,
    RiskLevel,
    Scope,
)
from privia_shared.ids import utcnow
from privia_shared.permissions import PermissionGrant, PolicyRequest, PolicyResult

#: Scopes that always require an interactive prompt the first time, and which
#: can never be granted implicitly by configuration.
ALWAYS_PROMPT_SCOPES = frozenset(
    {
        Scope.EMAIL_SEND,
        Scope.FILES_DELETE,
        Scope.CALENDAR_DELETE,
        Scope.TERMINAL_EXEC,
        Scope.CLOUD_INFERENCE,
    }
)

#: Scopes whose grants are narrowed by filesystem paths.
PATH_SCOPES = frozenset({Scope.FILES_READ, Scope.FILES_WRITE, Scope.FILES_DELETE})
#: Scopes whose grants are narrowed by domain.
DOMAIN_SCOPES = frozenset({Scope.BROWSER_READ})
#: Scopes whose grants are narrowed by program name.
PROGRAM_SCOPES = frozenset({Scope.TERMINAL_EXEC})

#: Risk at or above which confirmation is mandatory regardless of tool metadata.
CONFIRM_AT_OR_ABOVE = RiskLevel.HIGH


class PermissionEngine:
    """Evaluates :class:`PolicyRequest` objects against the current grants."""

    def __init__(
        self,
        grants: Iterable[PermissionGrant] = (),
        *,
        default_decision: PermissionDecision = PermissionDecision.PROMPT,
    ) -> None:
        self._grants: dict[Scope, PermissionGrant] = {}
        for grant in grants:
            self._grants[grant.scope] = grant
        self.default_decision = default_decision
        #: Confirmations the user chose to remember for the session.
        self._remembered: set[str] = set()

    # -- grant management ----------------------------------------------------

    def load(self, grants: Iterable[PermissionGrant]) -> None:
        self._grants = {g.scope: g for g in grants}

    def grant(
        self,
        scope: Scope,
        *,
        resources: Sequence[str] = (),
        session_only: bool = False,
        ttl_seconds: int | None = None,
        note: str | None = None,
    ) -> PermissionGrant:
        now = utcnow()
        grant = PermissionGrant(
            scope=scope,
            state=PermissionGrantState.GRANTED,
            resources=tuple(resources),
            granted_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds) if ttl_seconds else None,
            session_only=session_only,
            note=note,
        )
        self._grants[scope] = grant
        return grant

    def deny(self, scope: Scope, note: str | None = None) -> PermissionGrant:
        grant = PermissionGrant(scope=scope, state=PermissionGrantState.DENIED, note=note)
        self._grants[scope] = grant
        return grant

    def revoke(self, scope: Scope) -> None:
        self._grants.pop(scope, None)

    def get(self, scope: Scope) -> PermissionGrant | None:
        return self._grants.get(scope)

    def all_grants(self) -> tuple[PermissionGrant, ...]:
        return tuple(self._grants.values())

    def remember_confirmation(self, tool_name: str) -> None:
        self._remembered.add(tool_name)

    def forget_confirmations(self) -> None:
        self._remembered.clear()

    # -- evaluation ----------------------------------------------------------

    def evaluate(self, request: PolicyRequest, *, now: datetime | None = None) -> PolicyResult:
        now = now or utcnow()
        missing: list[Scope] = []
        out_of_scope: list[str] = []
        denied_scope: Scope | None = None

        for scope in request.scopes:
            grant = self._grants.get(scope)
            if grant is None:
                missing.append(scope)
                continue
            if grant.state is PermissionGrantState.DENIED:
                denied_scope = scope
                break
            if not grant.is_active(now):
                missing.append(scope)
                continue
            unmatched = self._unmatched_resources(scope, grant, request.resources)
            if unmatched:
                out_of_scope.extend(unmatched)

        if denied_scope is not None:
            return PolicyResult(
                decision=PermissionDecision.DENY,
                reason=(
                    f"You previously denied '{denied_scope.value}'. Re-enable it in the Privacy "
                    "Center if you want this to work."
                ),
                missing_scopes=(denied_scope,),
            )

        if missing:
            return PolicyResult(
                decision=PermissionDecision.PROMPT,
                reason="PRIVIA needs your permission for: " + ", ".join(s.value for s in missing),
                missing_scopes=tuple(missing),
                requires_confirmation=self._needs_confirmation(request),
            )

        if out_of_scope:
            return PolicyResult(
                decision=PermissionDecision.PROMPT,
                reason=(
                    "The permission you granted does not cover: "
                    + ", ".join(sorted(set(out_of_scope))[:5])
                ),
                out_of_scope_resources=tuple(sorted(set(out_of_scope))),
                requires_confirmation=self._needs_confirmation(request),
            )

        return PolicyResult(
            decision=PermissionDecision.ALLOW,
            reason="All required permissions are granted.",
            requires_confirmation=self._needs_confirmation(request),
        )

    def _needs_confirmation(self, request: PolicyRequest) -> bool:
        if request.tool_name in self._remembered:
            return False
        if request.requires_confirmation:
            return True
        return request.risk_level >= CONFIRM_AT_OR_ABOVE

    # -- resource matching ---------------------------------------------------

    @staticmethod
    def _unmatched_resources(
        scope: Scope, grant: PermissionGrant, resources: Sequence[str]
    ) -> list[str]:
        if not resources:
            return []
        if not grant.resources:
            # An unnarrowed grant covers everything the *other* guards permit.
            # Path and command guards still apply independently.
            return []
        unmatched: list[str] = []
        for resource in resources:
            if not _resource_matches(scope, resource, grant.resources):
                unmatched.append(resource)
        return unmatched


def _resource_matches(scope: Scope, resource: str, allowed: Sequence[str]) -> bool:
    if scope in PATH_SCOPES:
        try:
            candidate = Path(resource).expanduser().resolve(strict=False)
        except (OSError, RuntimeError):
            return False
        for raw in allowed:
            try:
                root = Path(raw).expanduser().resolve(strict=False)
            except (OSError, RuntimeError):
                continue
            try:
                candidate.relative_to(root)
                return True
            except ValueError:
                continue
        return False

    if scope in DOMAIN_SCOPES:
        host = resource.lower().lstrip(".")
        for raw in allowed:
            pattern = raw.lower().lstrip(".")
            if host == pattern or host.endswith("." + pattern) or fnmatch.fnmatch(host, pattern):
                return True
        return False

    if scope in PROGRAM_SCOPES:
        program = resource.lower()
        return any(program == a.lower() or fnmatch.fnmatch(program, a.lower()) for a in allowed)

    return resource in allowed or any(fnmatch.fnmatch(resource, a) for a in allowed)


def describe_scope(scope: Scope) -> str:
    """Plain-language description used in permission prompts."""
    return _SCOPE_TEXT.get(scope, scope.value)


_SCOPE_TEXT: dict[Scope, str] = {
    Scope.FILES_READ: "read files in the folders you allow",
    Scope.FILES_WRITE: "create and edit files in the folders you allow",
    Scope.FILES_DELETE: "delete files (always asks first, one file at a time)",
    Scope.NOTES_READ: "read your notes",
    Scope.NOTES_WRITE: "create and edit your notes",
    Scope.CALENDAR_READ: "see your calendar events",
    Scope.CALENDAR_WRITE: "create and update calendar events",
    Scope.CALENDAR_DELETE: "cancel calendar events (always asks first)",
    Scope.EMAIL_READ: "read your email",
    Scope.EMAIL_DRAFT: "write email drafts (never sends them)",
    Scope.EMAIL_SEND: "send email (always asks first, every time)",
    Scope.BROWSER_READ: "fetch and read public web pages",
    Scope.TERMINAL_EXEC: "run allowlisted commands in your project folders",
    Scope.MEMORY_READ: "use what it remembers about you",
    Scope.MEMORY_WRITE: "remember new facts you approve",
    Scope.CLOUD_INFERENCE: "send this request to a cloud AI provider",
}
