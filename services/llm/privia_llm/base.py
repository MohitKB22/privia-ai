"""The LLM provider interface.

Nothing outside this package knows which vendor is in use. Swapping Ollama for
a cloud provider changes one line of configuration and nothing else.
"""

from __future__ import annotations

import abc
import json
import re
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from privia_shared.domain import ModelInfo
from privia_shared.errors import LLMInvalidOutputError


@dataclass(frozen=True)
class ChatMessage:
    role: str  # "system" | "user" | "assistant"
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass
class GenerationResult:
    text: str
    model: str
    provider: str
    location: str = "local"
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    latency_ms: int = 0
    finish_reason: str = "stop"
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int | None:
        if self.prompt_tokens is None and self.completion_tokens is None:
            return None
        return (self.prompt_tokens or 0) + (self.completion_tokens or 0)


@dataclass
class GenerationOptions:
    temperature: float = 0.2
    max_tokens: int = 1024
    top_p: float = 0.9
    stop: tuple[str, ...] = ()
    seed: int | None = None
    #: When set, the provider is asked to emit JSON matching this schema.
    json_schema: dict[str, Any] | None = None


class LLMProvider(abc.ABC):
    """Every language-model backend implements exactly this."""

    name: str = "provider"
    location: str = "local"
    #: True when using this provider means data leaves the machine.
    sends_data_off_device: bool = False

    def __init__(self, model: str) -> None:
        self.model = model

    @abc.abstractmethod
    async def generate(
        self, messages: Sequence[ChatMessage], options: GenerationOptions | None = None
    ) -> GenerationResult:
        """One-shot completion."""

    @abc.abstractmethod
    def stream(
        self, messages: Sequence[ChatMessage], options: GenerationOptions | None = None
    ) -> AsyncIterator[str]:
        """Yield text chunks as they arrive.

        Declared with ``def``, not ``async def``: an async generator function is
        a synchronous callable that returns an async iterator. Implementations
        use ``async def`` with ``yield``, which produces exactly that.
        """

    @abc.abstractmethod
    async def health_check(self) -> ModelInfo:
        """Report availability. Must never raise."""

    async def structured_output(
        self,
        messages: Sequence[ChatMessage],
        schema: dict[str, Any],
        options: GenerationOptions | None = None,
        *,
        max_attempts: int = 3,
    ) -> dict[str, Any]:
        """Generate JSON that satisfies ``schema``.

        Models fail at this regularly, so the loop is explicit: ask, extract,
        validate, and on failure feed the error back once before giving up. The
        caller always gets valid data or a clear
        :class:`~privia_shared.errors.LLMInvalidOutputError`.
        """
        options = options or GenerationOptions()
        options.json_schema = schema
        conversation = list(messages)
        last_error = ""
        for attempt in range(1, max_attempts + 1):
            result = await self.generate(conversation, options)
            try:
                payload = extract_json(result.text)
                validate_against_schema(payload, schema)
                return payload
            except LLMInvalidOutputError as exc:
                last_error = exc.message
                if attempt >= max_attempts:
                    break
                conversation = [
                    *messages,
                    ChatMessage("assistant", result.text[:2000]),
                    ChatMessage(
                        "user",
                        "That response was not valid. "
                        f"Problem: {last_error}. "
                        "Reply with ONLY a JSON object matching the schema. No prose, no "
                        "markdown fence, no explanation.",
                    ),
                ]
        raise LLMInvalidOutputError(
            "The model could not produce output matching the required schema.",
            details={"attempts": max_attempts, "last_error": last_error, "model": self.model},
        )

    async def close(self) -> None:
        """Release any connection pool."""
        return None


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> dict[str, Any]:
    """Pull the first JSON object out of a model response."""
    if not text or not text.strip():
        raise LLMInvalidOutputError("The model returned an empty response.")
    candidates: list[str] = []
    fenced = _FENCE_RE.search(text)
    if fenced:
        candidates.append(fenced.group(1).strip())
    stripped = text.strip()
    candidates.append(stripped)
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end > start:
        candidates.append(stripped[start : end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, list):
            return {"items": parsed}
    raise LLMInvalidOutputError(
        "The model response did not contain a JSON object.",
        details={"preview": text[:200]},
    )


_TYPE_MAP: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
}


def validate_against_schema(payload: dict[str, Any], schema: dict[str, Any]) -> None:
    """A deliberately small JSON-Schema subset check.

    Only the constructs PRIVIA's own prompts use are supported: ``type``,
    ``required``, ``properties`` and ``enum``. Bringing in a full validator for
    this would be more dependency than the problem deserves; anything the model
    produces is re-validated by Pydantic downstream anyway.
    """
    if schema.get("type") == "object" or "properties" in schema:
        if not isinstance(payload, dict):
            raise LLMInvalidOutputError("Expected a JSON object at the top level.")
        for key in schema.get("required", []):
            if key not in payload:
                raise LLMInvalidOutputError(f"Required field '{key}' is missing.")
        properties = schema.get("properties", {})
        for key, definition in properties.items():
            if key not in payload:
                continue
            _check_value(key, payload[key], definition)


def _check_value(key: str, value: Any, definition: dict[str, Any]) -> None:
    expected = definition.get("type")
    if expected and expected in _TYPE_MAP:
        if expected == "number" and isinstance(value, bool):
            raise LLMInvalidOutputError(f"Field '{key}' should be a number.")
        if not isinstance(value, _TYPE_MAP[expected]):
            raise LLMInvalidOutputError(
                f"Field '{key}' should be {expected}, got {type(value).__name__}."
            )
    choices = definition.get("enum")
    if choices and value not in choices:
        raise LLMInvalidOutputError(f"Field '{key}' must be one of {choices[:8]}, got {value!r}.")
    if definition.get("type") == "array" and isinstance(value, list):
        item_schema = definition.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value[:50]):
                if item_schema.get("type") == "object":
                    validate_against_schema(item if isinstance(item, dict) else {}, item_schema)
                else:
                    _check_value(f"{key}[{index}]", item, item_schema)
