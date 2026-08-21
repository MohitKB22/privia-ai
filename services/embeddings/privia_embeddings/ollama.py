"""Ollama embeddings: better recall when a model is installed."""

from __future__ import annotations

import time
from collections.abc import Sequence

import httpx

from privia_shared.domain import ModelInfo

from .base import Embedder, normalize


class OllamaEmbedder(Embedder):
    name = "ollama"
    sends_data_off_device = False

    def __init__(
        self,
        model: str = "nomic-embed-text",
        base_url: str = "http://127.0.0.1:11434",
        *,
        timeout_seconds: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._client = client
        self._owns_client = client is None
        self.dimensions = 768

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.timeout_seconds),
                # Never route loopback traffic through a proxy. A corporate
                # HTTP_PROXY in the environment would otherwise send every
                # prompt to the proxy instead of to the local model, which is
                # both broken and a privacy leak.
                trust_env=False,
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        client = self._get_client()
        for text in texts:
            response = await client.post(
                "/api/embeddings", json={"model": self.model, "prompt": text}
            )
            response.raise_for_status()
            embedding = response.json().get("embedding") or []
            if embedding:
                self.dimensions = len(embedding)
            vectors.append(normalize([float(v) for v in embedding]))
        return vectors

    async def health_check(self) -> ModelInfo:
        started = time.perf_counter()
        try:
            response = await self._get_client().post(
                "/api/embeddings", json={"model": self.model, "prompt": "ping"}, timeout=8.0
            )
            response.raise_for_status()
            embedding = response.json().get("embedding") or []
        except Exception as exc:
            return ModelInfo(
                provider=self.name,
                model=self.model,
                available=False,
                location="local",
                detail=(
                    f"Ollama embeddings unavailable ({type(exc).__name__}). "
                    "PRIVIA falls back to local hashed embeddings."
                ),
            )
        if embedding:
            self.dimensions = len(embedding)
        return ModelInfo(
            provider=self.name,
            model=self.model,
            available=bool(embedding),
            location="local",
            detail=f"{self.dimensions}-dimensional embeddings",
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
