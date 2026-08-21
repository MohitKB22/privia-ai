"""Memory inspection and control."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from privia_shared.domain import MemoryRecord
from privia_shared.enums import AuditAction, MemoryKind
from privia_shared.errors import NotFoundError

from ..deps import ContainerDep, RequestIdDep

router = APIRouter(prefix="/api/v1/memory", tags=["memory"])


class RememberRequest(BaseModel):
    content: str = Field(min_length=2, max_length=2000)
    kind: MemoryKind = MemoryKind.FACT
    tags: list[str] = Field(default_factory=list, max_length=10)
    pinned: bool = False


@router.get("", summary="List or search memories")
async def list_memories(
    container: ContainerDep,
    query: str = Query(default="", max_length=300),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    if query:
        hits = await container.memory.search_scored(query, limit=limit)
        records = [h.record for h in hits]
    else:
        records = container.repositories.memories.list(limit=limit)
    return {
        "count": len(records),
        "enabled": container.settings.memory_enabled,
        "memories": [r.model_dump(mode="json") for r in records],
    }


@router.get("/stats", summary="Memory statistics")
async def stats(container: ContainerDep) -> dict[str, Any]:
    return await container.memory.stats()


@router.post("", response_model=MemoryRecord, summary="Remember something")
async def remember(
    body: RememberRequest, container: ContainerDep, request_id: RequestIdDep
) -> MemoryRecord:
    record = await container.memory.remember(
        body.content, kind=body.kind, tags=body.tags, pinned=body.pinned
    )
    container.audit.record(
        AuditAction.MEMORY_WRITTEN,
        target=record.id,
        request_id=request_id,
        detail={"kind": str(record.kind)},
    )
    return record


@router.delete("/{memory_id}", summary="Forget one memory")
async def forget(
    memory_id: str, container: ContainerDep, request_id: RequestIdDep
) -> dict[str, Any]:
    if not await container.memory.forget(memory_id):
        raise NotFoundError(f"No memory with id {memory_id}.")
    container.audit.record(AuditAction.MEMORY_DELETED, target=memory_id, request_id=request_id)
    return {"deleted": memory_id}


@router.post("/clear", summary="Forget everything")
async def clear(
    container: ContainerDep,
    request_id: RequestIdDep,
    keep_pinned: bool = Query(default=False),
) -> dict[str, Any]:
    deleted = await container.memory.forget_all(keep_pinned=keep_pinned)
    container.audit.record(
        AuditAction.MEMORY_DELETED,
        target="all",
        request_id=request_id,
        detail={"deleted": deleted, "kept_pinned": keep_pinned},
    )
    return {"deleted": deleted}


@router.post("/reindex", summary="Rebuild the semantic index")
async def reindex(container: ContainerDep) -> dict[str, Any]:
    indexed = await container.memory.reindex(limit=1000)
    return {"indexed": indexed, "model": container.embedder.model}
