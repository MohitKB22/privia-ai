"""Layered memory.

Three layers, deliberately separate:

``short-term``
    The current conversation. Lives in ``messages``, capped by turn count, never
    embedded, discarded with the session.
``long-term``
    Preferences and facts the user explicitly approved. Persisted, inspectable,
    deletable one by one.
``semantic``
    A local vector index over the long-term layer, used for recall.

Rules the service enforces, not just documents:

* nothing is stored without an explicit user action or approval,
* anything that looks like a credential is refused,
* every record carries provenance, so "why do you know that?" is answerable,
* deletion removes the vector too.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass

from privia_embeddings.base import Embedder, cosine_similarity
from privia_security.redaction import contains_secret
from privia_shared.config import Settings
from privia_shared.domain import MemoryRecord
from privia_shared.enums import MemoryKind, MessageRole
from privia_shared.errors import ValidationError
from privia_storage.repositories import MemoryRepository, MessageRepository

#: Patterns that are never stored, whatever the user says.
REFUSED_SUBSTRINGS = (
    "password",
    "passphrase",
    "api key",
    "api_key",
    "secret key",
    "private key",
    "credit card",
    "card number",
    "cvv",
    "social security",
    "ssn",
    "passport number",
    "bank account",
    "routing number",
    "pin code",
    "seed phrase",
)

MAX_MEMORY_CHARS = 2000
DEFAULT_CONTEXT_TURNS = 12


@dataclass
class MemoryHit:
    record: MemoryRecord
    score: float
    source: str  # "semantic" | "text" | "pinned"


class MemoryService:
    """Read/write facade over the memory layers."""

    def __init__(
        self,
        memories: MemoryRepository,
        messages: MessageRepository,
        embedder: Embedder,
        settings: Settings,
    ) -> None:
        self.memories = memories
        self.messages = messages
        self.embedder = embedder
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return self.settings.memory_enabled

    # -- writing --------------------------------------------------------------

    def check_storable(self, content: str) -> None:
        """Raise when the content must not be remembered."""
        text = (content or "").strip()
        if len(text) < 2:
            raise ValidationError("There is nothing to remember.")
        if len(text) > MAX_MEMORY_CHARS:
            raise ValidationError(
                f"That is longer than the {MAX_MEMORY_CHARS} character memory limit. "
                "Save it as a note instead."
            )
        lowered = text.lower()
        for pattern in REFUSED_SUBSTRINGS:
            if pattern in lowered:
                raise ValidationError(
                    "PRIVIA does not keep credentials or financial identifiers in memory.",
                    details={"matched": pattern},
                )
        if contains_secret(text):
            raise ValidationError("That looks like a live credential, so it was not stored.")

    async def remember(
        self,
        content: str,
        *,
        kind: MemoryKind = MemoryKind.FACT,
        tags: Sequence[str] = (),
        session_id: str | None = None,
        provenance: str = "user:explicit",
        pinned: bool = False,
    ) -> MemoryRecord:
        if not self.enabled:
            raise ValidationError("Memory is switched off in Settings.")
        self.check_storable(content)
        record = await asyncio.to_thread(
            self.memories.add,
            kind,
            content.strip(),
            tags=list(tags),
            provenance=provenance,
            session_id=session_id,
            pinned=pinned,
        )
        await self._index(record)
        return record

    async def _index(self, record: MemoryRecord) -> None:
        try:
            vector = await self.embedder.embed_one(record.content)
        except Exception:
            # The memory is already stored. Failing to index it degrades recall
            # to literal matching; losing the memory itself would be far worse.
            return
        await asyncio.to_thread(self.memories.set_vector, record.id, self.embedder.model, vector)

    async def reindex(self, limit: int = 500) -> int:
        """Embed anything that has no vector for the current model."""
        pending = await asyncio.to_thread(self.memories.missing_vectors, self.embedder.model, limit)
        if not pending:
            return 0
        vectors = await self.embedder.embed([p.content for p in pending])
        for record, vector in zip(pending, vectors, strict=True):
            await asyncio.to_thread(
                self.memories.set_vector, record.id, self.embedder.model, vector
            )
        return len(pending)

    # -- reading --------------------------------------------------------------

    async def search(
        self, query: str, *, limit: int = 8, min_score: float = 0.08
    ) -> list[MemoryRecord]:
        hits = await self.search_scored(query, limit=limit, min_score=min_score)
        return [hit.record for hit in hits]

    async def search_scored(
        self, query: str, *, limit: int = 8, min_score: float = 0.08
    ) -> list[MemoryHit]:
        """Hybrid recall: semantic similarity merged with literal text matching.

        Literal matching matters more than it sounds. If the user says "remember
        my flight is BA287", the exact string is what they will search for, and
        no embedding recovers a token like that reliably.
        """
        if not self.enabled or not query.strip():
            return []

        hits: dict[str, MemoryHit] = {}

        try:
            query_vector = await self.embedder.embed_one(query)
            stored = await asyncio.to_thread(self.memories.all_vectors, self.embedder.model)
            scored = [
                (memory_id, cosine_similarity(query_vector, vector)) for memory_id, vector in stored
            ]
            scored.sort(key=lambda item: item[1], reverse=True)
            for memory_id, score in scored[: limit * 2]:
                if score < min_score:
                    break
                record = await asyncio.to_thread(self.memories.get, memory_id)
                if record is not None:
                    hits[memory_id] = MemoryHit(
                        record.model_copy(update={"score": round(score, 4)}), score, "semantic"
                    )
        except Exception:  # noqa: S110
            # The semantic layer is best effort. If the embedder is unavailable or
            # the index is stale, literal matching below still returns results,
            # which is a degraded search rather than a failed one.
            pass

        for record in await asyncio.to_thread(self.memories.search_text, query, limit):
            existing = hits.get(record.id)
            boosted = 0.65 if existing is None else min(1.0, existing.score + 0.25)
            hits[record.id] = MemoryHit(
                record.model_copy(update={"score": round(boosted, 4)}), boosted, "text"
            )

        ordered = sorted(hits.values(), key=lambda h: h.score, reverse=True)
        for hit in ordered[:limit]:
            await asyncio.to_thread(self.memories.mark_used, hit.record.id)
        return ordered[:limit]

    async def pinned(self, limit: int = 10) -> list[MemoryRecord]:
        records = await asyncio.to_thread(self.memories.list, limit=limit * 4)
        return [r for r in records if r.pinned][:limit]

    async def short_term(self, session_id: str, turns: int = DEFAULT_CONTEXT_TURNS) -> list[dict]:
        return await asyncio.to_thread(self.messages.history, session_id, turns)

    async def build_context(
        self, session_id: str, query: str, *, turns: int = DEFAULT_CONTEXT_TURNS, recall: int = 5
    ) -> dict[str, object]:
        """Assemble everything the agent should know for this turn."""
        history = await self.short_term(session_id, turns)
        pinned = await self.pinned(5) if self.enabled else []
        recalled = await self.search(query, limit=recall) if self.enabled else []
        seen = {r.id for r in pinned}
        merged = pinned + [r for r in recalled if r.id not in seen]
        return {
            "history": [
                {"role": m["role"], "content": m["content"]}
                for m in history
                if m["role"] in (MessageRole.USER.value, MessageRole.ASSISTANT.value)
            ],
            "memories": [
                {"content": r.content, "kind": str(r.kind), "provenance": r.provenance}
                for r in merged
            ],
            "memory_enabled": self.enabled,
        }

    # -- deleting -------------------------------------------------------------

    async def forget(self, memory_id: str) -> bool:
        return await asyncio.to_thread(self.memories.delete, memory_id)

    async def forget_all(self, *, keep_pinned: bool = False) -> int:
        return await asyncio.to_thread(self.memories.delete_all, keep_pinned=keep_pinned)

    async def stats(self) -> dict[str, object]:
        records = await asyncio.to_thread(self.memories.list, limit=10_000)
        vectors = await asyncio.to_thread(self.memories.all_vectors, self.embedder.model)
        by_kind: dict[str, int] = {}
        for record in records:
            by_kind[str(record.kind)] = by_kind.get(str(record.kind), 0) + 1
        return {
            "enabled": self.enabled,
            "total": len(records),
            "pinned": sum(1 for r in records if r.pinned),
            "by_kind": by_kind,
            "indexed": len(vectors),
            "embedding_model": self.embedder.model,
            "embedding_dimensions": self.embedder.dimensions,
        }
