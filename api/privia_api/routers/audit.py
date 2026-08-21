"""The audit log: what PRIVIA did, and when."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from privia_shared.ids import utcnow

from ..deps import ContainerDep

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])


@router.get("", summary="Query the audit log")
async def query(
    container: ContainerDep,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    action: str | None = Query(default=None, max_length=64),
    run_id: str | None = Query(default=None, max_length=64),
    session_id: str | None = Query(default=None, max_length=64),
    minutes: int | None = Query(default=None, ge=1, le=60 * 24 * 90),
) -> dict[str, Any]:
    since = utcnow() - timedelta(minutes=minutes) if minutes else None
    events = container.repositories.audit.query(
        limit=limit,
        offset=offset,
        action=action,
        run_id=run_id,
        session_id=session_id,
        since=since,
    )
    return {
        "count": len(events),
        "total": container.repositories.audit.count(),
        "events": [e.model_dump(mode="json") for e in events],
    }


@router.get("/runs", summary="Recent agent runs")
async def runs(
    container: ContainerDep,
    limit: int = Query(default=50, ge=1, le=200),
    session_id: str | None = Query(default=None),
) -> dict[str, Any]:
    return {"runs": container.repositories.runs.recent(limit=limit, session_id=session_id)}


@router.get("/runs/{run_id}", summary="One run in full")
async def run_detail(run_id: str, container: ContainerDep) -> dict[str, Any]:
    from privia_shared.errors import NotFoundError

    run = container.repositories.runs.get(run_id)
    if run is None:
        raise NotFoundError(f"No run with id {run_id}.")
    return {
        "run": run.model_dump(mode="json"),
        "tool_calls": container.repositories.tool_calls.for_run(run_id),
        "audit": [
            e.model_dump(mode="json")
            for e in container.repositories.audit.query(run_id=run_id, limit=200)
        ],
    }


@router.get("/stream", summary="Live activity feed")
async def stream(container: ContainerDep, request: Request) -> StreamingResponse:
    """Server-sent events, one per audit record, as they happen."""
    queue = container.subscribe()

    async def generate() -> AsyncIterator[str]:
        try:
            for event in container.recent_audit.query(limit=20)[::-1]:
                yield f"data: {json.dumps(event.model_dump(mode='json'), default=str)}\n\n"
            while not await request.is_disconnected():
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                yield f"data: {json.dumps(payload, default=str)}\n\n"
        finally:
            container.unsubscribe(queue)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"cache-control": "no-cache", "x-accel-buffering": "no"},
    )


@router.delete("", summary="Clear the audit log")
async def clear(container: ContainerDep) -> dict[str, Any]:
    """Deleting the audit log is itself audited, as the first new entry."""
    removed = container.repositories.audit.delete_all()
    container.audit.record("data.purged", target="audit_events", detail={"removed": removed})
    return {"deleted": removed}
