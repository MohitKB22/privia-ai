"""Migrations and repositories."""

from __future__ import annotations

from pathlib import Path

import pytest

from privia_shared.domain import AuditEvent, EmailAddress
from privia_shared.enums import MemoryKind, MessageRole, PermissionGrantState, Scope
from privia_shared.errors import ConfigurationError, NotFoundError
from privia_shared.ids import audit_id, utcnow
from privia_shared.permissions import PermissionGrant
from privia_storage.engine import Database, json_dumps, json_loads
from privia_storage.migrator import current_version, discover, migrate, reset
from privia_storage.repositories import Repositories, pack_vector, unpack_vector


def test_migrations_are_ordered_and_unique() -> None:
    migrations = discover()
    versions = [m.version for m in migrations]
    assert versions == sorted(versions)
    assert len(set(versions)) == len(versions)


def test_migrate_is_idempotent(tmp_path: Path) -> None:
    db = Database(f"sqlite:///{tmp_path}/m.db")
    first = migrate(db)
    assert first
    assert migrate(db) == []
    assert current_version(db) == len(first)
    db.dispose()


def test_every_required_table_exists(database: Database) -> None:
    tables = set(database.table_names())
    required = {
        "users",
        "sessions",
        "messages",
        "runs",
        "tool_calls",
        "tool_results",
        "permissions",
        "memories",
        "memory_vectors",
        "notes",
        "email_drafts",
        "integrations",
        "audit_events",
        "settings",
        "confirmations",
        "schema_migrations",
    }
    assert required <= tables


def test_editing_an_applied_migration_is_detected(tmp_path: Path) -> None:
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    target = migrations_dir / "0001_initial.sql"
    target.write_text("CREATE TABLE t (id TEXT PRIMARY KEY);")
    db = Database(f"sqlite:///{tmp_path}/x.db")
    migrate(db, migrations_dir)
    target.write_text("CREATE TABLE t (id TEXT PRIMARY KEY, extra TEXT);")
    with pytest.raises(ConfigurationError, match="modified after it was applied"):
        migrate(db, migrations_dir)
    db.dispose()


def test_bad_migration_filename_is_rejected(tmp_path: Path) -> None:
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "oops.sql").write_text("SELECT 1;")
    with pytest.raises(ConfigurationError, match="NNNN_description"):
        discover(migrations_dir)


def test_trigger_bodies_survive_statement_splitting(database: Database) -> None:
    """FTS triggers contain semicolons; a naive split would corrupt them."""
    triggers = database.fetch_all(
        "SELECT name FROM sqlite_master WHERE type='trigger' ORDER BY name"
    )
    assert {t["name"] for t in triggers} == {"notes_ai", "notes_ad", "notes_au"}


def test_foreign_keys_are_enforced(database: Database) -> None:
    import sqlalchemy

    with pytest.raises(sqlalchemy.exc.IntegrityError):
        database.execute(
            "INSERT INTO messages (id, session_id, role, content, created_at) "
            "VALUES ('m1', 'nonexistent', 'user', 'x', '2026-01-01T00:00:00')"
        )


def test_reset_drops_everything(tmp_path: Path) -> None:
    db = Database(f"sqlite:///{tmp_path}/r.db")
    migrate(db)
    reset(db)
    assert db.table_names() == []
    db.dispose()


def test_session_and_message_round_trip(repositories: Repositories) -> None:
    session_id = repositories.sessions.create("Test conversation")
    repositories.messages.add(session_id, MessageRole.USER, "hello")
    repositories.messages.add(session_id, MessageRole.ASSISTANT, "hi")
    history = repositories.messages.history(session_id)
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert repositories.messages.count(session_id) == 2
    repositories.sessions.rename(session_id, "Renamed")
    assert repositories.sessions.get(session_id)["title"] == "Renamed"


def test_message_history_is_chronological_within_the_same_timestamp(
    repositories: Repositories,
) -> None:
    session_id = repositories.sessions.create()
    for index in range(10):
        repositories.messages.add(session_id, MessageRole.USER, f"m{index}")
    contents = [m["content"] for m in repositories.messages.history(session_id)]
    assert contents == [f"m{i}" for i in range(10)]


def test_notes_full_text_search(repositories: Repositories) -> None:
    repositories.notes.create("Interview preparation", "STAR method and system design")
    repositories.notes.create("Grocery list", "milk, bread")
    assert [n.title for n in repositories.notes.search("interview")] == ["Interview preparation"]
    assert [n.title for n in repositories.notes.search("system design")] == [
        "Interview preparation"
    ]
    assert repositories.notes.search("nothingmatches") == []


def test_note_search_cannot_be_injected_with_fts_syntax(repositories: Repositories) -> None:
    repositories.notes.create("Safe", "content")
    for hostile in ['" OR NEAR(', "a*)(", '"" OR 1=1 --', "*"]:
        repositories.notes.search(hostile)


def test_note_update_and_delete(repositories: Repositories) -> None:
    note = repositories.notes.create("Title", "Body")
    updated = repositories.notes.update(note.id, title="New title", tags=["a"])
    assert updated.title == "New title"
    assert updated.tags == ("a",)
    assert repositories.notes.delete(note.id)
    assert not repositories.notes.delete(note.id)
    with pytest.raises(NotFoundError):
        repositories.notes.update(note.id, title="x")


def test_permission_upsert_is_idempotent(repositories: Repositories) -> None:
    for resources in (("/a",), ("/a", "/b")):
        repositories.permissions.upsert(
            PermissionGrant(
                scope=Scope.FILES_READ,
                state=PermissionGrantState.GRANTED,
                resources=resources,
                granted_at=utcnow(),
            )
        )
    grants = repositories.permissions.list()
    files_read = [g for g in grants if g.scope is Scope.FILES_READ]
    assert len(files_read) == 1
    assert files_read[0].resources == ("/a", "/b")


def test_memory_vector_round_trip(repositories: Repositories) -> None:
    record = repositories.memories.add(MemoryKind.FACT, "Likes espresso")
    vector = [0.1, -0.25, 0.5]
    repositories.memories.set_vector(record.id, "test-model", vector)
    stored = repositories.memories.all_vectors("test-model")
    assert stored[0][0] == record.id
    assert stored[0][1] == pytest.approx(vector, abs=1e-6)


def test_vector_packing_round_trip() -> None:
    values = [0.0, 1.0, -1.0, 0.333]
    assert unpack_vector(pack_vector(values)) == pytest.approx(values, abs=1e-6)


def test_memory_text_search_escapes_like_wildcards(repositories: Repositories) -> None:
    repositories.memories.add(MemoryKind.FACT, "100% complete")
    repositories.memories.add(MemoryKind.FACT, "unrelated")
    assert len(repositories.memories.search_text("100%")) == 1


def test_email_draft_lifecycle(repositories: Repositories) -> None:
    draft = repositories.drafts.create(
        [EmailAddress(address="a@b.com", name="A")], "Subject", "Body"
    )
    assert draft.status == "draft"
    repositories.drafts.mark_sent(draft.id)
    assert repositories.drafts.get(draft.id).status == "sent"
    repositories.drafts.mark_failed(draft.id)
    assert repositories.drafts.get(draft.id).status == "failed"


def test_audit_query_filters(repositories: Repositories) -> None:
    for index in range(5):
        repositories.audit.append(
            AuditEvent(
                id=audit_id(),
                timestamp=utcnow(),
                action="tool.invoked" if index % 2 else "tool.failed",
                run_id="run_a" if index < 3 else "run_b",
            )
        )
    assert repositories.audit.count() == 5
    assert len(repositories.audit.query(action="tool.invoked")) == 2
    assert len(repositories.audit.query(run_id="run_a")) == 3
    assert len(repositories.audit.query(limit=2)) == 2


def test_settings_repository(repositories: Repositories) -> None:
    repositories.settings.set("flag", True)
    repositories.settings.set("count", 42)
    assert repositories.settings.get("flag") is True
    assert repositories.settings.get("missing", "default") == "default"
    assert repositories.settings.all()["count"] == 42
    repositories.settings.delete("flag")
    assert repositories.settings.get("flag") is None


def test_json_helpers_are_deterministic() -> None:
    assert json_dumps({"b": 1, "a": 2}) == '{"a":2,"b":1}'
    assert json_loads("not json", {"fallback": True}) == {"fallback": True}
    assert json_loads(None, []) == []


def test_cascade_delete_removes_messages(repositories: Repositories) -> None:
    session_id = repositories.sessions.create()
    repositories.messages.add(session_id, MessageRole.USER, "x")
    repositories.sessions.delete(session_id)
    assert repositories.messages.count(session_id) == 0
