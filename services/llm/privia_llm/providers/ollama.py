"""Ollama provider: the default, fully local path."""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Sequence

import httpx

from privia_shared.domain import ModelInfo
from privia_shared.errors import LLMUnavailableError

from ..base import ChatMessage, GenerationOptions, GenerationResult, LLMProvider


class OllamaProvider(LLMProvider):
    name = "ollama"
    location = "local"
    sends_data_off_device = False

    def __init__(
        self,
        model: str,
        base_url: str = "http://127.0.0.1:11434",
        *,
        timeout_seconds: float = 90.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(model)
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._client = client
        self._owns_client = client is None

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

    def _payload(
        self, messages: Sequence[ChatMessage], options: GenerationOptions, stream: bool
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": self.model,
            "messages": [m.to_dict() for m in messages],
            "stream": stream,
            "options": {
                "temperature": options.temperature,
                "top_p": options.top_p,
                "num_predict": options.max_tokens,
                **({"stop": list(options.stop)} if options.stop else {}),
                **({"seed": options.seed} if options.seed is not None else {}),
            },
        }
        if options.json_schema is not None:
            # Ollama supports both "json" and a full schema depending on version;
            # the schema form degrades gracefully to plain JSON mode.
            payload["format"] = options.json_schema
        return payload

    async def generate(
        self, messages: Sequence[ChatMessage], options: GenerationOptions | None = None
    ) -> GenerationResult:
        options = options or GenerationOptions()
        started = time.perf_counter()
        try:
            response = await self._get_client().post(
                "/api/chat", json=self._payload(messages, options, stream=False)
            )
            response.raise_for_status()
            data = response.json()
        except httpx.ConnectError as exc:
            raise LLMUnavailableError(
                f"Ollama is not reachable at {self.base_url}. Start it with `ollama serve`, "
                "or PRIVIA will fall back to its offline planner.",
                details={"base_url": self.base_url},
            ) from exc
        except httpx.HTTPStatusError as exc:
            detail = _describe_status(exc, self.model)
            raise LLMUnavailableError(detail, details={"status": exc.response.status_code}) from exc
        except httpx.HTTPError as exc:
            raise LLMUnavailableError(
                f"The local model did not respond ({type(exc).__name__}).",
            ) from exc

        message = data.get("message") or {}
        return GenerationResult(
            text=str(message.get("content", "")),
            model=data.get("model", self.model),
            provider=self.name,
            location=self.location,
            prompt_tokens=data.get("prompt_eval_count"),
            completion_tokens=data.get("eval_count"),
            latency_ms=int((time.perf_counter() - started) * 1000),
            finish_reason=str(data.get("done_reason") or "stop"),
        )

    async def stream(
        self, messages: Sequence[ChatMessage], options: GenerationOptions | None = None
    ) -> AsyncIterator[str]:
        options = options or GenerationOptions()
        try:
            async with self._get_client().stream(
                "POST", "/api/chat", json=self._payload(messages, options, stream=True)
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        chunk = json.loads(line)
                    except ValueError:
                        continue
                    content = (chunk.get("message") or {}).get("content")
                    if content:
                        yield content
                    if chunk.get("done"):
                        break
        except httpx.ConnectError as exc:
            raise LLMUnavailableError(
                f"Ollama is not reachable at {self.base_url}.", details={"base_url": self.base_url}
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMUnavailableError(
                f"The local model stream failed ({type(exc).__name__})."
            ) from exc

    async def health_check(self) -> ModelInfo:
        started = time.perf_counter()
        try:
            response = await self._get_client().get("/api/tags", timeout=5.0)
            response.raise_for_status()
            models = [m.get("name", "") for m in response.json().get("models", [])]
        except Exception as exc:
            return ModelInfo(
                provider=self.name,
                model=self.model,
                available=False,
                location=self.location,
                detail=(
                    f"Ollama is not running at {self.base_url}. "
                    f"({type(exc).__name__}) PRIVIA still works with its offline planner."
                ),
            )
        latency = int((time.perf_counter() - started) * 1000)
        installed = any(m == self.model or m.startswith(f"{self.model}:") for m in models)
        if not installed:
            return ModelInfo(
                provider=self.name,
                model=self.model,
                available=False,
                location=self.location,
                detail=(
                    f"Ollama is running but '{self.model}' is not installed. "
                    f"Run: ollama pull {self.model}"
                ),
                latency_ms=latency,
            )
        return ModelInfo(
            provider=self.name,
            model=self.model,
            available=True,
            location=self.location,
            detail=f"{len(models)} model(s) installed",
            latency_ms=latency,
        )

    async def list_models(self) -> list[str]:
        try:
            response = await self._get_client().get("/api/tags", timeout=5.0)
            response.raise_for_status()
            return [m.get("name", "") for m in response.json().get("models", [])]
        except Exception:
            return []


def _describe_status(exc: httpx.HTTPStatusError, model: str) -> str:
    status = exc.response.status_code
    if status == 404:
        return f"Ollama does not have '{model}' installed. Run: ollama pull {model}"
    if status == 400:
        return "Ollama rejected the request. The model may not support this feature."
    return f"Ollama returned HTTP {status}."
