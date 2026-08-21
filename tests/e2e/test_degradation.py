"""Graceful degradation: no model, no network, no microphone, no permissions."""

from __future__ import annotations

from pathlib import Path

import pytest

from privia_llm.providers.ollama import OllamaProvider
from privia_llm.router import LLMRouter
from privia_shared.enums import ProcessingLocation

pytestmark = pytest.mark.e2e


async def test_no_local_model_falls_back_to_the_offline_planner(settings, permissions) -> None:
    """Ollama not running must degrade, not fail."""
    unreachable = OllamaProvider("llama3.1:8b", "http://127.0.0.1:1", timeout_seconds=0.5)
    router = LLMRouter(settings, permissions, local=unreachable, cloud=None)

    health = await router.local_health()
    assert not health.available
    assert "not running" in health.detail or "not reachable" in health.detail

    decision = await router.route(session_id="ses_1")
    assert decision.degraded
    assert decision.location is ProcessingLocation.LOCAL
    assert decision.provider.name == "offline-planner"
    await router.close()


def test_the_whole_product_works_with_no_model_installed(api_client, workspace: Path) -> None:
    api_client.post(
        "/api/v1/permissions",
        json={"scope": "files:read", "grant": True, "resources": [str(workspace)]},
    )
    response = api_client.post("/api/v1/chat", json={"message": "Find the project report"}).json()
    assert response["status"] == "completed"
    assert "project_report.md" in response["response"]
    assert response["model_used"].startswith("offline-planner")


def test_status_reports_the_degraded_model_honestly(api_client) -> None:
    status = api_client.get("/api/v1/status").json()
    local = status["models"]["local"]
    assert local["provider"] == "offline-planner"
    assert "Not a language model" in local["detail"]


async def test_web_search_offline_reports_it_cleanly(api_client) -> None:
    """The mock browser stands in for 'no network'."""
    api_client.post("/api/v1/permissions", json={"scope": "browser:read", "grant": True})
    result = api_client.post(
        "/api/v1/tools/execute",
        json={"tool_name": "browser.open_url", "arguments": {"url": "https://example.com/x"}},
    ).json()
    assert result["success"] is False
    assert (
        "cache" in (result["error"] or "").lower() or "offline" in (result["error"] or "").lower()
    )


def test_no_microphone_leaves_typing_available(api_client) -> None:
    status = api_client.get("/api/v1/voice/status").json()
    assert status["stt"]["status"] in {"not_configured", "unavailable", "error"}
    assert api_client.post("/api/v1/chat", json={"message": "hi"}).status_code == 200


def test_no_allowed_folders_produces_a_clear_message(settings, tmp_path) -> None:
    from fastapi.testclient import TestClient

    from privia_api.app import create_app
    from privia_api.container import build_container

    object.__setattr__(settings, "allowed_directories", "")
    with TestClient(
        create_app(
            settings, container=build_container(settings, offline=True, configure_logs=False)
        )
    ) as client:
        client.post("/api/v1/permissions", json={"scope": "files:read", "grant": True})
        response = client.post("/api/v1/chat", json={"message": "Find my resume"}).json()
        assert response["status"] == "completed"
        assert "folder" in response["response"].lower()

        health = client.get("/health").json()
        assert health["status"] == "degraded"
        assert "No folders allowed" in health["checks"]["note"]


def test_a_corrupt_secret_store_is_reported_not_crashed(settings, tmp_path) -> None:
    from privia_security.secrets import SecretStore
    from privia_shared.errors import ConfigurationError

    settings.ensure_directories()
    store = SecretStore(settings.data_dir)
    store.set("smtp_password", "value")
    (settings.data_dir / "privia_secrets.enc").write_text("{not valid json")

    with pytest.raises(ConfigurationError, match="corrupt"):
        SecretStore(settings.data_dir).get("smtp_password")


async def test_a_missing_calendar_directory_is_created_on_demand(providers, settings) -> None:
    import shutil

    shutil.rmtree(settings.calendar_dir, ignore_errors=True)
    assert not settings.calendar_dir.exists()
    info = await providers.calendar.health_check()
    assert info.status.value in {"ready", "not_configured"}
    assert settings.calendar_dir.exists()


def test_concurrent_requests_do_not_corrupt_the_database(api_client, workspace: Path) -> None:
    """SQLite in WAL mode plus short-lived connections must survive parallel use."""
    import concurrent.futures

    api_client.post(
        "/api/v1/permissions",
        json={"scope": "notes:write", "grant": True},
    )

    def create(index: int) -> int:
        return api_client.post(
            "/api/v1/notes", json={"title": f"Concurrent {index}", "body": "x"}
        ).status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(create, range(24)))

    assert all(status == 200 for status in statuses)
    assert api_client.get("/api/v1/notes").json()["count"] >= 24
