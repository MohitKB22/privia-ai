"""Model routing.

The router owns one decision: *which model handles this request, and does that
mean data leaves the machine?*

Rules, in order:

1. Local first, always. If a local model is healthy, it is used.
2. Cloud is used only when the user enabled it **and** granted
   ``cloud:inference``. Configuration alone is never enough.
3. If neither is available, the deterministic offline planner runs. PRIVIA
   degrades; it does not fail.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from privia_security.policy import PermissionEngine
from privia_shared.config import Settings
from privia_shared.domain import ModelInfo
from privia_shared.enums import PermissionDecision, ProcessingLocation, RiskLevel, Scope
from privia_shared.errors import CloudDisabledError
from privia_shared.permissions import PolicyRequest

from .base import LLMProvider
from .providers.cloud import AnthropicProvider, OpenAIProvider
from .providers.heuristic import HeuristicProvider
from .providers.ollama import OllamaProvider

#: How long a health probe result is trusted before being re-checked.
HEALTH_TTL_SECONDS = 30.0


@dataclass
class RouteDecision:
    provider: LLMProvider
    location: ProcessingLocation
    reason: str
    degraded: bool = False


class LLMRouter:
    """Chooses a provider per request and reports the privacy consequence."""

    def __init__(
        self,
        settings: Settings,
        permissions: PermissionEngine,
        *,
        local: LLMProvider | None = None,
        cloud: LLMProvider | None = None,
        fallback: LLMProvider | None = None,
    ) -> None:
        self.settings = settings
        self.permissions = permissions
        self.local = local if local is not None else build_local_provider(settings)
        self.cloud = cloud if cloud is not None else build_cloud_provider(settings)
        self.fallback = fallback or HeuristicProvider()
        self._local_health: tuple[float, ModelInfo] | None = None
        self._cloud_health: tuple[float, ModelInfo] | None = None
        self._lock = asyncio.Lock()

    # -- health ---------------------------------------------------------------

    async def local_health(self, *, force: bool = False) -> ModelInfo:
        now = time.monotonic()
        if not force and self._local_health and now - self._local_health[0] < HEALTH_TTL_SECONDS:
            return self._local_health[1]
        info = await self.local.health_check()
        self._local_health = (now, info)
        return info

    async def cloud_health(self, *, force: bool = False) -> ModelInfo | None:
        if self.cloud is None:
            return None
        now = time.monotonic()
        if not force and self._cloud_health and now - self._cloud_health[0] < HEALTH_TTL_SECONDS:
            return self._cloud_health[1]
        info = await self.cloud.health_check()
        self._cloud_health = (now, info)
        return info

    def invalidate_health(self) -> None:
        self._local_health = None
        self._cloud_health = None

    # -- routing --------------------------------------------------------------

    async def route(
        self,
        *,
        session_id: str,
        prefer: ProcessingLocation | None = None,
        cloud_enabled_override: bool | None = None,
    ) -> RouteDecision:
        cloud_enabled = (
            self.settings.cloud_processing_enabled
            if cloud_enabled_override is None
            else cloud_enabled_override
        )

        if prefer is ProcessingLocation.CLOUD:
            decision = self._cloud_permission(session_id)
            if not cloud_enabled:
                raise CloudDisabledError(
                    "Cloud processing is switched off. Turn it on in the Privacy Center first."
                )
            if decision is not PermissionDecision.ALLOW:
                raise CloudDisabledError(
                    "Sending this to a cloud provider needs your permission first.",
                    details={"scope": Scope.CLOUD_INFERENCE.value, "decision": str(decision)},
                )
            if self.cloud is None:
                raise CloudDisabledError("No cloud provider is configured.")
            health = await self.cloud_health()
            if health is None or not health.available:
                raise CloudDisabledError(
                    health.detail if health else "The cloud provider is not reachable."
                )
            return RouteDecision(self.cloud, ProcessingLocation.CLOUD, "You asked for cloud.")

        local_health = await self.local_health()
        if local_health.available:
            return RouteDecision(self.local, ProcessingLocation.LOCAL, "Local model is available.")

        if (
            cloud_enabled
            and self.cloud is not None
            and self._cloud_permission(session_id) is PermissionDecision.ALLOW
        ):
            cloud_health = await self.cloud_health()
            if cloud_health and cloud_health.available:
                return RouteDecision(
                    self.cloud,
                    ProcessingLocation.CLOUD,
                    "The local model is unavailable and you allowed cloud processing.",
                )

        return RouteDecision(
            self.fallback,
            ProcessingLocation.LOCAL,
            local_health.detail or "No language model is available; using the offline planner.",
            degraded=True,
        )

    def _cloud_permission(self, session_id: str) -> PermissionDecision:
        result = self.permissions.evaluate(
            PolicyRequest(
                session_id=session_id,
                tool_name="llm.cloud",
                scopes=(Scope.CLOUD_INFERENCE,),
                risk_level=RiskLevel.HIGH,
                requires_confirmation=True,
            )
        )
        return result.decision

    # -- lifecycle ------------------------------------------------------------

    async def close(self) -> None:
        for provider in (self.local, self.cloud, self.fallback):
            if provider is not None:
                await provider.close()

    async def describe(self) -> dict[str, object]:
        local = await self.local_health()
        cloud = await self.cloud_health()
        return {
            "local": local.model_dump(mode="json"),
            "cloud": cloud.model_dump(mode="json") if cloud else None,
            "fallback": "offline rule engine (always available)",
            "cloud_enabled": self.settings.cloud_processing_enabled,
        }


def build_local_provider(settings: Settings) -> LLMProvider:
    if settings.local_llm_provider == "heuristic":
        return HeuristicProvider()
    return OllamaProvider(
        settings.local_llm_model,
        settings.ollama_base_url,
        timeout_seconds=settings.llm_timeout_seconds,
    )


def build_cloud_provider(settings: Settings) -> LLMProvider | None:
    if settings.cloud_llm_provider == "openai":
        return OpenAIProvider(
            settings.cloud_llm_model or "gpt-4o-mini",
            settings.openai_api_key,
            settings.openai_base_url,
            timeout_seconds=settings.llm_timeout_seconds,
        )
    if settings.cloud_llm_provider == "anthropic":
        return AnthropicProvider(
            settings.cloud_llm_model or "claude-sonnet-4-20250514",
            settings.anthropic_api_key,
            settings.anthropic_base_url,
            timeout_seconds=settings.llm_timeout_seconds,
        )
    return None
