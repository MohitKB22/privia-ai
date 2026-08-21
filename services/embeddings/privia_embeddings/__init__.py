"""PRIVIA embeddings."""

from __future__ import annotations

from privia_shared.config import Settings

from .base import Embedder, cosine_similarity, normalize
from .local import LocalHashEmbedder
from .ollama import OllamaEmbedder

__all__ = [
    "Embedder",
    "LocalHashEmbedder",
    "OllamaEmbedder",
    "build_embedder",
    "cosine_similarity",
    "normalize",
]


def build_embedder(settings: Settings) -> Embedder:
    if settings.local_embedding_provider == "ollama":
        return OllamaEmbedder(settings.local_embedding_model, settings.ollama_base_url)
    return LocalHashEmbedder()
