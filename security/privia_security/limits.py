"""Rate limiting and size limits."""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field

from privia_shared.errors import PayloadTooLargeError, RateLimitedError, ToolOutputTooLargeError


@dataclass
class _Bucket:
    hits: deque[float] = field(default_factory=deque)


class RateLimiter:
    """Sliding-window rate limiter, keyed by an arbitrary string.

    Local-only software still needs this: a runaway agent loop or a malicious
    page that triggers repeated tool calls should hit a ceiling, not the disk.
    """

    def __init__(self, limit: int, window_seconds: float = 60.0) -> None:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        self.limit = limit
        self.window = window_seconds
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    def _prune(self, bucket: _Bucket, now: float) -> None:
        cutoff = now - self.window
        while bucket.hits and bucket.hits[0] < cutoff:
            bucket.hits.popleft()

    def check(self, key: str = "global", *, cost: int = 1) -> None:
        """Consume ``cost`` tokens or raise :class:`RateLimitedError`."""
        now = time.monotonic()
        with self._lock:
            bucket = self._buckets.setdefault(key, _Bucket())
            self._prune(bucket, now)
            if len(bucket.hits) + cost > self.limit:
                retry_after = (
                    max(0.0, self.window - (now - bucket.hits[0])) if bucket.hits else self.window
                )
                raise RateLimitedError(
                    f"Rate limit reached ({self.limit} per {int(self.window)}s).",
                    details={"key": key, "retry_after_seconds": round(retry_after, 2)},
                )
            for _ in range(cost):
                bucket.hits.append(now)

    def remaining(self, key: str = "global") -> int:
        now = time.monotonic()
        with self._lock:
            bucket = self._buckets.setdefault(key, _Bucket())
            self._prune(bucket, now)
            return max(0, self.limit - len(bucket.hits))

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._buckets.clear()
            else:
                self._buckets.pop(key, None)


class ConcurrencyLimiter:
    """Caps how many operations of a kind run at once."""

    def __init__(self, limit: int) -> None:
        self._semaphore = threading.BoundedSemaphore(limit)
        self.limit = limit

    def __enter__(self) -> ConcurrencyLimiter:
        acquired = self._semaphore.acquire(timeout=30)
        if not acquired:
            raise RateLimitedError("Too many operations are already running.")
        return self

    def __exit__(self, *exc: object) -> None:
        self._semaphore.release()


def enforce_payload_size(size_bytes: int, limit_bytes: int, what: str = "payload") -> None:
    if size_bytes > limit_bytes:
        raise PayloadTooLargeError(
            f"The {what} is {size_bytes:,} bytes, above the {limit_bytes:,} byte limit.",
            details={"size_bytes": size_bytes, "limit_bytes": limit_bytes},
        )


def clamp_output(text: str, limit_bytes: int, *, hard: bool = False) -> tuple[str, bool]:
    """Truncate ``text`` to ``limit_bytes`` UTF-8 bytes.

    Returns ``(text, truncated)``. With ``hard=True`` an oversized value raises
    instead of truncating, which is what tools that must return complete data do.
    """
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= limit_bytes:
        return text, False
    if hard:
        raise ToolOutputTooLargeError(
            f"Output is {len(encoded):,} bytes, above the {limit_bytes:,} byte limit.",
            details={"size_bytes": len(encoded), "limit_bytes": limit_bytes},
        )
    clipped = encoded[:limit_bytes].decode("utf-8", errors="ignore")
    return clipped + "\n... [output truncated at limit]", True


def clamp_list(items: list[object], limit: int) -> tuple[list[object], bool]:
    if len(items) <= limit:
        return items, False
    return items[:limit], True
