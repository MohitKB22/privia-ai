"""Structured logging with redaction built in.

Two properties matter more than features here:

* **Nothing sensitive is ever written.** Every field passes through
  :mod:`privia_security.redaction` before it reaches a handler, so a careless
  ``logger.info("x", token=...)`` cannot leak a credential.
* **Logs stay local.** The only handlers are stderr and a rotating file in the
  PRIVIA data directory. There is no remote sink and no way to configure one.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import sys
import threading
from contextlib import suppress
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from privia_security.redaction import redact_mapping, redact_text
from privia_shared.ids import utcnow_iso

#: Correlation ids for the current task, set by the API middleware.
current_request_id: ContextVar[str] = ContextVar("privia_request_id", default="")
current_session_id: ContextVar[str] = ContextVar("privia_session_id", default="")
current_run_id: ContextVar[str] = ContextVar("privia_run_id", default="")

#: Fields that are dropped outright rather than redacted, because their
#: presence at all is a mistake.
FORBIDDEN_FIELDS = frozenset({"file_contents", "email_body", "page_text", "stdout_full"})

_configured = False
_lock = threading.Lock()

# Until configure_logging() runs, PRIVIA is a library. A NullHandler stops
# Python's lastResort handler from printing raw records to stderr, which would
# otherwise interleave with command output (privia-doctor, privia-migrate).
logging.getLogger("privia").addHandler(logging.NullHandler())


class JsonFormatter(logging.Formatter):
    """One JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": utcnow_iso(),
            "level": record.levelname,
            "event": record.getMessage(),
            "component": record.name,
        }
        extra = getattr(record, "privia_fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info).splitlines()[-1][:300]
        return json.dumps(payload, default=str, ensure_ascii=False)


class HumanFormatter(logging.Formatter):
    """Compact, readable output for a developer's terminal."""

    COLOURS = {
        "DEBUG": "\033[38;5;245m",
        "INFO": "\033[38;5;39m",
        "WARNING": "\033[38;5;214m",
        "ERROR": "\033[38;5;203m",
        "CRITICAL": "\033[38;5;197m",
    }
    RESET = "\033[0m"

    def __init__(self, colour: bool = True) -> None:
        super().__init__()
        self.colour = colour and sys.stderr.isatty()

    def format(self, record: logging.LogRecord) -> str:
        extra = getattr(record, "privia_fields", {}) or {}
        request_id = extra.get("request_id", "")
        suffix = " ".join(
            f"{k}={_short(v)}"
            for k, v in extra.items()
            if k not in ("request_id", "session_id", "run_id")
        )
        prefix = f"[{request_id[-6:]}] " if request_id else ""
        line = f"{record.levelname:<7} {prefix}{record.getMessage()}"
        if suffix:
            line = f"{line}  {suffix}"
        if self.colour:
            colour = self.COLOURS.get(record.levelname, "")
            return f"{colour}{line}{self.RESET}"
        return line


class StructuredLogger:
    """Thin wrapper that redacts, adds correlation ids, and never raises."""

    def __init__(self, component: str = "privia") -> None:
        self._logger = logging.getLogger(component)
        self.component = component

    def bind(self, component: str) -> StructuredLogger:
        return StructuredLogger(f"{self.component}.{component}")

    def _emit(self, level: int, event: str, **fields: Any) -> None:
        try:
            clean = redact_mapping({k: v for k, v in fields.items() if k not in FORBIDDEN_FIELDS})
            for key, value in (
                ("request_id", current_request_id.get()),
                ("session_id", current_session_id.get()),
                ("run_id", current_run_id.get()),
            ):
                if value and key not in clean:
                    clean[key] = value
            self._logger.log(level, redact_text(event), extra={"privia_fields": clean})
        except Exception:  # noqa: S110
            # Logging is observability, not behaviour. A formatting bug or a full
            # disk must never turn a working request into a failed one, and there
            # is nowhere safe to report a logging failure to from inside a logger.
            pass

    def debug(self, event: str, **fields: Any) -> None:
        self._emit(logging.DEBUG, event, **fields)

    def info(self, event: str, **fields: Any) -> None:
        self._emit(logging.INFO, event, **fields)

    def warning(self, event: str, **fields: Any) -> None:
        self._emit(logging.WARNING, event, **fields)

    def error(self, event: str, **fields: Any) -> None:
        self._emit(logging.ERROR, event, **fields)

    def exception(self, event: str, **fields: Any) -> None:
        # Same reasoning as _emit: logging is observability, never behaviour.
        with suppress(Exception):
            self._logger.exception(
                redact_text(event), extra={"privia_fields": redact_mapping(fields)}
            )


def configure_logging(
    level: str = "INFO",
    *,
    log_dir: Path | None = None,
    json_output: bool = False,
    max_bytes: int = 5 * 1024 * 1024,
    backups: int = 3,
) -> StructuredLogger:
    """Install handlers once. Safe to call repeatedly."""
    global _configured
    with _lock:
        root = logging.getLogger("privia")
        if _configured:
            root.setLevel(level)
            return StructuredLogger()
        root.setLevel(level)
        root.propagate = False
        for handler in list(root.handlers):
            root.removeHandler(handler)

        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(JsonFormatter() if json_output else HumanFormatter())
        root.addHandler(console)

        if log_dir is not None:
            try:
                log_dir.mkdir(parents=True, exist_ok=True)
                file_handler = logging.handlers.RotatingFileHandler(
                    log_dir / "privia.log",
                    maxBytes=max_bytes,
                    backupCount=backups,
                    encoding="utf-8",
                )
                file_handler.setFormatter(JsonFormatter())
                root.addHandler(file_handler)
            except OSError:
                root.warning("Log directory is not writable; logging to stderr only.")

        # Third-party loggers stay quiet unless something goes wrong.
        for noisy in ("httpx", "httpcore", "uvicorn.access", "sqlalchemy.engine"):
            logging.getLogger(noisy).setLevel(logging.WARNING)

        _configured = True
        return StructuredLogger()


def get_logger(component: str = "privia") -> StructuredLogger:
    return StructuredLogger(component if component.startswith("privia") else f"privia.{component}")


def reset_logging() -> None:
    """Used by tests."""
    global _configured
    with _lock:
        _configured = False


def _short(value: Any, limit: int = 60) -> str:
    text = str(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."
