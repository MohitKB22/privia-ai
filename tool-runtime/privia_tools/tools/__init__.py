"""Built-in tool set."""

from __future__ import annotations

from typing import Any

from ..registry import ToolRegistry
from .browser import BROWSER_TOOLS
from .calendar import CALENDAR_TOOLS
from .email import EMAIL_TOOLS
from .files import FILE_TOOLS
from .memory import MEMORY_TOOLS
from .notes import NOTE_TOOLS
from .system import SYSTEM_TOOLS
from .terminal import TERMINAL_TOOLS

ALL_TOOLS: list[Any] = [
    *FILE_TOOLS,
    *NOTE_TOOLS,
    *CALENDAR_TOOLS,
    *EMAIL_TOOLS,
    *BROWSER_TOOLS,
    *TERMINAL_TOOLS,
    *MEMORY_TOOLS,
    *SYSTEM_TOOLS,
]


def build_registry(extra: list[Any] | None = None) -> ToolRegistry:
    """Create a registry containing every built-in tool."""
    registry = ToolRegistry()
    registry.register_all([*ALL_TOOLS, *(extra or [])])
    return registry


__all__ = [
    "ALL_TOOLS",
    "BROWSER_TOOLS",
    "CALENDAR_TOOLS",
    "EMAIL_TOOLS",
    "FILE_TOOLS",
    "MEMORY_TOOLS",
    "NOTE_TOOLS",
    "SYSTEM_TOOLS",
    "TERMINAL_TOOLS",
    "build_registry",
]
