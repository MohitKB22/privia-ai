"""Structured log and metric records."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from .base import PriviaModel
from .ids import utcnow


class LogRecord(PriviaModel):
    timestamp: datetime = Field(default_factory=utcnow)
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    event: str
    request_id: str | None = None
    session_id: str | None = None
    run_id: str | None = None
    component: str = "privia"
    duration_ms: float | None = None
    fields: dict[str, Any] = Field(default_factory=dict)


class MetricSnapshot(PriviaModel):
    counters: dict[str, int] = Field(default_factory=dict)
    #: name -> {count, sum_ms, p50, p95, max}
    timers: dict[str, dict[str, float]] = Field(default_factory=dict)
    collected_at: datetime = Field(default_factory=utcnow)
