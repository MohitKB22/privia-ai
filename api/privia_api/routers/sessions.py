"""Conversation sessions."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from privia_shared.errors import NotFoundError

from ..deps import ContainerDep

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])


class SessionCreate(BaseModel):
    title: str = Field(default="New conversation", max_length=120)


@router.get("", summary="List conversations")
async def list_sessions(
    container: ContainerDep, limit: int = Query(default=50, ge=1, le=200)
) -> dict[str, Any]:
    return {"sessions": container.repositories.sessions.list(limit)}


@router.post("", summary="Start a conversation")
async def create(body: SessionCreate, container: ContainerDep) -> dict[str, Any]:
    session_id = container.repositories.sessions.create(body.title)
    return {"session_id": session_id, "title": body.title}


@router.get("/{session_id}", summary="Read a conversation")
async def detail(
    session_id: str, container: ContainerDep, limit: int = Query(default=100, ge=1, le=500)
) -> dict[str, Any]:
    session = container.repositories.sessions.get(session_id)
    if session is None:
        raise NotFoundError(f"No session with id {session_id}.")
    return {
        "session": session,
        "messages": container.repositories.messages.history(session_id, limit),
        "runs": container.repositories.runs.recent(limit=50, session_id=session_id),
    }


@router.delete("/{session_id}", summary="Delete a conversation")
async def delete(session_id: str, container: ContainerDep) -> dict[str, Any]:
    if container.repositories.sessions.get(session_id) is None:
        raise NotFoundError(f"No session with id {session_id}.")
    container.repositories.sessions.delete(session_id)
    return {"deleted": session_id}
