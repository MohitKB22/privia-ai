"""Integration health and configuration state."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from privia_shared.errors import BadRequestError

from ..deps import ContainerDep, RequestIdDep

router = APIRouter(prefix="/api/v1/integrations", tags=["integrations"])

#: Secrets the UI is allowed to set. Anything else is refused.
ALLOWED_SECRET_KEYS = frozenset(
    {"smtp_password", "imap_password", "openai_api_key", "anthropic_api_key"}
)


class SecretRequest(BaseModel):
    key: str = Field(max_length=64)
    value: str = Field(min_length=1, max_length=4096)


@router.get("", summary="Integration health")
async def list_integrations(container: ContainerDep) -> dict[str, Any]:
    infos = await container.providers.health()
    for info in infos:
        container.repositories.integrations.upsert(info)
    return {"integrations": [i.model_dump(mode="json") for i in infos]}


@router.get("/secrets", summary="Which credentials are stored (never their values)")
async def secrets(container: ContainerDep) -> dict[str, Any]:
    described = container.secrets.describe()
    described["settable_keys"] = sorted(ALLOWED_SECRET_KEYS)
    return described


@router.post("/secrets", summary="Store a credential")
async def set_secret(
    body: SecretRequest, container: ContainerDep, request_id: RequestIdDep
) -> dict[str, Any]:
    """Write a credential to the keychain or the encrypted local store.

    The value is never logged, never returned, and never written to the database.
    """
    key = body.key.strip().lower()
    if key not in ALLOWED_SECRET_KEYS:
        raise BadRequestError(
            f"'{key}' is not a credential PRIVIA manages.",
            details={"allowed": sorted(ALLOWED_SECRET_KEYS)},
        )
    reference = container.secrets.set(key, body.value)
    container.audit.record(
        "settings.changed",
        request_id=request_id,
        target=key,
        detail={"action": "secret_stored", "backend": reference.backend},
    )
    return {"stored": key, "backend": reference.backend}


@router.delete("/secrets/{key}", summary="Delete a stored credential")
async def delete_secret(
    key: str, container: ContainerDep, request_id: RequestIdDep
) -> dict[str, Any]:
    normalised = key.strip().lower()
    if normalised not in ALLOWED_SECRET_KEYS:
        raise BadRequestError(f"'{normalised}' is not a credential PRIVIA manages.")
    container.secrets.delete(normalised)
    container.audit.record(
        "settings.changed",
        request_id=request_id,
        target=normalised,
        detail={"action": "secret_deleted"},
    )
    return {"deleted": normalised}
