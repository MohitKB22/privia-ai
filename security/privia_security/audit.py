"""Append-only audit log.

Every permission decision, tool execution and side effect lands here. The log is
the user's answer to "what did it just do?" and it is written *before* the
answer is rendered, so a crash mid-action still leaves a trace.

The audit log never stores secrets, file contents, or email bodies. It stores
what happened, to what, and whether it succeeded.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from contextlib import suppress
from datetime import datetime
from typing import Any, Protocol

from privia_shared.domain import AuditEvent
from privia_shared.enums import AuditAction
from privia_shared.ids import audit_id, utcnow

from .redaction import redact_mapping, redact_text, truncate

MAX_TARGET_CHARS = 512
MAX_DETAIL_CHARS = 2000


class AuditSink(Protocol):
    """Anything that can persist an audit event."""

    def append(self, event: AuditEvent) -> str: ...


class InMemoryAuditSink:
    """Used by tests and by the CLI doctor."""

    def __init__(self, capacity: int = 5000) -> None:
        self.events: list[AuditEvent] = []
        self.capacity = capacity
        self._lock = threading.Lock()

    def append(self, event: AuditEvent) -> str:
        with self._lock:
            self.events.append(event)
            if len(self.events) > self.capacity:
                del self.events[: len(self.events) - self.capacity]
        return event.id

    def query(self, action: str | None = None, limit: int = 100) -> list[AuditEvent]:
        with self._lock:
            items = list(reversed(self.events))
        if action:
            items = [e for e in items if e.action == action]
        return items[:limit]


class AuditLogger:
    """Writes structured audit events to one or more sinks."""

    def __init__(
        self,
        sinks: Sequence[AuditSink] = (),
        *,
        on_event: Callable[[AuditEvent], None] | None = None,
    ) -> None:
        self._sinks: list[AuditSink] = list(sinks)
        self._on_event = on_event
        self._lock = threading.Lock()

    def add_sink(self, sink: AuditSink) -> None:
        with self._lock:
            self._sinks.append(sink)

    def record(
        self,
        action: AuditAction | str,
        *,
        outcome: str = "success",
        session_id: str | None = None,
        run_id: str | None = None,
        request_id: str | None = None,
        tool_name: str | None = None,
        target: str | None = None,
        actor: str = "user",
        detail: dict[str, Any] | None = None,
        timestamp: datetime | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            id=audit_id(),
            timestamp=timestamp or utcnow(),
            action=str(action),
            session_id=session_id,
            run_id=run_id,
            request_id=request_id,
            actor=actor,
            tool_name=tool_name,
            target=truncate(redact_text(target), MAX_TARGET_CHARS) if target else None,
            outcome=outcome,  # type: ignore[arg-type]
            detail=_clean_detail(detail or {}),
        )
        with self._lock:
            sinks = list(self._sinks)
        for sink in sinks:
            try:
                sink.append(event)
            except Exception:  # noqa: S112
                # One broken sink must not stop the others, and must not fail the
                # action being audited. Sinks are independent by design.
                continue
        if self._on_event is not None:
            # The live-activity callback is a UI convenience. A subscriber that
            # throws must not fail the action being audited.
            with suppress(Exception):
                self._on_event(event)
        return event

    # -- convenience helpers used across the code base ------------------------

    def tool_invoked(self, tool_name: str, target: str | None = None, **kw: Any) -> AuditEvent:
        return self.record(AuditAction.TOOL_INVOKED, tool_name=tool_name, target=target, **kw)

    def tool_succeeded(self, tool_name: str, duration_ms: int, **kw: Any) -> AuditEvent:
        detail = dict(kw.pop("detail", {}) or {})
        detail["duration_ms"] = duration_ms
        return self.record(AuditAction.TOOL_SUCCEEDED, tool_name=tool_name, detail=detail, **kw)

    def tool_failed(self, tool_name: str, error_code: str | None, **kw: Any) -> AuditEvent:
        detail = dict(kw.pop("detail", {}) or {})
        detail["error_code"] = error_code
        return self.record(
            AuditAction.TOOL_FAILED, tool_name=tool_name, outcome="failure", detail=detail, **kw
        )

    def permission_denied(self, scope: str, reason: str, **kw: Any) -> AuditEvent:
        return self.record(
            AuditAction.PERMISSION_DENIED,
            outcome="denied",
            target=scope,
            detail={"reason": reason},
            **kw,
        )

    def permission_granted(self, scope: str, **kw: Any) -> AuditEvent:
        return self.record(AuditAction.PERMISSION_GRANTED, target=scope, **kw)

    def injection_detected(
        self, source: str, flags: Sequence[str], score: int, **kw: Any
    ) -> AuditEvent:
        return self.record(
            AuditAction.INJECTION_DETECTED,
            outcome="denied",
            target=source,
            detail={"flags": list(flags), "score": score},
            **kw,
        )


def _clean_detail(detail: dict[str, Any]) -> dict[str, Any]:
    redacted = redact_mapping(detail)
    out: dict[str, Any] = {}
    for key, value in redacted.items():
        if isinstance(value, str):
            out[key] = truncate(value, MAX_DETAIL_CHARS)
        else:
            out[key] = value
    return out


class DatabaseAuditSink:
    """Adapter that persists events through an ``AuditRepository``."""

    def __init__(self, repository: Any) -> None:
        self.repository = repository

    def append(self, event: AuditEvent) -> str:
        return str(self.repository.append(event))
