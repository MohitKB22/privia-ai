"""SQLite access layer.

PRIVIA uses SQLAlchemy Core (not the ORM) so that every statement in the code
base is a visible, auditable piece of SQL. The database is a local file the
user owns; there is no server, no replication and no cloud sync.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.engine import Connection

from privia_shared.config import Settings
from privia_shared.errors import ConfigurationError

_PRAGMAS = (
    "PRAGMA journal_mode=WAL",
    "PRAGMA synchronous=NORMAL",
    "PRAGMA foreign_keys=ON",
    "PRAGMA busy_timeout=5000",
    "PRAGMA temp_store=MEMORY",
)


class Database:
    """Owns the SQLAlchemy engine and hands out short-lived connections."""

    def __init__(self, url: str, *, echo: bool = False) -> None:
        self.url = url
        self._lock = threading.Lock()
        connect_args: dict[str, Any] = {}
        if url.startswith("sqlite"):
            connect_args = {"check_same_thread": False, "timeout": 15.0}
        self.engine: Engine = create_engine(
            url,
            echo=echo,
            future=True,
            connect_args=connect_args,
            pool_pre_ping=True,
        )
        if url.startswith("sqlite"):
            self._install_sqlite_pragmas()

    def _install_sqlite_pragmas(self) -> None:
        @event.listens_for(self.engine, "connect")
        def _set_pragmas(dbapi_connection: Any, _record: Any) -> None:  # pragma: no cover
            if not isinstance(dbapi_connection, sqlite3.Connection):
                return
            cursor = dbapi_connection.cursor()
            try:
                for pragma in _PRAGMAS:
                    cursor.execute(pragma)
            finally:
                cursor.close()

    # -- connection helpers ---------------------------------------------------

    @contextmanager
    def connect(self) -> Iterator[Connection]:
        """A connection with an implicit transaction that commits on success."""
        with self.engine.begin() as conn:
            yield conn

    @contextmanager
    def read(self) -> Iterator[Connection]:
        with self.engine.connect() as conn:
            yield conn

    # -- convenience ----------------------------------------------------------

    def execute(self, sql: str, params: Mapping[str, Any] | None = None) -> None:
        with self.connect() as conn:
            conn.execute(text(sql), dict(params or {}))

    def fetch_one(self, sql: str, params: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
        with self.read() as conn:
            row = conn.execute(text(sql), dict(params or {})).mappings().first()
            return dict(row) if row is not None else None

    def fetch_all(self, sql: str, params: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        with self.read() as conn:
            rows = conn.execute(text(sql), dict(params or {})).mappings().all()
            return [dict(row) for row in rows]

    def scalar(self, sql: str, params: Mapping[str, Any] | None = None) -> Any:
        with self.read() as conn:
            return conn.execute(text(sql), dict(params or {})).scalar()

    def table_names(self) -> list[str]:
        rows = self.fetch_all("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        return [r["name"] for r in rows]

    def vacuum(self) -> None:
        with self.engine.connect() as conn:
            conn.exec_driver_sql("VACUUM")

    def dispose(self) -> None:
        self.engine.dispose()

    def size_bytes(self) -> int:
        path = sqlite_path_from_url(self.url)
        return path.stat().st_size if path and path.exists() else 0


def sqlite_path_from_url(url: str) -> Path | None:
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        return None
    raw = url[len(prefix) :]
    if raw in {"", ":memory:"}:
        return None
    return Path(raw)


def json_dumps(value: Any) -> str:
    """Deterministic JSON for storage and hashing."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def json_loads(raw: str | None, default: Any = None) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return default


_db_singleton: Database | None = None
_singleton_lock = threading.Lock()


def get_database(settings: Settings | None = None) -> Database:
    """Process-wide database handle."""
    global _db_singleton
    if _db_singleton is not None:
        return _db_singleton
    with _singleton_lock:
        if _db_singleton is None:
            if settings is None:
                from privia_shared.config import get_settings

                settings = get_settings()
            if not settings.database_url:
                raise ConfigurationError("DATABASE_URL is not configured.")
            settings.ensure_directories()
            _db_singleton = Database(settings.database_url)
    return _db_singleton


def set_database(db: Database | None) -> None:
    """Used by tests and by the application factory."""
    global _db_singleton
    _db_singleton = db
