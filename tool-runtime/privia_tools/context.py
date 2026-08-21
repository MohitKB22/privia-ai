"""Execution context handed to every tool.

A tool receives everything it needs through this object and reaches for nothing
global. That is what makes tools unit-testable without patching.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from privia_integrations.registry import ProviderSet
from privia_security.audit import AuditLogger
from privia_security.limits import RateLimiter
from privia_security.policy import PermissionEngine
from privia_shared.config import Settings
from privia_shared.enums import ProcessingLocation
from privia_storage.repositories import Repositories


@dataclass
class ToolContext:
    """Per-request execution context."""

    settings: Settings
    repositories: Repositories
    providers: ProviderSet
    permissions: PermissionEngine
    audit: AuditLogger
    rate_limiter: RateLimiter
    session_id: str
    request_id: str
    run_id: str = ""
    processing_location: ProcessingLocation = ProcessingLocation.LOCAL
    #: Confirmation ids the user approved in this turn.
    approved_confirmations: set[str] = field(default_factory=set)
    #: Resources touched during this run; surfaced in the UI.
    accessed_resources: list[str] = field(default_factory=list)
    #: Free-form scratch space shared between tools in one run.
    scratch: dict[str, Any] = field(default_factory=dict)
    #: Set when the caller wants tools to skip anything with side effects.
    dry_run: bool = False

    def note_resource(self, resource: str) -> None:
        if resource and resource not in self.accessed_resources:
            self.accessed_resources.append(resource)

    def child(self, **overrides: Any) -> ToolContext:
        data = {f: getattr(self, f) for f in self.__dataclass_fields__}
        data.update(overrides)
        return ToolContext(**data)
