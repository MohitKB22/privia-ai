"""Shared fixtures.

Every test gets a throwaway data directory, a fresh migrated database and a
container wired with offline providers, so nothing touches the developer's real
PRIVIA installation and no test needs the network.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from privia_api.container import Container, build_container, build_tool_context
from privia_integrations.registry import ProviderSet, build_providers
from privia_security.audit import AuditLogger, InMemoryAuditSink
from privia_security.limits import RateLimiter
from privia_security.policy import PermissionEngine
from privia_shared.config import Settings
from privia_shared.enums import Scope
from privia_storage.engine import Database, set_database
from privia_storage.migrator import migrate
from privia_storage.repositories import Repositories
from privia_tools.context import ToolContext
from privia_tools.runtime import ToolRuntime
from privia_tools.tools import build_registry

ALL_SCOPES = tuple(Scope)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """A realistic little folder tree the file tools may touch."""
    root = tmp_path / "Documents"
    root.mkdir()
    (root / "project_report.md").write_text(
        "# Q3 Project Report\n\n"
        "Revenue grew 12 percent this quarter, driven by enterprise deals. "
        "Costs held flat against plan. The team shipped four features including the new "
        "ingest pipeline. Churn fell to 2 percent. Hiring continues for two backend roles. "
        "Revenue guidance for Q4 is unchanged.\n",
        encoding="utf-8",
    )
    (root / "my_resume.md").write_text("Hemant - engineer. Python, analytics.\n", encoding="utf-8")
    (root / "notes.txt").write_text("Buy milk.\n", encoding="utf-8")
    nested = root / "projects" / "analytics"
    nested.mkdir(parents=True)
    (nested / "main.py").write_text("print('hello')\n", encoding="utf-8")
    # A sensitive location that must stay invisible even though it is inside an
    # allowed root.
    secrets = root / ".ssh"
    secrets.mkdir()
    (secrets / "id_rsa").write_text("PRIVATE KEY\n", encoding="utf-8")
    return root


@pytest.fixture
def outside_dir(tmp_path: Path) -> Path:
    """A directory that is never allowed, for escape tests."""
    root = tmp_path / "elsewhere"
    root.mkdir()
    (root / "secret.txt").write_text("do not read me", encoding="utf-8")
    return root


@pytest.fixture
def settings(tmp_path: Path, workspace: Path) -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        log_level="WARNING",
        privia_data_dir=str(tmp_path / "data"),
        database_url=f"sqlite:///{tmp_path}/privia-test.db",
        allowed_directories=str(workspace),
        terminal_workspace_roots=str(workspace),
        calendar_ics_dir=str(tmp_path / "calendar"),
        local_llm_provider="heuristic",
        stt_provider="disabled",
        tts_provider="disabled",
        email_provider="local",
        rate_limit_per_minute=10_000,
    )


@pytest.fixture
def database(settings: Settings) -> Iterator[Database]:
    settings.ensure_directories()
    db = Database(settings.database_url)
    migrate(db)
    set_database(db)
    yield db
    db.dispose()
    set_database(None)


@pytest.fixture
def repositories(database: Database) -> Repositories:
    repos = Repositories(database)
    repos.users.ensure_default()
    return repos


@pytest.fixture
def audit_sink() -> InMemoryAuditSink:
    return InMemoryAuditSink()


@pytest.fixture
def audit(audit_sink: InMemoryAuditSink) -> AuditLogger:
    return AuditLogger([audit_sink])


@pytest.fixture
def permissions() -> PermissionEngine:
    return PermissionEngine()


@pytest.fixture
def providers(settings: Settings, repositories: Repositories) -> ProviderSet:
    return build_providers(settings, repositories, offline=True)


@pytest.fixture
def runtime(permissions: PermissionEngine, settings: Settings) -> ToolRuntime:
    return ToolRuntime(
        build_registry(), permissions, max_output_bytes=settings.max_tool_output_bytes
    )


@pytest.fixture
def context(
    settings: Settings,
    repositories: Repositories,
    providers: ProviderSet,
    permissions: PermissionEngine,
    audit: AuditLogger,
) -> ToolContext:
    session_id = repositories.sessions.create("test")
    return ToolContext(
        settings=settings,
        repositories=repositories,
        providers=providers,
        permissions=permissions,
        audit=audit,
        rate_limiter=RateLimiter(10_000),
        session_id=session_id,
        request_id="req_test",
        run_id="run_test",
    )


@pytest.fixture
def grant_all(permissions: PermissionEngine, workspace: Path) -> PermissionEngine:
    """Grant every scope, narrowed to the workspace for path scopes."""
    for scope in ALL_SCOPES:
        if scope.family == "files":
            permissions.grant(scope, resources=[str(workspace)])
        else:
            permissions.grant(scope)
    return permissions


@pytest.fixture
async def container(settings: Settings, database: Database):
    """A started container. Async so it shares the test's event loop."""
    built = build_container(settings, offline=True, configure_logs=False)
    await built.startup()
    yield built
    await built.shutdown()


@pytest.fixture
def api_client(settings: Settings, tmp_path: Path):
    """A TestClient with a fully wired offline container."""
    from fastapi.testclient import TestClient

    from privia_api.app import create_app

    built = build_container(settings, offline=True, configure_logs=False)
    app = create_app(settings, container=built)
    with TestClient(app) as client:
        client.container = built  # type: ignore[attr-defined]
        yield client


def grant(client: Any, scope: str, resources: list[str] | None = None) -> None:
    response = client.post(
        "/api/v1/permissions",
        json={"scope": scope, "grant": True, "resources": resources or []},
    )
    assert response.status_code == 200, response.text


def make_context(container: Container, session_id: str = "ses_test") -> ToolContext:
    return build_tool_context(container, session_id, "req_test")
