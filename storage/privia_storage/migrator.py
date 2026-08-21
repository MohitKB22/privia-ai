"""A minimal, deterministic forward-only migration runner.

Migrations are plain ``.sql`` files named ``NNNN_description.sql``. Each file is
applied exactly once inside a transaction and recorded with its SHA-256, so a
migration that is edited after being applied is detected rather than silently
ignored.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text

from privia_shared.errors import ConfigurationError
from privia_shared.ids import utcnow_iso

from .engine import Database

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_NAME_RE = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    checksum    TEXT NOT NULL,
    applied_at  TEXT NOT NULL
)
"""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    path: Path
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()


def discover(directory: Path | None = None) -> list[Migration]:
    directory = directory or MIGRATIONS_DIR
    if not directory.is_dir():
        raise ConfigurationError(f"Migration directory is missing: {directory}")
    migrations: list[Migration] = []
    for path in sorted(directory.glob("*.sql")):
        match = _NAME_RE.match(path.name)
        if not match:
            raise ConfigurationError(
                f"Migration file name must be NNNN_description.sql: {path.name}"
            )
        migrations.append(
            Migration(
                version=int(match.group(1)),
                name=match.group(2),
                path=path,
                sql=path.read_text(encoding="utf-8"),
            )
        )
    versions = [m.version for m in migrations]
    if len(set(versions)) != len(versions):
        raise ConfigurationError("Duplicate migration version numbers detected.")
    return migrations


def applied_versions(db: Database) -> dict[int, str]:
    db.execute(_CREATE_TABLE)
    rows = db.fetch_all("SELECT version, checksum FROM schema_migrations ORDER BY version")
    return {int(r["version"]): str(r["checksum"]) for r in rows}


def current_version(db: Database) -> int:
    applied = applied_versions(db)
    return max(applied) if applied else 0


def migrate(db: Database, directory: Path | None = None) -> list[str]:
    """Apply every pending migration. Returns the names that were applied."""
    migrations = discover(directory)
    applied = applied_versions(db)

    for migration in migrations:
        recorded = applied.get(migration.version)
        if recorded is not None and recorded != migration.checksum:
            raise ConfigurationError(
                f"Migration {migration.version:04d}_{migration.name} was modified after it was "
                "applied. Migrations are immutable; add a new one instead.",
                details={"version": migration.version},
            )

    performed: list[str] = []
    for migration in migrations:
        if migration.version in applied:
            continue
        with db.connect() as conn:
            # SQLite's DDL is transactional, so a failure rolls the file back
            # as a unit. Statements are split on ';' at statement boundaries.
            for statement in _split_statements(migration.sql):
                conn.execute(text(statement))
            conn.execute(
                text(
                    "INSERT INTO schema_migrations (version, name, checksum, applied_at) "
                    "VALUES (:v, :n, :c, :t)"
                ),
                {
                    "v": migration.version,
                    "n": migration.name,
                    "c": migration.checksum,
                    "t": utcnow_iso(),
                },
            )
        performed.append(f"{migration.version:04d}_{migration.name}")
    return performed


def _split_statements(sql: str) -> list[str]:
    """Split a migration into statements, honouring ``BEGIN ... END;`` blocks.

    SQLite triggers contain semicolons inside their body, so a naive split on
    ``;`` corrupts them.
    """
    statements: list[str] = []
    buffer: list[str] = []
    depth = 0
    for raw_line in sql.splitlines():
        line = raw_line
        stripped = line.strip()
        if stripped.startswith("--") or not stripped:
            buffer.append(line)
            continue
        upper = stripped.upper()
        if re.search(r"\bBEGIN\b", upper) and not upper.startswith("--"):
            depth += 1
        buffer.append(line)
        if depth > 0:
            if re.match(r"^END\s*;", upper):
                depth -= 1
                if depth == 0:
                    statements.append("\n".join(buffer).strip())
                    buffer = []
            continue
        if stripped.endswith(";"):
            statements.append("\n".join(buffer).strip())
            buffer = []
    tail = "\n".join(buffer).strip()
    if tail and not tail.startswith("--"):
        statements.append(tail)
    return [s.rstrip(";").strip() for s in statements if s.strip().rstrip(";").strip()]


def reset(db: Database) -> None:
    """Drop every PRIVIA object. Used by ``make db-reset`` and by tests."""
    rows = db.fetch_all(
        "SELECT name, type FROM sqlite_master WHERE type IN ('table','view','trigger','index') "
        "AND name NOT LIKE 'sqlite_%'"
    )
    with db.connect() as conn:
        conn.execute(text("PRAGMA foreign_keys=OFF"))
        for row in rows:
            if row["type"] == "table":
                conn.execute(text(f'DROP TABLE IF EXISTS "{row["name"]}"'))
        conn.execute(text("PRAGMA foreign_keys=ON"))
