"""Identifier helpers.

PRIVIA uses prefixed, sortable identifiers so that a raw id in a log line is
self-describing (``run_01J...``) without needing a lookup.
"""

from __future__ import annotations

import secrets
import threading
import time
from datetime import datetime, timezone

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32, no I/L/O/U


def _encode(value: int, length: int) -> str:
    out = []
    for _ in range(length):
        out.append(_ALPHABET[value & 0x1F])
        value >>= 5
    return "".join(reversed(out))


_MAX_RANDOM = (1 << 80) - 1
_lock = threading.Lock()
_last_timestamp_ms = -1
_last_randomness = 0


def ulid() -> str:
    """A lexicographically sortable, monotonic 26-character identifier.

    Plain ULIDs only sort correctly across milliseconds: two generated in the
    same millisecond carry independent random suffixes and can come out in
    either order. PRIVIA sorts messages, runs and audit rows by id, and those
    are routinely created in the same millisecond, so the random component is
    made monotonic within a millisecond (the behaviour the ULID spec calls
    "monotonic mode").
    """
    global _last_timestamp_ms, _last_randomness
    with _lock:
        timestamp_ms = int(time.time() * 1000)
        if timestamp_ms == _last_timestamp_ms:
            randomness = _last_randomness + 1
            if randomness > _MAX_RANDOM:
                # Overflow within one millisecond is not reachable in practice;
                # borrowing a millisecond keeps the ordering guarantee anyway.
                timestamp_ms += 1
                randomness = secrets.randbits(79)
        elif timestamp_ms < _last_timestamp_ms:
            # The clock moved backwards. Keep issuing increasing ids rather than
            # emitting one that sorts before an id already handed out.
            timestamp_ms = _last_timestamp_ms
            randomness = _last_randomness + 1
        else:
            randomness = secrets.randbits(79)
        _last_timestamp_ms = timestamp_ms
        _last_randomness = randomness
    return _encode(timestamp_ms, 10) + _encode(randomness, 16)


def new_id(prefix: str) -> str:
    return f"{prefix}_{ulid()}"


def request_id() -> str:
    return new_id("req")


def session_id() -> str:
    return new_id("ses")


def run_id() -> str:
    return new_id("run")


def message_id() -> str:
    return new_id("msg")


def tool_call_id() -> str:
    return new_id("tc")


def memory_id() -> str:
    return new_id("mem")


def note_id() -> str:
    return new_id("note")


def event_id() -> str:
    return new_id("evt")


def audit_id() -> str:
    return new_id("aud")


def draft_id() -> str:
    return new_id("draft")


def utcnow() -> datetime:
    """Timezone-aware UTC now. Never use ``datetime.utcnow()`` in PRIVIA."""
    return datetime.now(timezone.utc)


def utcnow_iso() -> str:
    return utcnow().isoformat()
