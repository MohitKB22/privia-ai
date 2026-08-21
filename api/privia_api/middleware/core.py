"""Request middleware.

Ordering (outermost first): security headers, request context, body-size limit,
authentication, rate limit. Authentication comes before rate limiting so an
unauthenticated flood cannot consume a legitimate caller's budget.
"""

from __future__ import annotations

import hmac
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

from privia_observability.logging import (
    current_request_id,
    current_session_id,
    get_logger,
)
from privia_observability.metrics import get_metrics
from privia_security.limits import RateLimiter
from privia_shared.config import Settings
from privia_shared.enums import ErrorCode
from privia_shared.ids import request_id as new_request_id

from ..errors import error_payload

logger = get_logger("api.http")
metrics = get_metrics()

#: Endpoints reachable without the local API token.
PUBLIC_PATHS = frozenset({"/health", "/api/v1/status", "/docs", "/openapi.json", "/redoc"})


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns a request id, times the request, and logs the outcome."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming = request.headers.get("x-request-id", "")
        request_id = incoming if incoming.startswith("req_") else new_request_id()
        request.state.request_id = request_id
        token = current_request_id.set(request_id)
        session_token = current_session_id.set(request.headers.get("x-session-id", ""))
        started = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            current_request_id.reset(token)
            current_session_id.reset(session_token)
        duration_ms = (time.perf_counter() - started) * 1000
        response.headers["x-request-id"] = request_id
        response.headers["server-timing"] = f"app;dur={duration_ms:.1f}"
        route = request.scope.get("route")
        path = getattr(route, "path", request.url.path)
        metrics.observe("http.request", duration_ms, path=path, method=request.method)
        metrics.increment("http.responses", status=str(response.status_code))
        if request.url.path not in ("/health", "/api/v1/metrics"):
            logger.info(
                "http.request",
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                duration_ms=round(duration_ms, 1),
            )
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Defensive headers. The API serves JSON to a local client only."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers.setdefault("x-content-type-options", "nosniff")
        response.headers.setdefault("x-frame-options", "DENY")
        response.headers.setdefault("referrer-policy", "no-referrer")
        response.headers.setdefault("cross-origin-opener-policy", "same-origin")
        response.headers.setdefault(
            "content-security-policy", "default-src 'none'; frame-ancestors 'none'"
        )
        response.headers.setdefault("cache-control", "no-store")
        response.headers.setdefault(
            "permissions-policy", "geolocation=(), camera=(), microphone=(self)"
        )
        return response


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Rejects oversized bodies before they are buffered."""

    def __init__(self, app: FastAPI, max_bytes: int) -> None:
        super().__init__(app)
        self.max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > self.max_bytes:
            return JSONResponse(
                status_code=413,
                content=error_payload(
                    str(ErrorCode.PAYLOAD_TOO_LARGE),
                    f"The request body exceeds the {self.max_bytes:,} byte limit.",
                    getattr(request.state, "request_id", None),
                    {"limit_bytes": self.max_bytes},
                ),
            )
        return await call_next(request)


class AuthMiddleware(BaseHTTPMiddleware):
    """Bearer-token auth, required only when PRIVIA is not bound to loopback.

    On loopback the operating system already restricts access to processes on
    this machine, and forcing a token there would only push users to store one
    in a file. Off loopback a token is mandatory and start-up refuses without it.
    """

    def __init__(self, app: FastAPI, token: str) -> None:
        super().__init__(app)
        self.token = token

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if not self.token or request.url.path in PUBLIC_PATHS or request.method == "OPTIONS":
            return await call_next(request)
        header = request.headers.get("authorization", "")
        presented = header[7:] if header.lower().startswith("bearer ") else ""
        if not presented or not hmac.compare_digest(presented, self.token):
            logger.warning("auth.rejected", path=request.url.path)
            return JSONResponse(
                status_code=401,
                content=error_payload(
                    str(ErrorCode.UNAUTHORIZED),
                    "A valid API token is required.",
                    getattr(request.state, "request_id", None),
                ),
                headers={"www-authenticate": "Bearer"},
            )
        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: FastAPI, limiter: RateLimiter) -> None:
        super().__init__(app)
        self.limiter = limiter

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path in ("/health", "/api/v1/metrics"):
            return await call_next(request)
        key = request.client.host if request.client else "local"
        try:
            self.limiter.check(key)
        except Exception:
            metrics.increment("http.rate_limited")
            return JSONResponse(
                status_code=429,
                content=error_payload(
                    str(ErrorCode.RATE_LIMITED),
                    "Too many requests. Please slow down.",
                    getattr(request.state, "request_id", None),
                ),
                headers={"retry-after": "10"},
            )
        return await call_next(request)


def install_middleware(app: FastAPI, settings: Settings, limiter: RateLimiter) -> None:
    # Starlette applies middleware bottom-up, so the last added runs first.
    # The type: ignore comments are Starlette's own limitation: add_middleware is
    # typed for ASGI callables and does not model BaseHTTPMiddleware subclasses
    # that take extra keyword arguments.
    app.add_middleware(RateLimitMiddleware, limiter=limiter)  # type: ignore[arg-type]
    app.add_middleware(AuthMiddleware, token=settings.privia_api_token)  # type: ignore[arg-type]
    app.add_middleware(
        BodySizeLimitMiddleware,  # type: ignore[arg-type]
        max_bytes=settings.max_upload_bytes,
    )
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origin_list),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["authorization", "content-type", "x-request-id", "x-session-id"],
        expose_headers=["x-request-id", "server-timing"],
        max_age=600,
    )
