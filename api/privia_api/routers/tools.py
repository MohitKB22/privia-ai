"""Tool catalogue and direct execution."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from privia_shared.errors import ConfirmationRequiredError, ConflictError, NotFoundError
from privia_shared.ids import utcnow
from privia_shared.tools import ToolCall, ToolResult, ToolSpec

from ..container import build_tool_context
from ..deps import ContainerDep, RequestIdDep

router = APIRouter(prefix="/api/v1/tools", tags=["tools"])


class ExecuteRequest(BaseModel):
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    session_id: str | None = None
    confirmation_id: str | None = Field(
        default=None, description="Id returned by a previous confirmation-required response."
    )


@router.get("", response_model=list[ToolSpec], summary="List every registered tool")
async def list_tools(container: ContainerDep) -> list[ToolSpec]:
    return container.runtime.specs()


@router.get("/{tool_name}", response_model=ToolSpec, summary="Describe one tool")
async def describe_tool(tool_name: str, container: ContainerDep) -> ToolSpec:
    return container.runtime.describe(tool_name)


@router.post("/execute", response_model=ToolResult, summary="Run one tool directly")
async def execute(
    body: ExecuteRequest, container: ContainerDep, request_id: RequestIdDep
) -> ToolResult:
    """Run a tool without the agent.

    The same runtime, the same permission checks and the same confirmation gate
    apply. This endpoint exists so the UI can act on a button press; it is not a
    way around the policy engine.
    """
    approved: set[str] = set()
    session_id: str

    if body.confirmation_id:
        # Resume inside the session the confirmation was issued for. Deriving the
        # session from the stored record rather than from the request body means a
        # caller cannot approve one session's action from another, and cannot
        # sidestep the check by omitting session_id entirely.
        record = container.repositories.confirmations.get(body.confirmation_id)
        if record is None:
            raise NotFoundError(
                "That confirmation is no longer available. Ask again for a fresh preview.",
                details={"confirmation_id": body.confirmation_id},
            )
        if record.get("resolved"):
            raise ConflictError("That confirmation was already answered.")
        if str(record.get("expires_at") or "") < utcnow().isoformat():
            container.repositories.confirmations.resolve(body.confirmation_id, False)
            raise ConflictError("That confirmation expired. Ask again for a fresh preview.")
        if record.get("tool_name") != body.tool_name:
            raise ConflictError("That confirmation was issued for a different tool.")
        session_id = str(record["session_id"])
        container.repositories.confirmations.resolve(body.confirmation_id, True)
        approved = {body.confirmation_id}
    else:
        session_id = container.repositories.sessions.ensure(body.session_id)

    ctx = build_tool_context(container, session_id, request_id)
    ctx.approved_confirmations = approved

    call = ToolCall(tool_name=body.tool_name, arguments=body.arguments)
    try:
        return await container.runtime.execute(call, ctx)
    except ConfirmationRequiredError as exc:
        confirmation = exc.details.get("confirmation")
        if confirmation:
            from privia_shared.tools import ConfirmationRequest

            container.repositories.confirmations.create(
                ConfirmationRequest.model_validate(confirmation), session_id
            )
        raise
