"""The Privacy Center: what PRIVIA knows, where it runs, and how to erase it."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

from privia_shared.domain import PrivacyState
from privia_shared.enums import AuditAction, PermissionGrantState
from privia_shared.errors import BadRequestError
from privia_shared.ids import utcnow_iso

from ..deps import ContainerDep, RequestIdDep

router = APIRouter(prefix="/api/v1/privacy", tags=["privacy"])


class PrivacyUpdate(BaseModel):
    cloud_processing: bool | None = None
    memory_enabled: bool | None = None
    telemetry_enabled: bool | None = None
    cloud_consent: bool | None = None
    data_retention_days: int | None = None


@router.get("", response_model=PrivacyState, summary="Current privacy posture")
async def state(container: ContainerDep) -> PrivacyState:
    settings = container.settings
    local = await container.router.local_health()
    integrations = await container.providers.health()
    stt = await container.stt.health_check()
    tts = await container.tts.health_check()
    grants = container.permissions.all_grants()
    recent = container.repositories.audit.query(limit=25)
    overrides = container.repositories.settings.all()

    return PrivacyState(
        local_processing=True,
        cloud_processing=bool(settings.cloud_processing_enabled),
        cloud_provider=settings.cloud_llm_provider or None,
        cloud_consent_given=bool(overrides.get("cloud_consent", False)),
        current_llm=local,
        current_embedding_model=container.embedder.model,
        stt_available=stt.status.value == "ready",
        tts_available=tts.status.value == "ready",
        telemetry_enabled=settings.telemetry_enabled,
        memory_enabled=settings.memory_enabled,
        data_leaving_device=bool(settings.cloud_processing_enabled and settings.cloud_ready()),
        data_retention_days=overrides.get("data_retention_days"),
        allowed_directories=tuple(str(p) for p in container.providers.path_guard.roots),
        terminal_roots=tuple(str(p) for p in container.providers.command_guard.workspace_roots),
        integrations=tuple(integrations),
        grants=tuple(
            {
                "scope": g.scope.value,
                "state": str(g.state),
                "resources": list(g.resources),
                "granted": g.state is PermissionGrantState.GRANTED,
            }
            for g in grants
        ),
        recent_activity=tuple(recent),
        database_path=str(settings.database_path or ":memory:"),
    )


@router.post("", summary="Change the privacy settings")
async def update(
    body: PrivacyUpdate, container: ContainerDep, request_id: RequestIdDep
) -> dict[str, Any]:
    changed: dict[str, Any] = {}
    settings = container.settings

    if body.cloud_processing is not None:
        if body.cloud_processing and not settings.cloud_llm_provider:
            raise BadRequestError(
                "No cloud provider is configured. Set CLOUD_LLM_PROVIDER and an API key first."
            )
        if body.cloud_processing and not settings.cloud_api_key:
            raise BadRequestError(f"No API key is configured for {settings.cloud_llm_provider}.")
        container.repositories.settings.set("cloud_processing_enabled", body.cloud_processing)
        object.__setattr__(settings, "cloud_processing_enabled", body.cloud_processing)
        changed["cloud_processing_enabled"] = body.cloud_processing
        container.router.invalidate_health()

    for field, key in (
        (body.memory_enabled, "memory_enabled"),
        (body.telemetry_enabled, "telemetry_enabled"),
        (body.cloud_consent, "cloud_consent"),
    ):
        if field is not None:
            container.repositories.settings.set(key, field)
            if hasattr(settings, key):
                object.__setattr__(settings, key, field)
            changed[key] = field

    if body.data_retention_days is not None:
        if body.data_retention_days < 0:
            raise BadRequestError("Retention cannot be negative.")
        container.repositories.settings.set("data_retention_days", body.data_retention_days)
        changed["data_retention_days"] = body.data_retention_days

    if changed:
        container.audit.record(AuditAction.SETTINGS_CHANGED, request_id=request_id, detail=changed)
    return {"changed": changed}


@router.get("/export", summary="Export everything PRIVIA stores about you")
async def export(container: ContainerDep, request_id: RequestIdDep) -> dict[str, Any]:
    """A complete, portable copy of the local data. Secrets are never included."""
    repositories = container.repositories
    payload = {
        "exported_at": utcnow_iso(),
        "version": "1.0.0",
        "user": repositories.users.get(),
        "sessions": repositories.sessions.list(limit=1000),
        "messages": [
            m
            for session in repositories.sessions.list(limit=1000)
            for m in repositories.messages.history(session["id"], limit=1000)
        ],
        "memories": [m.model_dump(mode="json") for m in repositories.memories.list(limit=10_000)],
        "notes": [n.model_dump(mode="json") for n in repositories.notes.list(limit=10_000)],
        "drafts": [d.model_dump(mode="json") for d in repositories.drafts.list(limit=10_000)],
        "runs": repositories.runs.recent(limit=1000),
        "audit": [e.model_dump(mode="json") for e in repositories.audit.query(limit=10_000)],
        "settings": repositories.settings.all(),
        "permissions": [
            {"scope": g.scope.value, "state": str(g.state), "resources": list(g.resources)}
            for g in repositories.permissions.list()
        ],
        "note": "Credentials are stored in the OS keychain or an encrypted file and are "
        "deliberately excluded from this export.",
    }
    container.audit.record(
        AuditAction.DATA_EXPORTED,
        request_id=request_id,
        detail={"bytes": len(json.dumps(payload, default=str))},
    )
    return payload


@router.post("/purge", summary="Delete local history")
async def purge(
    container: ContainerDep,
    request_id: RequestIdDep,
    conversations: bool = Query(default=True),
    memories: bool = Query(default=False),
    audit_log: bool = Query(default=False),
    keep_pinned: bool = Query(default=True),
) -> dict[str, Any]:
    """Erase local data. Each category is opt-in so nothing goes by accident."""
    repositories = container.repositories
    removed: dict[str, int] = {}
    if conversations:
        removed["runs"] = repositories.runs.delete_all()
        removed["sessions"] = repositories.sessions.delete_all()
    if memories:
        removed["memories"] = await container.memory.forget_all(keep_pinned=keep_pinned)
    if audit_log:
        removed["audit_events"] = repositories.audit.delete_all()
    container.audit.record(AuditAction.DATA_PURGED, request_id=request_id, detail=removed)
    container.database.vacuum()
    return {"deleted": removed}
