"""Notes CRUD for the Notes screen."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from privia_shared.domain import Note
from privia_shared.errors import NotFoundError

from ..deps import ContainerDep

router = APIRouter(prefix="/api/v1/notes", tags=["notes"])


class NoteCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=200_000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    pinned: bool = False


class NoteUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    body: str | None = Field(default=None, max_length=200_000)
    tags: list[str] | None = Field(default=None, max_length=20)
    pinned: bool | None = None


@router.get("", summary="List or search notes")
async def list_notes(
    container: ContainerDep,
    query: str = Query(default="", max_length=200),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    notes = (
        container.repositories.notes.search(query, limit)
        if query
        else container.repositories.notes.list(limit)
    )
    return {"count": len(notes), "notes": [n.model_dump(mode="json") for n in notes]}


@router.post("", response_model=Note, summary="Create a note")
async def create(body: NoteCreate, container: ContainerDep) -> Note:
    return container.repositories.notes.create(body.title, body.body, body.tags, body.pinned)


@router.get("/{note_id}", response_model=Note, summary="Read a note")
async def read(note_id: str, container: ContainerDep) -> Note:
    note = container.repositories.notes.get(note_id)
    if note is None:
        raise NotFoundError(f"No note with id {note_id}.")
    return note


@router.put("/{note_id}", response_model=Note, summary="Update a note")
async def update(note_id: str, body: NoteUpdate, container: ContainerDep) -> Note:
    return container.repositories.notes.update(
        note_id, title=body.title, body=body.body, tags=body.tags, pinned=body.pinned
    )


@router.delete("/{note_id}", summary="Delete a note")
async def delete(note_id: str, container: ContainerDep) -> dict[str, Any]:
    if not container.repositories.notes.delete(note_id):
        raise NotFoundError(f"No note with id {note_id}.")
    return {"deleted": note_id}
