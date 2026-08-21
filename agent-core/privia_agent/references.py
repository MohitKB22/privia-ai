"""Resolution of ``${step.path}`` references between plan steps.

A plan may say "search for the report, then summarise ``${0.files.0.path}``".
This module turns that reference into a real value once step 0 has run. If it
cannot be resolved the step is dropped with an explanation, so a tool is never
invoked with a literal ``${...}`` string.
"""

from __future__ import annotations

import re
from typing import Any

from privia_shared.tools import ToolResult

REFERENCE_RE = re.compile(r"^\$\{(\d+)\.([A-Za-z0-9_.\[\]-]+)\}$")
INLINE_REFERENCE_RE = re.compile(r"\$\{(\d+)\.([A-Za-z0-9_.\[\]-]+)\}")


class ReferenceError(ValueError):
    """Raised when a reference cannot be resolved."""


def has_reference(value: Any) -> bool:
    return isinstance(value, str) and bool(INLINE_REFERENCE_RE.search(value))


def resolve_arguments(arguments: dict[str, Any], results: list[ToolResult]) -> dict[str, Any]:
    """Return a copy of ``arguments`` with every reference replaced."""
    resolved: dict[str, Any] = {}
    for key, value in arguments.items():
        resolved[key] = _resolve_value(value, results)
    return resolved


def _resolve_value(value: Any, results: list[ToolResult]) -> Any:
    if isinstance(value, dict):
        return {k: _resolve_value(v, results) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_value(v, results) for v in value]
    if not isinstance(value, str):
        return value

    whole = REFERENCE_RE.match(value.strip())
    if whole:
        return _lookup(int(whole.group(1)), whole.group(2), results)

    def replace(match: re.Match[str]) -> str:
        found = _lookup(int(match.group(1)), match.group(2), results)
        return "" if found is None else str(found)

    if INLINE_REFERENCE_RE.search(value):
        return INLINE_REFERENCE_RE.sub(replace, value)
    return value


def _lookup(step_index: int, path: str, results: list[ToolResult]) -> Any:
    if step_index >= len(results):
        raise ReferenceError(f"step {step_index} has not run yet")
    result = results[step_index]
    if not result.success:
        raise ReferenceError(f"step {step_index} ({result.tool_name}) failed")
    current: Any = result.data
    for part in _split_path(path):
        if current is None:
            raise ReferenceError(f"'{path}' is empty at '{part}'")
        if isinstance(current, dict):
            if part not in current:
                raise ReferenceError(f"'{part}' is not present in the step {step_index} result")
            current = current[part]
        elif isinstance(current, list):
            try:
                index = int(part)
            except ValueError as exc:
                raise ReferenceError(f"'{part}' is not a list index") from exc
            if index >= len(current):
                raise ReferenceError(
                    f"step {step_index} returned {len(current)} item(s); index {index} does not exist"
                )
            current = current[index]
        else:
            raise ReferenceError(f"cannot descend into '{part}'")
    return current


def _split_path(path: str) -> list[str]:
    normalised = path.replace("[", ".").replace("]", "")
    return [part for part in normalised.split(".") if part]
