"""Health, status and metrics."""

from __future__ import annotations

import asyncio
import platform
import sys
from typing import Any

from fastapi import APIRouter

from privia_shared import __version__
from privia_shared.domain import HealthReport
from privia_storage.migrator import current_version

from ..deps import ContainerDep

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthReport, summary="Liveness probe")
async def health(container: ContainerDep) -> HealthReport:
    """Cheap liveness check. Never touches the network."""
    checks: dict[str, Any] = {}
    status = "ok"
    try:
        container.database.scalar("SELECT 1")
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error: {type(exc).__name__}"
        status = "error"
    checks["tools"] = len(container.registry)
    checks["allowed_directories"] = len(container.providers.path_guard.roots)
    if not container.providers.path_guard.roots:
        checks["note"] = "No folders allowed yet; file tools will refuse every path."
        status = "degraded" if status == "ok" else status
    return HealthReport(
        status=status,  # type: ignore[arg-type]
        version=__version__,
        uptime_seconds=round(container.metrics.uptime_seconds(), 1),
        checks=checks,
    )


@router.get("/api/v1/status", summary="Full runtime status")
async def status(container: ContainerDep) -> dict[str, Any]:
    """Everything the desktop client needs to render its status bar."""
    local, cloud, integrations = await asyncio.gather(
        container.router.local_health(),
        container.router.cloud_health(),
        container.providers.health(),
        return_exceptions=False,
    )
    stt, tts = await asyncio.gather(container.stt.health_check(), container.tts.health_check())
    return {
        "version": __version__,
        "app_env": container.settings.app_env,
        "uptime_seconds": round(container.metrics.uptime_seconds(), 1),
        "python": sys.version.split()[0],
        "platform": platform.system(),
        "database": {
            "path": str(container.settings.database_path or ":memory:"),
            "schema_version": current_version(container.database),
            "size_bytes": container.database.size_bytes(),
        },
        "models": {
            "local": local.model_dump(mode="json"),
            "cloud": cloud.model_dump(mode="json") if cloud else None,
            "embeddings": {
                "model": container.embedder.model,
                "dimensions": container.embedder.dimensions,
                "local": not container.embedder.sends_data_off_device,
            },
        },
        "speech": {"stt": stt.model_dump(mode="json"), "tts": tts.model_dump(mode="json")},
        "integrations": [i.model_dump(mode="json") for i in integrations],
        "privacy": {
            "cloud_processing_enabled": container.settings.cloud_processing_enabled,
            "memory_enabled": container.settings.memory_enabled,
            "telemetry_enabled": container.settings.telemetry_enabled,
            "data_leaving_device": bool(
                container.settings.cloud_processing_enabled and container.settings.cloud_ready()
            ),
        },
        "tools": len(container.registry),
        "warnings": container.startup_warnings,
    }


@router.get("/api/v1/metrics", summary="Local metrics snapshot")
async def metrics(container: ContainerDep) -> dict[str, Any]:
    """In-process metrics. Local only: there is no exporter."""
    snapshot = container.metrics.snapshot()
    return {
        "uptime_seconds": round(container.metrics.uptime_seconds(), 1),
        "counters": snapshot.counters,
        "timers": snapshot.timers,
        "collected_at": snapshot.collected_at.isoformat(),
    }
