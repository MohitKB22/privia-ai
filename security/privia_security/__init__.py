"""PRIVIA security package.

Everything that decides *may this happen* lives here. The rule the whole product
rests on: the language model proposes, this package disposes.
"""

from __future__ import annotations

from .audit import AuditLogger, AuditSink, DatabaseAuditSink, InMemoryAuditSink
from .commands import (
    ALLOWLIST,
    BASE_ENV_KEYS,
    HARD_DENIED,
    SHELL_METACHARACTERS,
    CommandDecision,
    CommandGuard,
    CommandRule,
    sanitize_environment,
)
from .injection import (
    QUARANTINE_THRESHOLD,
    WARN_THRESHOLD,
    InjectionReport,
    scan,
    scan_user_input,
    strip_invisible,
    summarise_flags,
    wrap_untrusted,
)
from .limits import (
    ConcurrencyLimiter,
    RateLimiter,
    clamp_list,
    clamp_output,
    enforce_payload_size,
)
from .paths import (
    FORBIDDEN_PREFIXES,
    SENSITIVE_DIR_NAMES,
    SENSITIVE_FILE_NAMES,
    SENSITIVE_SUFFIXES,
    PathDecision,
    PathGuard,
    safe_join,
    sanitize_filename,
)
from .policy import (
    ALWAYS_PROMPT_SCOPES,
    CONFIRM_AT_OR_ABOVE,
    PermissionEngine,
    describe_scope,
)
from .redaction import (
    REDACTED,
    contains_secret,
    redact_arguments,
    redact_mapping,
    redact_text,
    redact_value,
    truncate,
)
from .secrets import (
    EncryptedFileBackend,
    EnvironmentBackend,
    KeyringBackend,
    SecretRef,
    SecretStore,
)
from .urls import (
    ALLOWED_PORTS,
    ALLOWED_SCHEMES,
    MAX_REDIRECTS,
    StaticResolver,
    SystemResolver,
    UrlDecision,
    UrlGuard,
    classify_address,
    redact_url,
)

__all__ = [
    # paths
    "PathGuard",
    "PathDecision",
    "safe_join",
    "sanitize_filename",
    "SENSITIVE_DIR_NAMES",
    "SENSITIVE_FILE_NAMES",
    "SENSITIVE_SUFFIXES",
    "FORBIDDEN_PREFIXES",
    # commands
    "CommandGuard",
    "CommandDecision",
    "CommandRule",
    "ALLOWLIST",
    "HARD_DENIED",
    "SHELL_METACHARACTERS",
    "BASE_ENV_KEYS",
    "sanitize_environment",
    # urls
    "UrlGuard",
    "UrlDecision",
    "SystemResolver",
    "StaticResolver",
    "classify_address",
    "redact_url",
    "ALLOWED_SCHEMES",
    "ALLOWED_PORTS",
    "MAX_REDIRECTS",
    # injection
    "scan",
    "scan_user_input",
    "wrap_untrusted",
    "strip_invisible",
    "summarise_flags",
    "InjectionReport",
    "WARN_THRESHOLD",
    "QUARANTINE_THRESHOLD",
    # policy
    "PermissionEngine",
    "describe_scope",
    "ALWAYS_PROMPT_SCOPES",
    "CONFIRM_AT_OR_ABOVE",
    # limits
    "RateLimiter",
    "ConcurrencyLimiter",
    "clamp_output",
    "clamp_list",
    "enforce_payload_size",
    # redaction
    "redact_text",
    "redact_value",
    "redact_mapping",
    "redact_arguments",
    "contains_secret",
    "truncate",
    "REDACTED",
    # secrets
    "SecretStore",
    "SecretRef",
    "KeyringBackend",
    "EncryptedFileBackend",
    "EnvironmentBackend",
    # audit
    "AuditLogger",
    "AuditSink",
    "InMemoryAuditSink",
    "DatabaseAuditSink",
]
