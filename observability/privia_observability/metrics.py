"""In-process metrics.

Counters and timers only, held in memory, exposed on a local endpoint. There is
no exporter and no push target: this is for the user's own observability screen,
not for anyone else's dashboard.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from collections.abc import Iterator
from contextlib import contextmanager

from privia_shared.observability import MetricSnapshot

#: How many samples are kept per timer for percentile estimation.
WINDOW = 512


class Metrics:
    def __init__(self) -> None:
        self._counters: dict[str, int] = defaultdict(int)
        self._timers: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=WINDOW))
        self._gauges: dict[str, float] = {}
        self._lock = threading.Lock()
        self._started = time.monotonic()

    def increment(self, name: str, amount: int = 1, **labels: str) -> None:
        with self._lock:
            self._counters[_key(name, labels)] += amount

    def observe(self, name: str, milliseconds: float, **labels: str) -> None:
        with self._lock:
            self._timers[_key(name, labels)].append(milliseconds)

    def gauge(self, name: str, value: float, **labels: str) -> None:
        with self._lock:
            self._gauges[_key(name, labels)] = value

    @contextmanager
    def timer(self, name: str, **labels: str) -> Iterator[None]:
        started = time.perf_counter()
        try:
            yield
        finally:
            self.observe(name, (time.perf_counter() - started) * 1000, **labels)

    def snapshot(self) -> MetricSnapshot:
        with self._lock:
            counters = dict(self._counters)
            timers = {name: _summarise(list(samples)) for name, samples in self._timers.items()}
            timers.update({f"gauge.{k}": {"value": v} for k, v in self._gauges.items()})
        return MetricSnapshot(counters=counters, timers=timers)

    def uptime_seconds(self) -> float:
        return time.monotonic() - self._started

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._timers.clear()
            self._gauges.clear()


def _key(name: str, labels: dict[str, str]) -> str:
    if not labels:
        return name
    rendered = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
    return f"{name}{{{rendered}}}"


def _summarise(samples: list[float]) -> dict[str, float]:
    if not samples:
        return {"count": 0, "sum_ms": 0.0, "p50": 0.0, "p95": 0.0, "max": 0.0}
    ordered = sorted(samples)
    return {
        "count": len(ordered),
        "sum_ms": round(sum(ordered), 2),
        "mean": round(sum(ordered) / len(ordered), 2),
        "p50": round(_percentile(ordered, 0.50), 2),
        "p95": round(_percentile(ordered, 0.95), 2),
        "max": round(ordered[-1], 2),
    }


def _percentile(ordered: list[float], fraction: float) -> float:
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return ordered[index]


_metrics = Metrics()


def get_metrics() -> Metrics:
    return _metrics
