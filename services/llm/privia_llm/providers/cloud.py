"""Cloud provider adapters.

These are never used unless the user explicitly enables cloud processing *and*
grants the ``cloud:inference`` scope. Both adapters speak the vendor HTTP API
directly with ``httpx``: no vendor SDK, so there is no hidden telemetry and no
transitive dependency surface.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Sequence

import httpx

from privia_shared.domain import ModelInfo
from privia_shared.errors import LLMUnavailableError

from ..base import ChatMessage, GenerationOptions, GenerationResult, LLMProvider


class _CloudProvider(LLMProvider):
    location = "cloud"
    sends_data_off_device = True

    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str,
        *,
        timeout_seconds: float = 90.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(model)
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._client = client
        self._owns_client = client is None

    def _headers(self) -> dict[str, str]:  # pragma: no cover - overridden
        raise NotImplementedError

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.timeout_seconds),
                headers=self._headers(),
                # Explicit about where the request goes: no environment proxy
                # silently interposing itself on a request the user opted into.
                trust_env=False,
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    def _guard(self) -> None:
        if not self.api_key:
            raise LLMUnavailableError(
                f"No API key is configured for {self.name}.", details={"provider": self.name}
            )


class OpenAIProvider(_CloudProvider):
    name = "openai"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def _payload(
        self, messages: Sequence[ChatMessage], options: GenerationOptions, stream: bool
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": self.model,
            "messages": [m.to_dict() for m in messages],
            "temperature": options.temperature,
            "max_tokens": options.max_tokens,
            "top_p": options.top_p,
            "stream": stream,
        }
        if options.stop:
            payload["stop"] = list(options.stop)
        if options.json_schema is not None:
            payload["response_format"] = {"type": "json_object"}
        return payload

    async def generate(
        self, messages: Sequence[ChatMessage], options: GenerationOptions | None = None
    ) -> GenerationResult:
        self._guard()
        options = options or GenerationOptions()
        started = time.perf_counter()
        try:
            response = await self._get_client().post(
                "/chat/completions", json=self._payload(messages, options, False)
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            raise LLMUnavailableError(
                _cloud_status_message("OpenAI", exc.response.status_code),
                details={"status": exc.response.status_code},
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMUnavailableError(
                f"OpenAI could not be reached ({type(exc).__name__})."
            ) from exc
        choice = (data.get("choices") or [{}])[0]
        usage = data.get("usage") or {}
        return GenerationResult(
            text=str((choice.get("message") or {}).get("content", "")),
            model=data.get("model", self.model),
            provider=self.name,
            location=self.location,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            latency_ms=int((time.perf_counter() - started) * 1000),
            finish_reason=str(choice.get("finish_reason") or "stop"),
        )

    async def stream(
        self, messages: Sequence[ChatMessage], options: GenerationOptions | None = None
    ) -> AsyncIterator[str]:
        self._guard()
        options = options or GenerationOptions()
        async with self._get_client().stream(
            "POST", "/chat/completions", json=self._payload(messages, options, True)
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                body = line[5:].strip()
                if body == "[DONE]":
                    break
                try:
                    chunk = json.loads(body)
                except ValueError:
                    continue
                delta = ((chunk.get("choices") or [{}])[0].get("delta") or {}).get("content")
                if delta:
                    yield delta

    async def health_check(self) -> ModelInfo:
        if not self.api_key:
            return ModelInfo(
                provider=self.name,
                model=self.model,
                available=False,
                location="cloud",
                detail="No OPENAI_API_KEY configured.",
            )
        started = time.perf_counter()
        try:
            response = await self._get_client().get("/models", timeout=8.0)
            response.raise_for_status()
        except Exception as exc:
            return ModelInfo(
                provider=self.name,
                model=self.model,
                available=False,
                location="cloud",
                detail=f"OpenAI is not reachable ({type(exc).__name__}).",
            )
        return ModelInfo(
            provider=self.name,
            model=self.model,
            available=True,
            location="cloud",
            detail="API key accepted",
            latency_ms=int((time.perf_counter() - started) * 1000),
        )


class AnthropicProvider(_CloudProvider):
    name = "anthropic"

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _split(messages: Sequence[ChatMessage]) -> tuple[str, list[dict[str, str]]]:
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        turns = [m.to_dict() for m in messages if m.role != "system"]
        return system, turns

    def _payload(
        self, messages: Sequence[ChatMessage], options: GenerationOptions, stream: bool
    ) -> dict[str, object]:
        system, turns = self._split(messages)
        payload: dict[str, object] = {
            "model": self.model,
            "messages": turns or [{"role": "user", "content": "Hello"}],
            "max_tokens": options.max_tokens,
            "temperature": options.temperature,
            "stream": stream,
        }
        if system:
            payload["system"] = system
        if options.stop:
            payload["stop_sequences"] = list(options.stop)
        return payload

    async def generate(
        self, messages: Sequence[ChatMessage], options: GenerationOptions | None = None
    ) -> GenerationResult:
        self._guard()
        options = options or GenerationOptions()
        started = time.perf_counter()
        try:
            response = await self._get_client().post(
                "/v1/messages", json=self._payload(messages, options, False)
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            raise LLMUnavailableError(
                _cloud_status_message("Anthropic", exc.response.status_code),
                details={"status": exc.response.status_code},
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMUnavailableError(
                f"Anthropic could not be reached ({type(exc).__name__})."
            ) from exc
        blocks = data.get("content") or []
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        usage = data.get("usage") or {}
        return GenerationResult(
            text=text,
            model=data.get("model", self.model),
            provider=self.name,
            location=self.location,
            prompt_tokens=usage.get("input_tokens"),
            completion_tokens=usage.get("output_tokens"),
            latency_ms=int((time.perf_counter() - started) * 1000),
            finish_reason=str(data.get("stop_reason") or "stop"),
        )

    async def stream(
        self, messages: Sequence[ChatMessage], options: GenerationOptions | None = None
    ) -> AsyncIterator[str]:
        self._guard()
        options = options or GenerationOptions()
        async with self._get_client().stream(
            "POST", "/v1/messages", json=self._payload(messages, options, True)
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                try:
                    chunk = json.loads(line[5:].strip())
                except ValueError:
                    continue
                if chunk.get("type") == "content_block_delta":
                    text = (chunk.get("delta") or {}).get("text")
                    if text:
                        yield text
                elif chunk.get("type") == "message_stop":
                    break

    async def health_check(self) -> ModelInfo:
        if not self.api_key:
            return ModelInfo(
                provider=self.name,
                model=self.model,
                available=False,
                location="cloud",
                detail="No ANTHROPIC_API_KEY configured.",
            )
        started = time.perf_counter()
        try:
            response = await self._get_client().post(
                "/v1/messages",
                json={
                    "model": self.model,
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "hi"}],
                },
                timeout=10.0,
            )
            response.raise_for_status()
        except Exception as exc:
            return ModelInfo(
                provider=self.name,
                model=self.model,
                available=False,
                location="cloud",
                detail=f"Anthropic is not reachable ({type(exc).__name__}).",
            )
        return ModelInfo(
            provider=self.name,
            model=self.model,
            available=True,
            location="cloud",
            detail="API key accepted",
            latency_ms=int((time.perf_counter() - started) * 1000),
        )


def _cloud_status_message(vendor: str, status: int) -> str:
    if status in (401, 403):
        return f"{vendor} rejected the API key."
    if status == 429:
        return f"{vendor} rate limited the request. Try again shortly."
    if status >= 500:
        return f"{vendor} is having a problem (HTTP {status})."
    return f"{vendor} returned HTTP {status}."
