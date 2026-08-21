"""Dependency-free local embeddings.

A hashed character-n-gram + word bag-of-features model. It is not competitive
with a trained sentence encoder, and PRIVIA says so in the UI. What it *is*:

* fully offline, with zero model download and no extra dependency,
* deterministic, so search results are reproducible and testable,
* good enough for "find the thing I told you about last week" over a few
  thousand short memories, which is the actual workload.

When Ollama is available with an embedding model, :class:`OllamaEmbedder` is
used instead and gives materially better recall.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence

from privia_shared.domain import ModelInfo

from .base import Embedder, normalize

_TOKEN_RE = re.compile(r"[a-z0-9']+")
_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "and",
        "or",
        "but",
        "of",
        "to",
        "in",
        "on",
        "for",
        "with",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "it",
        "this",
        "that",
        "as",
        "at",
        "by",
        "from",
        "i",
        "me",
        "my",
        "you",
        "your",
        "we",
        "our",
        "they",
        "their",
        "has",
        "have",
        "had",
    }
)


class LocalHashEmbedder(Embedder):
    name = "local-hash"
    sends_data_off_device = False

    def __init__(self, dimensions: int = 256, *, ngram: int = 4) -> None:
        self.dimensions = dimensions
        self.ngram = ngram
        self.model = f"local-hash-{dimensions}"

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed_sync(t) for t in texts]

    def _embed_sync(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        lowered = (text or "").lower()
        tokens = [t for t in _TOKEN_RE.findall(lowered) if t not in _STOPWORDS]

        # Word features carry most of the weight. Character n-grams are numerous,
        # so a high per-gram weight would let them dominate the vector norm and
        # drown out the lexical overlap that actually drives useful recall.
        for token in tokens:
            self._add(vector, f"w:{token}", 1.6)
            # A light stem so "reports" and "report" land near each other.
            if len(token) > 4:
                self._add(vector, f"w:{token[:-1]}", 0.9)
            if len(token) > 5:
                self._add(vector, f"w:{token[:-2]}", 0.5)

        for index in range(len(tokens) - 1):
            self._add(vector, f"b:{tokens[index]}_{tokens[index + 1]}", 0.7)

        compact = " ".join(tokens)
        char_weight = 0.10 if len(compact) > 40 else 0.18
        for index in range(max(0, len(compact) - self.ngram + 1)):
            self._add(vector, f"c:{compact[index : index + self.ngram]}", char_weight)

        return normalize(vector)

    def _add(self, vector: list[float], feature: str, weight: float) -> None:
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        value = int.from_bytes(digest, "little")
        bucket = value % self.dimensions
        sign = 1.0 if (value >> 63) & 1 else -1.0
        vector[bucket] += sign * weight

    async def health_check(self) -> ModelInfo:
        return ModelInfo(
            provider=self.name,
            model=self.model,
            available=True,
            location="local",
            detail=(
                f"{self.dimensions}-dimensional hashed n-gram embeddings. Offline and "
                "deterministic; install an Ollama embedding model for better recall."
            ),
        )
