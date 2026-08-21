"""The FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from privia_shared import __version__
from privia_shared.config import Settings

from .container import Container, build_container
from .errors import install_error_handlers
from .middleware import install_middleware
from .routers import (
    audit,
    chat,
    files,
    health,
    integrations,
    memory,
    notes,
    permissions,
    privacy,
    sessions,
    tools,
    voice,
)

DESCRIPTION = """\
PRIVIA is a local-first personal assistant. This API runs on your machine and is
bound to loopback by default.

**Design rules this API enforces**

* The language model proposes tool calls; a deterministic runtime validates
  permissions and executes them.
* High-impact actions (sending email, deleting a file, cancelling an event,
  running a state-changing command) return `428 CONFIRMATION_REQUIRED` with a
  full preview. They run only after you approve that exact preview.
* Cloud inference is opt-in, per-provider, and requires the `cloud:inference`
  scope in addition to being enabled.
* Errors always use one envelope and never contain a stack trace.
"""


def create_app(
    settings: Settings | None = None,
    *,
    container: Container | None = None,
    offline: bool = False,
) -> FastAPI:
    """Build the application. Tests pass their own container."""
    built = container or build_container(settings, offline=offline)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await built.startup()
        try:
            yield
        finally:
            await built.shutdown()

    app = FastAPI(
        title="PRIVIA",
        summary="Private Personal AI",
        description=DESCRIPTION,
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        contact={"name": "PRIVIA", "url": "https://github.com/privia-app/privia"},
        license_info={"name": "Apache-2.0"},
    )
    app.state.container = built
    app.state.settings = built.settings

    install_middleware(app, built.settings, built.rate_limiter)
    install_error_handlers(app)

    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(sessions.router)
    app.include_router(tools.router)
    app.include_router(permissions.router)
    app.include_router(memory.router)
    app.include_router(notes.router)
    app.include_router(files.router)
    app.include_router(audit.router)
    app.include_router(privacy.router)
    app.include_router(integrations.router)
    app.include_router(voice.router)
    return app
