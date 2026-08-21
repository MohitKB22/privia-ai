"""Embedding provider interface."""

from __future__ import annotations

import abc
import math
from collections.abc import Sequence

from privia_shared.domain import ModelInfo


class Embedder(abc.ABC):
    """Turns text into a vector. Local by default; never sends data anywhere."""

    name: str = "embedder"
    model: str = "unknown"
    dimensions: int = 256
    sends_data_off_device: bool = False

    @abc.abstractmethod
    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed a batch. Returns one unit-length vector per input."""

    async def embed_one(self, text: str) -> list[float]:
        vectors = await self.embed([text])
        return vectors[0]

    @abc.abstractmethod
    async def health_check(self) -> ModelInfo:
        """Report availability. Must never raise."""

    async def close(self) -> None:
        return None


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity, tolerant of differing lengths (compares the overlap)."""
    length = min(len(a), len(b))
    if length == 0:
        return 0.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for index in range(length):
        x = a[index]
        y = b[index]
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0
    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


def normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vector))
    if norm <= 0.0:
        return vector
    return [v / norm for v in vector]
