"""Application container.

Every long-lived object is built once here and handed out by dependency
injection. Nothing in the request path reaches for a global, which is what makes
the whole stack testable with a temporary database and mock providers.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from privia_agent.orchestrator import Agent
from privia_embeddings import Embedder, build_embedder
from privia_integrations.registry import ProviderSet, build_providers
from privia_llm.router import LLMRouter
from privia_memory.service import MemoryService
from privia_observability.logging import StructuredLogger, configure_logging, get_logger
from privia_observability.metrics import Metrics, get_metrics
from privia_security.audit import AuditLogger, DatabaseAuditSink, InMemoryAuditSink
from privia_security.limits import RateLimiter
from privia_security.policy import PermissionEngine
from privia_security.secrets import SecretStore
from privia_shared.config import Settings, get_settings
from privia_shared.domain import AuditEvent
from privia_shared.enums import PermissionGrantState, Scope
from privia_speech import SttProvider, TtsProvider, build_stt, build_tts
from privia_storage.engine import Database
from privia_storage.migrator import migrate
from privia_storage.repositories import Repositories
from privia_tools.registry import ToolRegistry
from privia_tools.runtime import ToolRuntime
from privia_tools.tools import build_registry

#: Ring buffer of recent audit events, for the live activity feed.
RECENT_EVENTS = 200


@dataclass
class Container:
    settings: Settings
    database: Database
    repositories: Repositories
    providers: ProviderSet
    permissions: PermissionEngine
    audit: AuditLogger
    registry: ToolRegistry
    runtime: ToolRuntime
    router: LLMRouter
    memory: MemoryService
    embedder: Embedder
    agent: Agent
    stt: SttProvider
    tts: TtsProvider
    secrets: SecretStore
    rate_limiter: RateLimiter
    logger: StructuredLogger
    metrics: Metrics
    recent_audit: InMemoryAuditSink
    startup_warnings: list[str] = field(default_factory=list)
    #: Subscribers for the server-sent activity stream.
    _listeners: list[asyncio.Queue] = field(default_factory=list)

    # -- lifecycle ------------------------------------------------------------

    async def startup(self) -> None:
        migrate(self.database)
        self.repositories.users.ensure_default()
        self.load_permissions()
        self.apply_setting_overrides()
        await self.memory.reindex(limit=200)
        self.logger.info(
            "startup.complete",
            tools=len(self.registry),
            allowed_directories=len(self.providers.path_guard.roots),
            local_llm=f"{self.settings.local_llm_provider}:{self.settings.local_llm_model}",
            cloud_enabled=self.settings.cloud_processing_enabled,
        )

    async def shutdown(self) -> None:
        try:
            await self.router.close()
            await self.stt.close()
            await self.embedder.close()
        finally:
            self.database.dispose()
        self.logger.info("shutdown.complete")

    # -- permissions ----------------------------------------------------------

    def load_permissions(self) -> None:
        """Rehydrate persisted grants into the in-memory engine."""
        grants = self.repositories.permissions.list()
        self.permissions.load(grants)
        granted = [g.scope for g in grants if g.state is PermissionGrantState.GRANTED]
        self.logger.info("permissions.loaded", granted=[str(s) for s in granted])

    def persist_grant(self, scope: Scope) -> None:
        grant = self.permissions.get(scope)
        if grant is not None:
            self.repositories.permissions.upsert(grant)

    def apply_setting_overrides(self) -> None:
        """Runtime settings the user changed in the UI beat the environment."""
        overrides = self.repositories.settings.all()
        for key in ("cloud_processing_enabled", "memory_enabled", "telemetry_enabled"):
            if key in overrides:
                object.__setattr__(self.settings, key, bool(overrides[key]))
        directories = overrides.get("allowed_directories")
        if isinstance(directories, list):
            object.__setattr__(self.settings, "allowed_directories", ",".join(directories))
            self.providers.update_allowed_directories([Path(d) for d in directories])
        self.router.invalidate_health()

    def grant_directory(self, directory: str) -> list[str]:
        """Add a folder to the allowlist and re-point every guard at it."""
        current = [str(p) for p in self.providers.path_guard.roots]
        if directory not in current:
            current.append(directory)
        self.repositories.settings.set("allowed_directories", current)
        self.providers.update_allowed_directories([Path(d) for d in current])
        object.__setattr__(self.settings, "allowed_directories", ",".join(current))
        return current

    def revoke_directory(self, directory: str) -> list[str]:
        current = [str(p) for p in self.providers.path_guard.roots if str(p) != directory]
        self.repositories.settings.set("allowed_directories", current)
        self.providers.update_allowed_directories([Path(d) for d in current])
        object.__setattr__(self.settings, "allowed_directories", ",".join(current))
        return current

    # -- live activity --------------------------------------------------------

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._listeners.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        if queue in self._listeners:
            self._listeners.remove(queue)

    def broadcast(self, event: AuditEvent) -> None:
        payload = event.model_dump(mode="json")
        for queue in list(self._listeners):
            # A slow listener must not block the request that produced the event.
            with suppress(asyncio.QueueFull):
                queue.put_nowait(payload)


def build_container(
    settings: Settings | None = None,
    *,
    offline: bool = False,
    configure_logs: bool = True,
) -> Container:
    settings = settings or get_settings()
    warnings = settings.validate_startup()
    settings.ensure_directories()

    logger = (
        configure_logging(
            settings.log_level,
            log_dir=settings.logs_dir,
            json_output=settings.app_env == "production",
        )
        if configure_logs
        else get_logger()
    )
    for warning in warnings:
        logger.warning("config.warning", detail=warning)

    database = Database(settings.database_url)
    repositories = Repositories(database)
    providers = build_providers(settings, repositories, offline=offline)
    permissions = PermissionEngine()

    recent = InMemoryAuditSink(capacity=RECENT_EVENTS)
    container_ref: dict[str, Container] = {}

    def on_event(event: AuditEvent) -> None:
        container = container_ref.get("value")
        if container is not None:
            container.broadcast(event)

    audit = AuditLogger([DatabaseAuditSink(repositories.audit), recent], on_event=on_event)

    registry = build_registry()
    runtime = ToolRuntime(
        registry,
        permissions,
        max_output_bytes=settings.max_tool_output_bytes,
        logger=logger.bind("tools"),
    )
    router = LLMRouter(settings, permissions)
    embedder = build_embedder(settings)
    memory = MemoryService(repositories.memories, repositories.messages, embedder, settings)
    agent = Agent(router, runtime, repositories, memory, logger=logger.bind("agent"))

    container = Container(
        settings=settings,
        database=database,
        repositories=repositories,
        providers=providers,
        permissions=permissions,
        audit=audit,
        registry=registry,
        runtime=runtime,
        router=router,
        memory=memory,
        embedder=embedder,
        agent=agent,
        stt=build_stt(settings),
        tts=build_tts(settings),
        secrets=providers.secrets,
        rate_limiter=RateLimiter(settings.rate_limit_per_minute),
        logger=logger,
        metrics=get_metrics(),
        recent_audit=recent,
        startup_warnings=list(warnings),
    )
    container_ref["value"] = container
    return container


def build_tool_context(container: Container, session_id: str, request_id: str, **extra: Any):
    from privia_tools.context import ToolContext

    return ToolContext(
        settings=container.settings,
        repositories=container.repositories,
        providers=container.providers,
        permissions=container.permissions,
        audit=container.audit,
        rate_limiter=container.rate_limiter,
        session_id=session_id,
        request_id=request_id,
        scratch={"memory_service": container.memory},
        **extra,
    )
