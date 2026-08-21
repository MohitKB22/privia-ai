"""PRIVIA language-model layer.

Everything above this package talks to :class:`LLMProvider`. Which vendor (or
whether there is a model at all) is a configuration detail.
"""

from __future__ import annotations

from .base import (
    ChatMessage,
    GenerationOptions,
    GenerationResult,
    LLMProvider,
    extract_json,
    validate_against_schema,
)
from .providers.cloud import AnthropicProvider, OpenAIProvider
from .providers.heuristic import (
    TASK_CLASSIFY,
    TASK_MARKER,
    TASK_PLAN,
    TASK_RESPOND,
    HeuristicProvider,
    compose_response,
)
from .providers.ollama import OllamaProvider
from .router import LLMRouter, RouteDecision, build_cloud_provider, build_local_provider
from .rules import (
    INTENT_RULES,
    TOOL_INTENTS,
    build_plan,
    classify,
    entity_value,
    entity_values,
    extract_datetime,
    extract_entities,
)

__all__ = [
    "INTENT_RULES",
    "TASK_CLASSIFY",
    "TASK_MARKER",
    "TASK_PLAN",
    "TASK_RESPOND",
    "TOOL_INTENTS",
    "AnthropicProvider",
    "ChatMessage",
    "GenerationOptions",
    "GenerationResult",
    "HeuristicProvider",
    "LLMProvider",
    "LLMRouter",
    "OllamaProvider",
    "OpenAIProvider",
    "RouteDecision",
    "build_cloud_provider",
    "build_local_provider",
    "build_plan",
    "classify",
    "compose_response",
    "entity_value",
    "entity_values",
    "extract_datetime",
    "extract_entities",
    "extract_json",
    "validate_against_schema",
]
