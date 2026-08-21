"""The single error envelope used by every endpoint.

    {"error": {"code": "...", "message": "...", "request_id": "...", "details": {}}}

Stack traces never reach the client. Unexpected exceptions become a generic
``INTERNAL_ERROR`` with the request id, which is enough to find the full detail
in the local log.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from privia_observability.logging import get_logger
from privia_security.redaction import redact_mapping
from privia_shared.enums import ErrorCode
from privia_shared.errors import PriviaError

logger = get_logger("api.errors")


def error_payload(
    code: str, message: str, request_id: str | None, details: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "request_id": request_id,
            "details": redact_mapping(details or {}),
        }
    }


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(PriviaError)
    async def _privia(request: Request, exc: PriviaError) -> JSONResponse:
        request_id = _request_id(request)
        logger.info(
            "api.error",
            code=str(exc.code),
            path=request.url.path,
            status=exc.http_status,
            request_id=request_id,
        )
        return JSONResponse(
            status_code=exc.http_status,
            content=error_payload(str(exc.code), exc.message, request_id, exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        problems = [
            {
                "field": ".".join(str(p) for p in err.get("loc", ()) if p != "body") or "(body)",
                "problem": err.get("msg", "invalid"),
            }
            for err in exc.errors()[:8]
        ]
        summary = "; ".join(f"{p['field']}: {p['problem']}" for p in problems[:3])
        return JSONResponse(
            status_code=422,
            content=error_payload(
                str(ErrorCode.VALIDATION_ERROR),
                f"The request did not validate ({summary}).",
                _request_id(request),
                {"problems": problems},
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {
            400: ErrorCode.BAD_REQUEST,
            401: ErrorCode.UNAUTHORIZED,
            404: ErrorCode.NOT_FOUND,
            405: ErrorCode.BAD_REQUEST,
            409: ErrorCode.CONFLICT,
            413: ErrorCode.PAYLOAD_TOO_LARGE,
            429: ErrorCode.RATE_LIMITED,
        }.get(exc.status_code, ErrorCode.INTERNAL_ERROR)
        message = str(exc.detail) if exc.detail else "The request could not be completed."
        return JSONResponse(
            status_code=exc.status_code,
            content=error_payload(str(code), message, _request_id(request)),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        request_id = _request_id(request)
        logger.exception(
            "api.unhandled",
            error=type(exc).__name__,
            path=request.url.path,
            request_id=request_id,
        )
        return JSONResponse(
            status_code=500,
            content=error_payload(
                str(ErrorCode.INTERNAL_ERROR),
                "Something went wrong. The details are in your local log; nothing was sent "
                "anywhere.",
                request_id,
            ),
        )
