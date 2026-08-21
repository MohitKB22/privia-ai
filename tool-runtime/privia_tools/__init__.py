"""PRIVIA tool runtime.

The model proposes tool calls. This package validates, authorises and executes
them. Nothing else in PRIVIA may perform a side effect.
"""

from __future__ import annotations

from .context import ToolContext
from .middleware import (
    Handler,
    Middleware,
    chain,
    confirmation_middleware,
    observability_middleware,
    output_limit_middleware,
    policy_middleware,
    rate_limit_middleware,
    retry_middleware,
    timeout_middleware,
    validation_middleware,
)
from .registry import Tool, ToolRegistry
from .runtime import ToolRuntime
from .tools import ALL_TOOLS, build_registry

__all__ = [
    "ALL_TOOLS",
    "Handler",
    "Middleware",
    "Tool",
    "ToolContext",
    "ToolRegistry",
    "ToolRuntime",
    "build_registry",
    "chain",
    "confirmation_middleware",
    "observability_middleware",
    "output_limit_middleware",
    "policy_middleware",
    "rate_limit_middleware",
    "retry_middleware",
    "timeout_middleware",
    "validation_middleware",
]
