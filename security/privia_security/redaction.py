"""Redaction of secrets from anything that gets logged or audited."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

REDACTED = "***redacted***"

#: Keys whose values are never recorded, at any nesting depth.
SENSITIVE_KEYS = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "token",
        "api_key",
        "apikey",
        "access_token",
        "refresh_token",
        "authorization",
        "auth",
        "credential",
        "credentials",
        "private_key",
        "client_secret",
        "session_key",
        "cookie",
        "set-cookie",
        "smtp_password",
        "imap_password",
        "openai_api_key",
        "anthropic_api_key",
        "privia_api_token",
        "azure_openai_api_key",
        "google_client_secret",
    }
)

_VALUE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{16,}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("aws_key", re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{12,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("google_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b")),
    ("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{12,}")),
    ("basic_auth_url", re.compile(r"://[^/\s:@]+:[^/\s@]+@")),
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
)


def redact_text(value: str) -> str:
    """Replace credential-shaped substrings with a marker."""
    if not value:
        return value
    out = value
    for _name, pattern in _VALUE_PATTERNS:
        out = pattern.sub(REDACTED, out)
    return out


def redact_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 12:
        return "<max depth>"
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {k: _redact_pair(k, v, depth) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [redact_value(v, depth=depth + 1) for v in value]
    return value


def _redact_pair(key: Any, value: Any, depth: int) -> Any:
    if isinstance(key, str) and key.lower().replace("-", "_") in SENSITIVE_KEYS:
        return REDACTED if value not in (None, "", [], {}) else value
    return redact_value(value, depth=depth + 1)


def redact_mapping(data: Mapping[str, Any]) -> dict[str, Any]:
    return {k: _redact_pair(k, v, 0) for k, v in data.items()}


def redact_arguments(
    arguments: Mapping[str, Any], redact_keys: tuple[str, ...] = ()
) -> dict[str, Any]:
    """Redact tool arguments, honouring the tool's own declared secret keys."""
    out: dict[str, Any] = {}
    for key, value in arguments.items():
        if key in redact_keys:
            out[key] = REDACTED
        else:
            out[key] = _redact_pair(key, value, 0)
    return out


def contains_secret(value: str) -> bool:
    return any(pattern.search(value) for _name, pattern in _VALUE_PATTERNS)


def truncate(value: str, limit: int, suffix: str = "... [truncated]") -> str:
    if len(value) <= limit:
        return value
    return value[: max(0, limit - len(suffix))] + suffix
