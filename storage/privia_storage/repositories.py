"""Repositories: the only place in PRIVIA that writes SQL.

Every repository takes a :class:`~privia_storage.engine.Database` so tests can
run against an isolated temporary database with zero patching.
"""

from __future__ import annotations

import builtins
import re
import struct
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta
from typing import Any

from privia_shared.agent import AgentRun
from privia_shared.domain import (
    AuditEvent,
    EmailAddress,
    EmailAttachment,
    EmailDraft,
    IntegrationInfo,
    MemoryRecord,
    Note,
)
from privia_shared.enums import (
    IntegrationStatus,
    MemoryKind,
    MessageRole,
    PermissionGrantState,
    Scope,
)
from privia_shared.errors import NotFoundError
from privia_shared.ids import (
    draft_id,
    memory_id,
    message_id,
    new_id,
    note_id,
    utcnow,
)
from privia_shared.ids import (
    session_id as new_session_id,
)
from privia_shared.permissions import PermissionGrant
from privia_shared.tools import ConfirmationRequest, ToolCall, ToolResult

from .engine import Database, json_dumps, json_loads

DEFAULT_USER_ID = "usr_local"

_IDENTIFIER_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


# Sites that interpolate into SQL carry linter suppressions. The
# invariant they rely on is enforced by the two helpers below: only column names
# and code-authored predicates are ever interpolated, every one is validated
# against a strict identifier pattern, and every *value* is a bound parameter.
#: Fixed clause suffixes. Keeping them out of the f-string means every
#: interpolated statement fits on one line, so a formatter can never move the
#: suppression comment away from the code it justifies.
_MEMORY_ORDER = "ORDER BY pinned DESC, updated_at DESC LIMIT :limit"
_AUDIT_ORDER = "ORDER BY timestamp DESC, rowid DESC LIMIT :limit OFFSET :offset"


def _assignments(columns: Iterable[str]) -> str:
    """Build a ``SET a = :a, b = :b`` fragment from column names.

    Values are always bound parameters; only the *column names* are
    interpolated. Every name is checked against a strict identifier pattern
    here, so the invariant "no user data ever reaches the SQL string" is
    enforced by code rather than asserted in a comment. Callers already pass
    literal or allowlisted names; this is the belt to that pair of braces.
    """
    names = list(columns)
    for name in names:
        if not _IDENTIFIER_RE.match(name):
            raise ValueError(f"Refusing to build SQL with the column name {name!r}.")
    return ", ".join(f"{name} = :{name}" for name in names)


def _where(clauses: Sequence[str]) -> str:
    """Build a ``WHERE`` fragment from literal, code-authored predicates."""
    for clause in clauses:
        if ";" in clause or "--" in clause:
            raise ValueError(f"Refusing to build SQL with the clause {clause!r}.")
    return f"WHERE {' AND '.join(clauses)}" if clauses else ""


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


class BaseRepository:
    def __init__(self, db: Database) -> None:
        self.db = db


# ---------------------------------------------------------------------------
# Users & sessions
# ---------------------------------------------------------------------------


class UserRepository(BaseRepository):
    def ensure_default(self, display_name: str = "You", timezone_name: str = "UTC") -> str:
        existing = self.db.fetch_one("SELECT id FROM users WHERE id = :id", {"id": DEFAULT_USER_ID})
        if existing:
            return DEFAULT_USER_ID
        now = utcnow().isoformat()
        self.db.execute(
            "INSERT INTO users (id, display_name, locale, timezone, created_at, updated_at) "
            "VALUES (:id, :dn, 'en', :tz, :now, :now)",
            {"id": DEFAULT_USER_ID, "dn": display_name, "tz": timezone_name, "now": now},
        )
        return DEFAULT_USER_ID

    def get(self, user_id: str = DEFAULT_USER_ID) -> dict[str, Any] | None:
        return self.db.fetch_one("SELECT * FROM users WHERE id = :id", {"id": user_id})

    def update(self, user_id: str = DEFAULT_USER_ID, **fields: Any) -> None:
        allowed = {"display_name", "locale", "timezone"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return
        assignments = _assignments(updates)
        updates["id"] = user_id
        updates["now"] = utcnow().isoformat()
        sql = f"UPDATE users SET {assignments}, updated_at = :now WHERE id = :id"  # noqa: S608  # nosec
        self.db.execute(sql, updates)


class SessionRepository(BaseRepository):
    def create(self, title: str = "New conversation", user_id: str = DEFAULT_USER_ID) -> str:
        sid = new_session_id()
        now = utcnow().isoformat()
        self.db.execute(
            "INSERT INTO sessions (id, user_id, title, created_at, updated_at) "
            "VALUES (:id, :uid, :title, :now, :now)",
            {"id": sid, "uid": user_id, "title": title, "now": now},
        )
        return sid

    def ensure(self, sid: str | None, user_id: str = DEFAULT_USER_ID) -> str:
        if sid:
            row = self.db.fetch_one("SELECT id FROM sessions WHERE id = :id", {"id": sid})
            if row:
                return sid
        return self.create(user_id=user_id)

    def get(self, sid: str) -> dict[str, Any] | None:
        return self.db.fetch_one("SELECT * FROM sessions WHERE id = :id", {"id": sid})

    def touch(self, sid: str) -> None:
        self.db.execute(
            "UPDATE sessions SET updated_at = :now WHERE id = :id",
            {"id": sid, "now": utcnow().isoformat()},
        )

    def rename(self, sid: str, title: str) -> None:
        self.db.execute(
            "UPDATE sessions SET title = :t, updated_at = :now WHERE id = :id",
            {"id": sid, "t": title[:120], "now": utcnow().isoformat()},
        )

    def list(self, limit: int = 50) -> builtins.list[dict[str, Any]]:
        return self.db.fetch_all(
            "SELECT s.*, (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) "
            "AS message_count FROM sessions s ORDER BY s.updated_at DESC LIMIT :limit",
            {"limit": limit},
        )

    def delete(self, sid: str) -> None:
        self.db.execute("DELETE FROM sessions WHERE id = :id", {"id": sid})

    def delete_all(self) -> int:
        count = int(self.db.scalar("SELECT COUNT(*) FROM sessions") or 0)
        self.db.execute("DELETE FROM sessions")
        return count


class MessageRepository(BaseRepository):
    def add(
        self,
        session_id: str,
        role: MessageRole,
        content: str,
        *,
        run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        mid = message_id()
        self.db.execute(
            "INSERT INTO messages (id, session_id, run_id, role, content, created_at, "
            "metadata_json) VALUES (:id, :sid, :rid, :role, :content, :now, :meta)",
            {
                "id": mid,
                "sid": session_id,
                "rid": run_id,
                "role": str(role),
                "content": content,
                "now": utcnow().isoformat(),
                "meta": json_dumps(metadata or {}),
            },
        )
        return mid

    def history(self, session_id: str, limit: int = 40) -> list[dict[str, Any]]:
        rows = self.db.fetch_all(
            "SELECT * FROM (SELECT *, rowid AS seq FROM messages WHERE session_id = :sid "
            "ORDER BY created_at DESC, rowid DESC LIMIT :limit) ORDER BY created_at ASC, seq ASC",
            {"sid": session_id, "limit": limit},
        )
        for row in rows:
            row.pop("seq", None)
            row["metadata"] = json_loads(row.pop("metadata_json", "{}"), {})
        return rows

    def count(self, session_id: str) -> int:
        return int(
            self.db.scalar(
                "SELECT COUNT(*) FROM messages WHERE session_id = :sid", {"sid": session_id}
            )
            or 0
        )


# ---------------------------------------------------------------------------
# Runs, tool calls, confirmations
# ---------------------------------------------------------------------------


class RunRepository(BaseRepository):
    def save(self, run: AgentRun) -> None:
        payload = run.model_dump(mode="json")
        self.db.execute(
            """
            INSERT INTO runs (id, session_id, request_id, input_text, intent, status, phase,
                              processing_location, model_used, response_text, duration_ms,
                              error_code, error, run_json, created_at)
            VALUES (:id, :sid, :req, :input, :intent, :status, :phase, :loc, :model, :resp,
                    :dur, :ecode, :err, :json, :created)
            ON CONFLICT(id) DO UPDATE SET
                intent = excluded.intent,
                status = excluded.status,
                phase = excluded.phase,
                processing_location = excluded.processing_location,
                model_used = excluded.model_used,
                response_text = excluded.response_text,
                duration_ms = excluded.duration_ms,
                error_code = excluded.error_code,
                error = excluded.error,
                run_json = excluded.run_json
            """,
            {
                "id": run.id,
                "sid": run.session_id,
                "req": run.request_id,
                "input": run.input_text,
                "intent": str(run.classification.intent),
                "status": str(run.status),
                "phase": str(run.phase),
                "loc": str(run.processing_location),
                "model": run.model_used,
                "resp": run.response_text,
                "dur": run.duration_ms,
                "ecode": run.error_code,
                "err": run.error,
                "json": json_dumps(payload),
                "created": run.timestamp.isoformat(),
            },
        )

    def get(self, run_id: str) -> AgentRun | None:
        row = self.db.fetch_one("SELECT run_json FROM runs WHERE id = :id", {"id": run_id})
        if not row:
            return None
        return AgentRun.model_validate(json_loads(row["run_json"], {}))

    def recent(self, limit: int = 50, session_id: str | None = None) -> list[dict[str, Any]]:
        if session_id:
            return self.db.fetch_all(
                "SELECT id, session_id, request_id, input_text, intent, status, phase, "
                "processing_location, model_used, duration_ms, created_at FROM runs "
                "WHERE session_id = :sid ORDER BY created_at DESC LIMIT :limit",
                {"sid": session_id, "limit": limit},
            )
        return self.db.fetch_all(
            "SELECT id, session_id, request_id, input_text, intent, status, phase, "
            "processing_location, model_used, duration_ms, created_at FROM runs "
            "ORDER BY created_at DESC LIMIT :limit",
            {"limit": limit},
        )

    def delete_all(self) -> int:
        count = int(self.db.scalar("SELECT COUNT(*) FROM runs") or 0)
        self.db.execute("DELETE FROM runs")
        return count


class ToolCallRepository(BaseRepository):
    def record_call(self, run_id: str, session_id: str, call: ToolCall) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO tool_calls (id, run_id, session_id, tool_name, "
            "arguments_json, risk_level, justification, requires_confirmation, created_at) "
            "VALUES (:id, :rid, :sid, :tool, :args, :risk, :just, :conf, :now)",
            {
                "id": call.id,
                "rid": run_id,
                "sid": session_id,
                "tool": call.tool_name,
                "args": json_dumps(call.arguments),
                "risk": str(call.risk_level),
                "just": call.justification,
                "conf": int(call.requires_confirmation),
                "now": utcnow().isoformat(),
            },
        )

    def record_result(self, run_id: str, result: ToolResult) -> None:
        self.db.execute(
            "INSERT INTO tool_results (id, call_id, run_id, tool_name, success, data_json, "
            "error, error_code, duration_ms, truncated, metadata_json, created_at) "
            "VALUES (:id, :cid, :rid, :tool, :ok, :data, :err, :code, :dur, :trunc, :meta, :now)",
            {
                "id": new_id("tr"),
                "cid": result.call_id,
                "rid": run_id,
                "tool": result.tool_name,
                "ok": int(result.success),
                "data": json_dumps(result.data) if result.data is not None else None,
                "err": result.error,
                "code": result.error_code,
                "dur": result.duration_ms,
                "trunc": int(result.truncated),
                "meta": json_dumps(result.metadata),
                "now": utcnow().isoformat(),
            },
        )

    def for_run(self, run_id: str) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            "SELECT c.id, c.tool_name, c.arguments_json, c.risk_level, c.created_at, "
            "r.success, r.error, r.error_code, r.duration_ms FROM tool_calls c "
            "LEFT JOIN tool_results r ON r.call_id = c.id WHERE c.run_id = :rid "
            "ORDER BY c.created_at",
            {"rid": run_id},
        )

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            "SELECT c.id, c.run_id, c.tool_name, c.risk_level, c.created_at, r.success, "
            "r.duration_ms, r.error_code FROM tool_calls c "
            "LEFT JOIN tool_results r ON r.call_id = c.id ORDER BY c.created_at DESC LIMIT :n",
            {"n": limit},
        )


class ConfirmationRepository(BaseRepository):
    def create(self, request: ConfirmationRequest, session_id: str, ttl_seconds: int = 900) -> None:
        now = utcnow()
        # Confirmation ids are deterministic for a given (session, tool, arguments),
        # so asking the same thing again must re-open the prompt rather than
        # collide on the primary key. Re-creating clears any previous answer,
        # which is what "ask me again" means.
        self.db.execute(
            "INSERT INTO confirmations (id, run_id, session_id, tool_name, payload_json, "
            "created_at, expires_at) VALUES (:id, :rid, :sid, :tool, :payload, :now, :exp) "
            "ON CONFLICT(id) DO UPDATE SET run_id = excluded.run_id, "
            "payload_json = excluded.payload_json, created_at = excluded.created_at, "
            "expires_at = excluded.expires_at, resolved = 0, approved = NULL, "
            "resolved_at = NULL",
            {
                "id": request.id,
                "rid": request.run_id,
                "sid": session_id,
                "tool": request.tool_name,
                "payload": json_dumps(request.model_dump(mode="json")),
                "now": now.isoformat(),
                "exp": (now + timedelta(seconds=ttl_seconds)).isoformat(),
            },
        )

    def get(self, confirmation_id: str) -> dict[str, Any] | None:
        row = self.db.fetch_one(
            "SELECT * FROM confirmations WHERE id = :id", {"id": confirmation_id}
        )
        if row:
            row["payload"] = json_loads(row["payload_json"], {})
        return row

    def resolve(self, confirmation_id: str, approved: bool) -> None:
        self.db.execute(
            "UPDATE confirmations SET resolved = 1, approved = :a, resolved_at = :now "
            "WHERE id = :id",
            {"id": confirmation_id, "a": int(approved), "now": utcnow().isoformat()},
        )

    def purge_expired(self) -> int:
        now = utcnow().isoformat()
        count = int(
            self.db.scalar(
                "SELECT COUNT(*) FROM confirmations WHERE resolved = 0 AND expires_at < :now",
                {"now": now},
            )
            or 0
        )
        self.db.execute(
            "DELETE FROM confirmations WHERE resolved = 0 AND expires_at < :now", {"now": now}
        )
        return count


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------


class PermissionRepository(BaseRepository):
    def upsert(self, grant: PermissionGrant, session_id: str | None = None) -> None:
        now = utcnow().isoformat()
        self.db.execute(
            """
            INSERT INTO permissions (id, session_id, scope, state, resources_json, session_only,
                                     granted_at, expires_at, note, created_at, updated_at)
            VALUES (:id, :sid, :scope, :state, :res, :so, :ga, :ea, :note, :now, :now)
            ON CONFLICT(scope, IFNULL(session_id,'')) DO UPDATE SET
                state = excluded.state,
                resources_json = excluded.resources_json,
                session_only = excluded.session_only,
                granted_at = excluded.granted_at,
                expires_at = excluded.expires_at,
                note = excluded.note,
                updated_at = excluded.updated_at
            """,
            {
                "id": new_id("perm"),
                "sid": session_id,
                "scope": str(grant.scope),
                "state": str(grant.state),
                "res": json_dumps(list(grant.resources)),
                "so": int(grant.session_only),
                "ga": _iso(grant.granted_at),
                "ea": _iso(grant.expires_at),
                "note": grant.note,
                "now": now,
            },
        )

    def get(self, scope: Scope, session_id: str | None = None) -> PermissionGrant | None:
        row = self.db.fetch_one(
            "SELECT * FROM permissions WHERE scope = :scope AND IFNULL(session_id,'') = :sid",
            {"scope": str(scope), "sid": session_id or ""},
        )
        if not row:
            return None
        return self._to_grant(row)

    def list(self, session_id: str | None = None) -> builtins.list[PermissionGrant]:
        if session_id is None:
            rows = self.db.fetch_all("SELECT * FROM permissions ORDER BY scope")
        else:
            rows = self.db.fetch_all(
                "SELECT * FROM permissions WHERE session_id IS NULL OR session_id = :sid "
                "ORDER BY scope",
                {"sid": session_id},
            )
        return [self._to_grant(r) for r in rows]

    def revoke(self, scope: Scope, session_id: str | None = None) -> None:
        self.db.execute(
            "UPDATE permissions SET state = 'denied', granted_at = NULL, updated_at = :now "
            "WHERE scope = :scope AND IFNULL(session_id,'') = :sid",
            {"scope": str(scope), "sid": session_id or "", "now": utcnow().isoformat()},
        )

    def clear_session_grants(self, session_id: str | None = None) -> int:
        if session_id:
            count = int(
                self.db.scalar(
                    "SELECT COUNT(*) FROM permissions WHERE session_id = :sid", {"sid": session_id}
                )
                or 0
            )
            self.db.execute("DELETE FROM permissions WHERE session_id = :sid", {"sid": session_id})
            return count
        count = int(self.db.scalar("SELECT COUNT(*) FROM permissions WHERE session_only = 1") or 0)
        self.db.execute("DELETE FROM permissions WHERE session_only = 1")
        return count

    def delete_all(self) -> int:
        count = int(self.db.scalar("SELECT COUNT(*) FROM permissions") or 0)
        self.db.execute("DELETE FROM permissions")
        return count

    @staticmethod
    def _to_grant(row: dict[str, Any]) -> PermissionGrant:
        return PermissionGrant(
            scope=Scope(row["scope"]),
            state=PermissionGrantState(row["state"]),
            resources=tuple(json_loads(row["resources_json"], []) or []),
            granted_at=_parse_dt(row.get("granted_at")),
            expires_at=_parse_dt(row.get("expires_at")),
            session_only=bool(row.get("session_only")),
            note=row.get("note"),
        )


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------


def pack_vector(vector: Sequence[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


def unpack_vector(blob: bytes) -> list[float]:
    count = len(blob) // 4
    return list(struct.unpack(f"<{count}f", blob[: count * 4]))


class MemoryRepository(BaseRepository):
    def add(
        self,
        kind: MemoryKind,
        content: str,
        *,
        tags: Sequence[str] = (),
        provenance: str = "user:explicit",
        session_id: str | None = None,
        pinned: bool = False,
    ) -> MemoryRecord:
        mid = memory_id()
        now = utcnow()
        self.db.execute(
            "INSERT INTO memories (id, kind, content, tags_json, provenance, session_id, "
            "pinned, use_count, created_at, updated_at) "
            "VALUES (:id, :kind, :content, :tags, :prov, :sid, :pin, 0, :now, :now)",
            {
                "id": mid,
                "kind": str(kind),
                "content": content,
                "tags": json_dumps(list(tags)),
                "prov": provenance,
                "sid": session_id,
                "pin": int(pinned),
                "now": now.isoformat(),
            },
        )
        return MemoryRecord(
            id=mid,
            kind=kind,
            content=content,
            tags=tuple(tags),
            provenance=provenance,
            session_id=session_id,
            created_at=now,
            updated_at=now,
            pinned=pinned,
        )

    def get(self, mid: str) -> MemoryRecord | None:
        row = self.db.fetch_one("SELECT * FROM memories WHERE id = :id", {"id": mid})
        return self._to_record(row) if row else None

    # `builtins.list` because the method below is itself named `list`, which
    # shadows the builtin inside this class body. Renaming the method would make
    # `repositories.memories.list()` read worse for the sake of the annotation.
    def list(
        self,
        *,
        kind: MemoryKind | None = None,
        session_id: str | None = None,
        limit: int = 200,
    ) -> builtins.list[MemoryRecord]:
        clauses: builtins.list[str] = []
        params: dict[str, Any] = {"limit": limit}
        if kind is not None:
            clauses.append("kind = :kind")
            params["kind"] = str(kind)
        if session_id is not None:
            clauses.append("session_id = :sid")
            params["sid"] = session_id
        where = _where(clauses)
        sql = f"SELECT * FROM memories {where} {_MEMORY_ORDER}"  # noqa: S608  # nosec
        rows = self.db.fetch_all(sql, params)
        return [self._to_record(r) for r in rows]

    def search_text(self, query: str, limit: int = 20) -> builtins.list[MemoryRecord]:
        rows = self.db.fetch_all(
            "SELECT * FROM memories WHERE content LIKE :q ESCAPE '\\' "
            "ORDER BY pinned DESC, updated_at DESC LIMIT :limit",
            {"q": f"%{_escape_like(query)}%", "limit": limit},
        )
        return [self._to_record(r) for r in rows]

    def update(self, mid: str, **fields: Any) -> MemoryRecord:
        record = self.get(mid)
        if record is None:
            raise NotFoundError(f"No memory with id {mid}.")
        updates: dict[str, Any] = {}
        if "content" in fields:
            updates["content"] = fields["content"]
        if "tags" in fields:
            updates["tags_json"] = json_dumps(list(fields["tags"]))
        if "pinned" in fields:
            updates["pinned"] = int(bool(fields["pinned"]))
        if not updates:
            return record
        assignments = _assignments(updates)
        updates.update({"id": mid, "now": utcnow().isoformat()})
        sql = f"UPDATE memories SET {assignments}, updated_at = :now WHERE id = :id"  # noqa: S608  # nosec
        self.db.execute(sql, updates)
        updated = self.get(mid)
        assert updated is not None
        return updated

    def mark_used(self, mid: str) -> None:
        self.db.execute(
            "UPDATE memories SET use_count = use_count + 1, last_used_at = :now WHERE id = :id",
            {"id": mid, "now": utcnow().isoformat()},
        )

    def delete(self, mid: str) -> bool:
        existing = self.db.fetch_one("SELECT id FROM memories WHERE id = :id", {"id": mid})
        if not existing:
            return False
        self.db.execute("DELETE FROM memories WHERE id = :id", {"id": mid})
        return True

    def delete_all(self, *, keep_pinned: bool = False) -> int:
        # `where` is one of two code-authored literals selected by a boolean.
        # No caller input reaches the statement.
        where = "WHERE pinned = 0" if keep_pinned else ""
        count_sql = f"SELECT COUNT(*) FROM memories {where}"  # noqa: S608  # nosec
        delete_sql = f"DELETE FROM memories {where}"  # noqa: S608  # nosec
        count = int(self.db.scalar(count_sql) or 0)
        self.db.execute(delete_sql)
        return count

    def count(self) -> int:
        return int(self.db.scalar("SELECT COUNT(*) FROM memories") or 0)

    # -- vectors --

    def set_vector(self, mid: str, model: str, vector: Sequence[float]) -> None:
        self.db.execute(
            "INSERT INTO memory_vectors (memory_id, model, dim, vector_blob, created_at) "
            "VALUES (:id, :model, :dim, :blob, :now) "
            "ON CONFLICT(memory_id) DO UPDATE SET model = excluded.model, dim = excluded.dim, "
            "vector_blob = excluded.vector_blob, created_at = excluded.created_at",
            {
                "id": mid,
                "model": model,
                "dim": len(vector),
                "blob": pack_vector(vector),
                "now": utcnow().isoformat(),
            },
        )

    def all_vectors(
        self, model: str | None = None
    ) -> builtins.list[tuple[str, builtins.list[float]]]:
        if model:
            rows = self.db.fetch_all(
                "SELECT memory_id, vector_blob FROM memory_vectors WHERE model = :m", {"m": model}
            )
        else:
            rows = self.db.fetch_all("SELECT memory_id, vector_blob FROM memory_vectors")
        return [(r["memory_id"], unpack_vector(r["vector_blob"])) for r in rows]

    def missing_vectors(self, model: str, limit: int = 200) -> builtins.list[MemoryRecord]:
        rows = self.db.fetch_all(
            "SELECT m.* FROM memories m LEFT JOIN memory_vectors v ON v.memory_id = m.id "
            "AND v.model = :model WHERE v.memory_id IS NULL LIMIT :limit",
            {"model": model, "limit": limit},
        )
        return [self._to_record(r) for r in rows]

    @staticmethod
    def _to_record(row: dict[str, Any]) -> MemoryRecord:
        return MemoryRecord(
            id=row["id"],
            kind=MemoryKind(row["kind"]),
            content=row["content"],
            tags=tuple(json_loads(row["tags_json"], []) or []),
            provenance=row["provenance"],
            session_id=row.get("session_id"),
            created_at=_parse_dt(row["created_at"]) or utcnow(),
            updated_at=_parse_dt(row["updated_at"]) or utcnow(),
            last_used_at=_parse_dt(row.get("last_used_at")),
            use_count=int(row.get("use_count") or 0),
            pinned=bool(row.get("pinned")),
        )


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------


class NoteRepository(BaseRepository):
    def create(
        self, title: str, body: str = "", tags: Sequence[str] = (), pinned: bool = False
    ) -> Note:
        nid = note_id()
        now = utcnow()
        self.db.execute(
            "INSERT INTO notes (id, title, body, tags_json, pinned, created_at, updated_at) "
            "VALUES (:id, :title, :body, :tags, :pin, :now, :now)",
            {
                "id": nid,
                "title": title,
                "body": body,
                "tags": json_dumps(list(tags)),
                "pin": int(pinned),
                "now": now.isoformat(),
            },
        )
        return Note(
            id=nid,
            title=title,
            body=body,
            tags=tuple(tags),
            created_at=now,
            updated_at=now,
            pinned=pinned,
        )

    def get(self, nid: str) -> Note | None:
        row = self.db.fetch_one("SELECT * FROM notes WHERE id = :id", {"id": nid})
        return self._to_note(row) if row else None

    def list(self, limit: int = 200) -> builtins.list[Note]:
        rows = self.db.fetch_all(
            "SELECT * FROM notes ORDER BY pinned DESC, updated_at DESC LIMIT :n", {"n": limit}
        )
        return [self._to_note(r) for r in rows]

    def search(self, query: str, limit: int = 25) -> builtins.list[Note]:
        """Full-text search, falling back to LIKE when the query is not valid FTS5."""
        cleaned = query.strip()
        if not cleaned:
            return self.list(limit=limit)
        try:
            rows = self.db.fetch_all(
                "SELECT n.* FROM notes_fts f JOIN notes n ON n.rowid = f.rowid "
                "WHERE notes_fts MATCH :q ORDER BY rank LIMIT :n",
                {"q": _fts_query(cleaned), "n": limit},
            )
        except Exception:
            rows = []
        if not rows:
            rows = self.db.fetch_all(
                "SELECT * FROM notes WHERE title LIKE :q ESCAPE '\\' OR body LIKE :q ESCAPE '\\' "
                "ORDER BY updated_at DESC LIMIT :n",
                {"q": f"%{_escape_like(cleaned)}%", "n": limit},
            )
        return [self._to_note(r) for r in rows]

    def update(self, nid: str, **fields: Any) -> Note:
        note = self.get(nid)
        if note is None:
            raise NotFoundError(f"No note with id {nid}.")
        updates: dict[str, Any] = {}
        if "title" in fields and fields["title"] is not None:
            updates["title"] = fields["title"]
        if "body" in fields and fields["body"] is not None:
            updates["body"] = fields["body"]
        if "tags" in fields and fields["tags"] is not None:
            updates["tags_json"] = json_dumps(list(fields["tags"]))
        if "pinned" in fields and fields["pinned"] is not None:
            updates["pinned"] = int(bool(fields["pinned"]))
        if not updates:
            return note
        assignments = _assignments(updates)
        updates.update({"id": nid, "now": utcnow().isoformat()})
        sql = f"UPDATE notes SET {assignments}, updated_at = :now WHERE id = :id"  # noqa: S608  # nosec
        self.db.execute(sql, updates)
        result = self.get(nid)
        assert result is not None
        return result

    def delete(self, nid: str) -> bool:
        if not self.db.fetch_one("SELECT id FROM notes WHERE id = :id", {"id": nid}):
            return False
        self.db.execute("DELETE FROM notes WHERE id = :id", {"id": nid})
        return True

    def count(self) -> int:
        return int(self.db.scalar("SELECT COUNT(*) FROM notes") or 0)

    @staticmethod
    def _to_note(row: dict[str, Any]) -> Note:
        return Note(
            id=row["id"],
            title=row["title"],
            body=row["body"],
            tags=tuple(json_loads(row["tags_json"], []) or []),
            pinned=bool(row.get("pinned")),
            created_at=_parse_dt(row["created_at"]) or utcnow(),
            updated_at=_parse_dt(row["updated_at"]) or utcnow(),
        )


def _fts_query(raw: str) -> str:
    """Turn free text into a safe FTS5 prefix query.

    Anything that is not alphanumeric is dropped, so user input can never inject
    FTS operators such as ``NEAR`` or unbalanced quotes.
    """
    tokens = [t for t in "".join(c if c.isalnum() else " " for c in raw).split() if t]
    if not tokens:
        return '""'
    return " OR ".join(f'"{t}"*' for t in tokens[:12])


# ---------------------------------------------------------------------------
# Email drafts
# ---------------------------------------------------------------------------


class EmailDraftRepository(BaseRepository):
    def create(
        self,
        to: Sequence[EmailAddress],
        subject: str,
        body: str,
        *,
        cc: Sequence[EmailAddress] = (),
        bcc: Sequence[EmailAddress] = (),
        in_reply_to: str | None = None,
        attachments: Sequence[EmailAttachment] = (),
    ) -> EmailDraft:
        did = draft_id()
        now = utcnow()
        self.db.execute(
            "INSERT INTO email_drafts (id, to_json, cc_json, bcc_json, subject, body, "
            "in_reply_to, attachments_json, status, created_at, updated_at) "
            "VALUES (:id, :to, :cc, :bcc, :subj, :body, :irt, :att, 'draft', :now, :now)",
            {
                "id": did,
                "to": json_dumps([a.model_dump() for a in to]),
                "cc": json_dumps([a.model_dump() for a in cc]),
                "bcc": json_dumps([a.model_dump() for a in bcc]),
                "subj": subject,
                "body": body,
                "irt": in_reply_to,
                "att": json_dumps([a.model_dump() for a in attachments]),
                "now": now.isoformat(),
            },
        )
        return EmailDraft(
            id=did,
            to=tuple(to),
            cc=tuple(cc),
            bcc=tuple(bcc),
            subject=subject,
            body=body,
            in_reply_to=in_reply_to,
            attachments=tuple(attachments),
            created_at=now,
            updated_at=now,
        )

    def get(self, did: str) -> EmailDraft | None:
        row = self.db.fetch_one("SELECT * FROM email_drafts WHERE id = :id", {"id": did})
        return self._to_draft(row) if row else None

    def list(self, status: str | None = None, limit: int = 100) -> builtins.list[EmailDraft]:
        if status:
            rows = self.db.fetch_all(
                "SELECT * FROM email_drafts WHERE status = :s ORDER BY updated_at DESC LIMIT :n",
                {"s": status, "n": limit},
            )
        else:
            rows = self.db.fetch_all(
                "SELECT * FROM email_drafts ORDER BY updated_at DESC LIMIT :n", {"n": limit}
            )
        return [self._to_draft(r) for r in rows]

    def update_body(self, did: str, subject: str | None, body: str | None) -> EmailDraft:
        draft = self.get(did)
        if draft is None:
            raise NotFoundError(f"No draft with id {did}.")
        self.db.execute(
            "UPDATE email_drafts SET subject = :s, body = :b, updated_at = :now WHERE id = :id",
            {
                "id": did,
                "s": subject if subject is not None else draft.subject,
                "b": body if body is not None else draft.body,
                "now": utcnow().isoformat(),
            },
        )
        result = self.get(did)
        assert result is not None
        return result

    def mark_sent(self, did: str) -> None:
        now = utcnow().isoformat()
        self.db.execute(
            "UPDATE email_drafts SET status = 'sent', sent_at = :now, updated_at = :now "
            "WHERE id = :id",
            {"id": did, "now": now},
        )

    def mark_failed(self, did: str) -> None:
        self.db.execute(
            "UPDATE email_drafts SET status = 'failed', updated_at = :now WHERE id = :id",
            {"id": did, "now": utcnow().isoformat()},
        )

    def delete(self, did: str) -> bool:
        if not self.db.fetch_one("SELECT id FROM email_drafts WHERE id = :id", {"id": did}):
            return False
        self.db.execute("DELETE FROM email_drafts WHERE id = :id", {"id": did})
        return True

    @staticmethod
    def _to_draft(row: dict[str, Any]) -> EmailDraft:
        def addrs(key: str) -> tuple[EmailAddress, ...]:
            return tuple(EmailAddress.model_validate(a) for a in (json_loads(row[key], []) or []))

        return EmailDraft(
            id=row["id"],
            to=addrs("to_json"),
            cc=addrs("cc_json"),
            bcc=addrs("bcc_json"),
            subject=row["subject"],
            body=row["body"],
            in_reply_to=row.get("in_reply_to"),
            attachments=tuple(
                EmailAttachment.model_validate(a)
                for a in (json_loads(row["attachments_json"], []) or [])
            ),
            status=row["status"],
            sent_at=_parse_dt(row.get("sent_at")),
            created_at=_parse_dt(row["created_at"]) or utcnow(),
            updated_at=_parse_dt(row["updated_at"]) or utcnow(),
        )


# ---------------------------------------------------------------------------
# Integrations, audit, settings
# ---------------------------------------------------------------------------


class IntegrationRepository(BaseRepository):
    def upsert(self, info: IntegrationInfo, config: dict[str, Any] | None = None) -> None:
        self.db.execute(
            "INSERT INTO integrations (name, family, provider, status, authenticated, "
            "capabilities_json, detail, checked_at, config_json) "
            "VALUES (:n, :f, :p, :s, :a, :c, :d, :t, :cfg) "
            "ON CONFLICT(name) DO UPDATE SET family = excluded.family, "
            "provider = excluded.provider, status = excluded.status, "
            "authenticated = excluded.authenticated, capabilities_json = excluded.capabilities_json, "
            "detail = excluded.detail, checked_at = excluded.checked_at",
            {
                "n": info.name,
                "f": info.family,
                "p": info.provider,
                "s": str(info.status),
                "a": int(info.authenticated),
                "c": json_dumps(list(info.capabilities)),
                "d": info.detail,
                "t": _iso(info.checked_at) or utcnow().isoformat(),
                "cfg": json_dumps(config or {}),
            },
        )

    def list(self) -> builtins.list[IntegrationInfo]:
        rows = self.db.fetch_all("SELECT * FROM integrations ORDER BY family, name")
        return [
            IntegrationInfo(
                name=r["name"],
                family=r["family"],
                provider=r["provider"],
                status=IntegrationStatus(r["status"]),
                capabilities=tuple(json_loads(r["capabilities_json"], []) or []),
                detail=r["detail"],
                authenticated=bool(r["authenticated"]),
                checked_at=_parse_dt(r.get("checked_at")),
            )
            for r in rows
        ]


class AuditRepository(BaseRepository):
    def append(self, event: AuditEvent) -> str:
        self.db.execute(
            "INSERT INTO audit_events (id, timestamp, action, session_id, run_id, request_id, "
            "actor, tool_name, target, outcome, detail_json) "
            "VALUES (:id, :ts, :action, :sid, :rid, :req, :actor, :tool, :target, :out, :detail)",
            {
                "id": event.id,
                "ts": event.timestamp.isoformat(),
                "action": event.action,
                "sid": event.session_id,
                "rid": event.run_id,
                "req": event.request_id,
                "actor": event.actor,
                "tool": event.tool_name,
                "target": event.target,
                "out": event.outcome,
                "detail": json_dumps(event.detail),
            },
        )
        return event.id

    def query(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        action: str | None = None,
        session_id: str | None = None,
        run_id: str | None = None,
        since: datetime | None = None,
    ) -> list[AuditEvent]:
        clauses: list[str] = []
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if action:
            clauses.append("action = :action")
            params["action"] = action
        if session_id:
            clauses.append("session_id = :sid")
            params["sid"] = session_id
        if run_id:
            clauses.append("run_id = :rid")
            params["rid"] = run_id
        if since:
            clauses.append("timestamp >= :since")
            params["since"] = since.isoformat()
        where = _where(clauses)
        sql = f"SELECT * FROM audit_events {where} {_AUDIT_ORDER}"  # noqa: S608  # nosec
        rows = self.db.fetch_all(sql, params)
        return [self._to_event(r) for r in rows]

    def count(self) -> int:
        return int(self.db.scalar("SELECT COUNT(*) FROM audit_events") or 0)

    def delete_all(self) -> int:
        count = self.count()
        self.db.execute("DELETE FROM audit_events")
        return count

    @staticmethod
    def _to_event(row: dict[str, Any]) -> AuditEvent:
        return AuditEvent(
            id=row["id"],
            timestamp=_parse_dt(row["timestamp"]) or utcnow(),
            action=row["action"],
            session_id=row.get("session_id"),
            run_id=row.get("run_id"),
            request_id=row.get("request_id"),
            actor=row.get("actor") or "user",
            tool_name=row.get("tool_name"),
            target=row.get("target"),
            outcome=row.get("outcome") or "success",
            detail=json_loads(row.get("detail_json"), {}) or {},
        )


class SettingsRepository(BaseRepository):
    """User-editable settings that override environment defaults at runtime."""

    def get(self, key: str, default: Any = None) -> Any:
        row = self.db.fetch_one("SELECT value_json FROM settings WHERE key = :k", {"k": key})
        if not row:
            return default
        return json_loads(row["value_json"], default)

    def set(self, key: str, value: Any) -> None:
        self.db.execute(
            "INSERT INTO settings (key, value_json, updated_at) VALUES (:k, :v, :now) "
            "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, "
            "updated_at = excluded.updated_at",
            {"k": key, "v": json_dumps(value), "now": utcnow().isoformat()},
        )

    def all(self) -> dict[str, Any]:
        rows = self.db.fetch_all("SELECT key, value_json FROM settings")
        return {r["key"]: json_loads(r["value_json"]) for r in rows}

    def delete(self, key: str) -> None:
        self.db.execute("DELETE FROM settings WHERE key = :k", {"k": key})


class Repositories:
    """Convenience aggregate so callers hold one object, not eleven."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self.users = UserRepository(db)
        self.sessions = SessionRepository(db)
        self.messages = MessageRepository(db)
        self.runs = RunRepository(db)
        self.tool_calls = ToolCallRepository(db)
        self.confirmations = ConfirmationRepository(db)
        self.permissions = PermissionRepository(db)
        self.memories = MemoryRepository(db)
        self.notes = NoteRepository(db)
        self.drafts = EmailDraftRepository(db)
        self.integrations = IntegrationRepository(db)
        self.audit = AuditRepository(db)
        self.settings = SettingsRepository(db)
