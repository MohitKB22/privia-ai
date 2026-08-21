"""Tool registry.

A tool is a small class with:

* an ``Args`` Pydantic model, which *is* the input schema,
* a ``spec`` describing permissions, risk, timeout and retry,
* an async ``execute``.

Registration happens once at start-up. Nothing is discovered dynamically at
request time, so the set of things PRIVIA can do is fixed and inspectable.
"""

from __future__ import annotations

import abc
import hashlib
import json
from collections.abc import Iterator
from typing import Any, ClassVar, Generic, TypeVar

from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from privia_shared.enums import RiskLevel, Scope
from privia_shared.errors import ToolInvalidArgumentsError, ToolNotFoundError
from privia_shared.tools import ConfirmationRequest, RetryPolicy, ToolResult, ToolSpec

from .context import ToolContext

ArgsT = TypeVar("ArgsT", bound=BaseModel)


class Tool(abc.ABC, Generic[ArgsT]):
    """Base class for every tool."""

    name: ClassVar[str]
    family: ClassVar[str]
    description: ClassVar[str]
    scopes: ClassVar[tuple[Scope, ...]] = ()
    risk_level: ClassVar[RiskLevel] = RiskLevel.LOW
    requires_confirmation: ClassVar[bool] = False
    timeout_seconds: ClassVar[float] = 20.0
    retry_policy: ClassVar[RetryPolicy] = RetryPolicy()
    redact_input_keys: ClassVar[tuple[str, ...]] = ()
    returns_untrusted_content: ClassVar[bool] = False
    confirmation_template: ClassVar[str | None] = None
    Args: ClassVar[type[BaseModel]]
    Output: ClassVar[type[BaseModel] | None] = None

    # -- schema ---------------------------------------------------------------

    @classmethod
    def input_schema(cls) -> dict[str, Any]:
        return cls.Args.model_json_schema()

    @classmethod
    def output_schema(cls) -> dict[str, Any]:
        if cls.Output is not None:
            return cls.Output.model_json_schema()
        return {"type": "object", "description": "Tool specific payload."}

    @classmethod
    def spec(cls) -> ToolSpec:
        return ToolSpec(
            name=cls.name,
            family=cls.family,
            description=cls.description,
            input_schema=cls.input_schema(),
            output_schema=cls.output_schema(),
            scopes=cls.scopes,
            risk_level=cls.risk_level,
            requires_confirmation=cls.requires_confirmation,
            timeout_seconds=cls.timeout_seconds,
            retry_policy=cls.retry_policy,
            redact_input_keys=cls.redact_input_keys,
            confirmation_template=cls.confirmation_template,
            returns_untrusted_content=cls.returns_untrusted_content,
        )

    # -- lifecycle ------------------------------------------------------------

    @classmethod
    def parse_args(cls, arguments: dict[str, Any]) -> BaseModel:
        try:
            return cls.Args.model_validate(arguments or {})
        except PydanticValidationError as exc:
            raise ToolInvalidArgumentsError(
                _format_validation_error(exc, cls.name),
                details={"tool": cls.name, "errors": _compact_errors(exc)},
            ) from exc

    def confirmation_id(self, args: BaseModel, ctx: ToolContext) -> str:
        """A confirmation id derived from the session, the tool and the exact arguments.

        Deterministic rather than random, which buys three properties:

        * Approving in one turn and executing in the next works, because the
          re-planned call hashes to the same id. Scoping to the *session* rather
          than the run is what makes that possible - a new turn is a new run.
        * Changing **any** argument yields a different id, so an approval can
          never be replayed against different content. A model that drafts an
          email, gets it approved, then swaps the recipient is stopped cold.
        * The id leaks nothing: it is a hash, and it is meaningless in another
          session.

        Freshness (expiry, single use) is enforced separately by the stored
        confirmation record, so a stale approval cannot be replayed later.
        """
        payload = json.dumps(
            {
                "session": ctx.session_id,
                "tool": self.name,
                "args": args.model_dump(mode="json"),
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:26].upper()
        return f"cfm_{digest}"

    def resources(self, args: ArgsT, ctx: ToolContext) -> tuple[str, ...]:
        """Concrete resources this call will touch, for the permission check."""
        return ()

    def confirmation(self, args: ArgsT, ctx: ToolContext) -> ConfirmationRequest | None:
        """Build the preview the user approves. Required for confirming tools."""
        return None

    @abc.abstractmethod
    async def execute(self, args: ArgsT, ctx: ToolContext) -> ToolResult:
        """Do the work. Raise a ``PriviaError`` on failure; the runtime wraps it."""


class ToolRegistry:
    """Name -> tool instance."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool[Any]] = {}

    def register(self, tool: Tool[Any]) -> Tool[Any]:
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' is already registered.")
        expected_family = tool.name.split(".", 1)[0]
        if tool.family != expected_family:
            raise ValueError(
                f"Tool '{tool.name}' declares family '{tool.family}' but its name implies "
                f"'{expected_family}'."
            )
        self._tools[tool.name] = tool
        return tool

    def register_all(self, tools: list[Tool[Any]]) -> None:
        for tool in tools:
            self.register(tool)

    def get(self, name: str) -> Tool[Any]:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolNotFoundError(
                f"There is no tool called '{name}'.",
                details={"tool": name, "available": sorted(self._tools)},
            ) from exc

    def has(self, name: str) -> bool:
        return name in self._tools

    def specs(self) -> list[ToolSpec]:
        return [tool.spec() for tool in sorted(self._tools.values(), key=lambda t: t.name)]

    def names(self) -> list[str]:
        return sorted(self._tools)

    def by_family(self, family: str) -> list[Tool[Any]]:
        return [t for t in self._tools.values() if t.family == family]

    def __len__(self) -> int:
        return len(self._tools)

    def __iter__(self) -> Iterator[Tool[Any]]:
        return iter(sorted(self._tools.values(), key=lambda t: t.name))

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._tools


def _compact_errors(exc: PydanticValidationError) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for error in exc.errors()[:8]:
        out.append(
            {
                "field": ".".join(str(p) for p in error.get("loc", ())) or "(root)",
                "problem": error.get("msg", "invalid"),
                "type": error.get("type", "value_error"),
            }
        )
    return out


def _format_validation_error(exc: PydanticValidationError, tool_name: str) -> str:
    parts = []
    for error in _compact_errors(exc)[:3]:
        parts.append(f"{error['field']}: {error['problem']}")
    detail = "; ".join(parts) or "the arguments did not match the tool's schema"
    return f"'{tool_name}' was called with invalid arguments ({detail})."
