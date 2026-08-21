"""Conversation endpoints."""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from privia_shared.agent import ChatRequest, ChatResponse
from privia_shared.enums import Intent, MessageRole, ProcessingLocation, RunStatus
from privia_shared.errors import ConflictError, NotFoundError, TtsUnavailableError
from privia_shared.ids import utcnow

from ..container import build_tool_context
from ..deps import ContainerDep, RequestIdDep

router = APIRouter(prefix="/api/v1", tags=["chat"])


def _resolve_confirmation(container: ContainerDep, body: ChatRequest, session_id: str) -> set[str]:
    """Turn a confirmation decision into the approved-id set for this turn.

    The stored record is the authority: it must exist, belong to this session,
    be unresolved, and not have expired. A client cannot approve an action by
    inventing an id.
    """
    if not body.confirmation_id:
        return set()
    record = container.repositories.confirmations.get(body.confirmation_id)
    if record is None:
        raise NotFoundError(
            "That confirmation is no longer available. Ask again and I will show you a "
            "fresh preview.",
            details={"confirmation_id": body.confirmation_id},
        )
    if record.get("session_id") != session_id:
        raise NotFoundError("That confirmation belongs to a different conversation.")
    if record.get("resolved"):
        raise ConflictError("That confirmation was already answered.")
    expires_at = str(record.get("expires_at") or "")
    if expires_at and expires_at < utcnow().isoformat():
        container.repositories.confirmations.resolve(body.confirmation_id, False)
        raise ConflictError(
            "That confirmation expired. Ask again and I will show you a fresh preview."
        )
    approved = bool(body.confirm)
    container.repositories.confirmations.resolve(body.confirmation_id, approved)
    container.audit.record(
        "confirmation.approved" if approved else "confirmation.rejected",
        session_id=session_id,
        tool_name=record.get("tool_name"),
        target=str(record.get("payload", {}).get("target") or ""),
        outcome="success" if approved else "denied",
    )
    return {body.confirmation_id} if approved else set()


@router.post("/chat", response_model=ChatResponse, summary="Send a message")
async def chat(
    body: ChatRequest, container: ContainerDep, request_id: RequestIdDep
) -> ChatResponse:
    session_id = container.repositories.sessions.ensure(body.session_id)
    approved = _resolve_confirmation(container, body, session_id)
    if body.confirmation_id and not approved:
        # The user said no. That is a complete answer: nothing runs, and the
        # rejection is already in the audit log.
        container.repositories.messages.add(
            session_id, MessageRole.ASSISTANT, "Understood, I have not done it."
        )
        return ChatResponse(
            run_id="",
            request_id=request_id,
            session_id=session_id,
            response="Understood, I have not done it.",
            status=RunStatus.DENIED,
            intent=Intent.UNKNOWN,
            processing_location=ProcessingLocation.LOCAL,
        )

    ctx = build_tool_context(container, session_id, request_id)
    ctx.approved_confirmations = approved
    run = await container.agent.run(body.message, ctx, request_id=request_id, prefer=body.prefer)

    audio: str | None = None
    if body.speak and run.response_text:
        try:
            audio = base64.b64encode(await container.tts.synthesize(run.response_text)).decode(
                "ascii"
            )
        except TtsUnavailableError:
            audio = None

    return ChatResponse(
        run_id=run.id,
        request_id=request_id,
        session_id=session_id,
        response=run.response_text,
        status=run.status,
        intent=run.classification.intent,
        processing_location=run.processing_location,
        model_used=run.model_used,
        tool_calls=run.tool_calls,
        tool_results=run.tool_results,
        pending_confirmation=run.pending_confirmation,
        accessed_resources=run.accessed_resources,
        permission_prompt=_permission_prompt(run),
        duration_ms=run.duration_ms,
        audio_base64=audio,
    )


def _permission_prompt(run: Any) -> dict[str, Any] | None:
    """Surface a missing-permission result as something the UI can act on."""
    for result in run.tool_results:
        if result.success or result.error_code != "TOOL_PERMISSION_DENIED":
            continue
        details = (result.metadata or {}).get("details", {})
        if details.get("decision") != "prompt":
            continue
        return {
            "tool_name": result.tool_name,
            "missing_scopes": details.get("missing_scopes", []),
            "resources": details.get("resources", []),
            "out_of_scope_resources": details.get("out_of_scope_resources", []),
            "rationale": result.error,
        }
    return None


@router.post("/chat/stream", summary="Send a message and stream the reply")
async def chat_stream(
    body: ChatRequest, container: ContainerDep, request_id: RequestIdDep, request: Request
) -> StreamingResponse:
    """Server-sent events: status, tool, confirmation, token, done."""
    session_id = container.repositories.sessions.ensure(body.session_id)
    approved = _resolve_confirmation(container, body, session_id)
    ctx = build_tool_context(container, session_id, request_id)
    ctx.approved_confirmations = approved

    async def generate() -> AsyncIterator[str]:
        yield _sse({"type": "start", "session_id": session_id, "request_id": request_id})
        try:
            async for event in container.agent.stream_response(
                body.message, ctx, request_id=request_id, prefer=body.prefer
            ):
                if await request.is_disconnected():
                    break
                yield _sse(event)
        except Exception as exc:
            container.logger.error("chat.stream_failed", error=type(exc).__name__)
            yield _sse(
                {
                    "type": "error",
                    "code": "INTERNAL_ERROR",
                    "message": "The reply stopped unexpectedly. Nothing was changed.",
                }
            )
        yield "event: end\ndata: {}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "cache-control": "no-cache",
            "connection": "keep-alive",
            "x-accel-buffering": "no",
        },
    )


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, default=str)}\n\n"
