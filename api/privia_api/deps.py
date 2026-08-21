"""FastAPI dependencies."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from privia_shared.errors import ConfigurationError
from privia_tools.context import ToolContext

from .container import Container, build_tool_context


def get_container(request: Request) -> Container:
    container = getattr(request.app.state, "container", None)
    if container is None:  # pragma: no cover - only if startup was skipped
        raise ConfigurationError("The application container is not initialised.")
    return container


ContainerDep = Annotated[Container, Depends(get_container)]


def get_request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "req_unknown")


RequestIdDep = Annotated[str, Depends(get_request_id)]


def make_context(container: Container, session_id: str, request_id: str) -> ToolContext:
    return build_tool_context(container, session_id, request_id)
