"""Notes adapter backed by the local SQLite database (with FTS5 search)."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from privia_shared.domain import IntegrationInfo, Note
from privia_shared.errors import NotFoundError
from privia_storage.repositories import NoteRepository

from ..base import NotesProvider


class LocalNotesProvider(NotesProvider):
    name = "local"
    display_name = "Local notes"

    def __init__(self, repository: NoteRepository) -> None:
        self.repository = repository

    def capabilities(self) -> tuple[str, ...]:
        return ("create", "read", "search", "update", "tag", "delete", "summarize")

    async def health_check(self) -> IntegrationInfo:
        try:
            count = await asyncio.to_thread(self.repository.count)
        except Exception as exc:
            return self.errored(f"The notes table is unreadable: {exc}")
        return self.ok(f"{count} note(s) stored locally")

    async def create(
        self, title: str, body: str = "", tags: Sequence[str] = (), pinned: bool = False
    ) -> Note:
        return await asyncio.to_thread(self.repository.create, title, body, list(tags), pinned)

    async def get(self, note_id: str) -> Note:
        note = await asyncio.to_thread(self.repository.get, note_id)
        if note is None:
            raise NotFoundError(f"No note with id {note_id}.", details={"note_id": note_id})
        return note

    async def search(self, query: str, limit: int = 25) -> list[Note]:
        return await asyncio.to_thread(self.repository.search, query, limit)

    async def list(self, limit: int = 100) -> list[Note]:
        return await asyncio.to_thread(self.repository.list, limit)

    async def update(
        self,
        note_id: str,
        *,
        title: str | None = None,
        body: str | None = None,
        tags: Sequence[str] | None = None,
        pinned: bool | None = None,
        append: bool = False,
    ) -> Note:
        if append and body is not None:
            existing = await self.get(note_id)
            body = f"{existing.body}\n{body}".strip() if existing.body else body
        return await asyncio.to_thread(
            self.repository.update,
            note_id,
            title=title,
            body=body,
            tags=list(tags) if tags is not None else None,
            pinned=pinned,
        )

    async def delete(self, note_id: str) -> bool:
        return await asyncio.to_thread(self.repository.delete, note_id)
