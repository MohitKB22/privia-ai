"""The single exception hierarchy used across PRIVIA.

Every error that can reach a user is a :class:`PriviaError` carrying a stable
:class:`~privia_shared.enums.ErrorCode`, a human-safe message, and structured
details. The API layer converts these into the documented error envelope; no
stack trace or internal path is ever exposed to the client.
"""

from __future__ import annotations

from typing import Any

from .enums import ErrorCode


class PriviaError(Exception):
    """Base class for all PRIVIA errors."""

    code: ErrorCode = ErrorCode.INTERNAL_ERROR
    http_status: int = 500
    #: User-facing default; subclasses may override, callers may pass their own.
    default_message: str = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
        code: ErrorCode | None = None,
        http_status: int | None = None,
    ) -> None:
        self.message = message or self.default_message
        self.details: dict[str, Any] = details or {}
        if code is not None:
            self.code = code
        if http_status is not None:
            self.http_status = http_status
        super().__init__(self.message)

    def to_dict(self, request_id: str | None = None) -> dict[str, Any]:
        return {
            "error": {
                "code": str(self.code),
                "message": self.message,
                "request_id": request_id,
                "details": self.details,
            }
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"{type(self).__name__}(code={self.code}, message={self.message!r})"


# --- Request / protocol ------------------------------------------------------


class BadRequestError(PriviaError):
    code = ErrorCode.BAD_REQUEST
    http_status = 400
    default_message = "The request could not be understood."


class ValidationError(PriviaError):
    code = ErrorCode.VALIDATION_ERROR
    http_status = 422
    default_message = "The request failed validation."


class UnauthorizedError(PriviaError):
    code = ErrorCode.UNAUTHORIZED
    http_status = 401
    default_message = "Authentication is required for this request."


class NotFoundError(PriviaError):
    code = ErrorCode.NOT_FOUND
    http_status = 404
    default_message = "The requested resource does not exist."


class ConflictError(PriviaError):
    code = ErrorCode.CONFLICT
    http_status = 409
    default_message = "The request conflicts with the current state."


class RateLimitedError(PriviaError):
    code = ErrorCode.RATE_LIMITED
    http_status = 429
    default_message = "Too many requests. Please slow down."


class PayloadTooLargeError(PriviaError):
    code = ErrorCode.PAYLOAD_TOO_LARGE
    http_status = 413
    default_message = "The payload exceeds the configured size limit."


class ConfigurationError(PriviaError):
    code = ErrorCode.CONFIGURATION_ERROR
    http_status = 500
    default_message = "PRIVIA is misconfigured."


# --- Tools -------------------------------------------------------------------


class ToolError(PriviaError):
    code = ErrorCode.TOOL_EXECUTION_FAILED
    http_status = 500
    default_message = "The tool failed to execute."


class ToolNotFoundError(ToolError):
    code = ErrorCode.TOOL_NOT_FOUND
    http_status = 404
    default_message = "No such tool is registered."


class ToolInvalidArgumentsError(ToolError):
    code = ErrorCode.TOOL_INVALID_ARGUMENTS
    http_status = 422
    default_message = "The tool arguments are invalid."


class ToolTimeoutError(ToolError):
    code = ErrorCode.TOOL_TIMEOUT
    http_status = 504
    default_message = "The tool timed out and was stopped."


class ToolOutputTooLargeError(ToolError):
    code = ErrorCode.TOOL_OUTPUT_TOO_LARGE
    http_status = 502
    default_message = "The tool produced more output than the configured limit."


# --- Permission / confirmation ----------------------------------------------


class PermissionDeniedError(PriviaError):
    code = ErrorCode.TOOL_PERMISSION_DENIED
    http_status = 403
    default_message = "Permission is required to access this resource."


class ConfirmationRequiredError(PriviaError):
    code = ErrorCode.CONFIRMATION_REQUIRED
    http_status = 428
    default_message = "This action requires your explicit confirmation."


# --- Security ----------------------------------------------------------------


class SecurityError(PriviaError):
    code = ErrorCode.POLICY_VIOLATION
    http_status = 403
    default_message = "The request was blocked by a security policy."


class PathNotAllowedError(SecurityError):
    code = ErrorCode.PATH_NOT_ALLOWED
    default_message = "That path is outside the folders you have allowed."


class PathTraversalError(SecurityError):
    code = ErrorCode.PATH_TRAVERSAL
    default_message = "The path escapes its allowed root."


class CommandNotAllowedError(SecurityError):
    code = ErrorCode.COMMAND_NOT_ALLOWED
    default_message = "That command is not on the allowlist."


class UrlNotAllowedError(SecurityError):
    code = ErrorCode.URL_NOT_ALLOWED
    default_message = "That URL is not allowed."


class SsrfBlockedError(SecurityError):
    code = ErrorCode.SSRF_BLOCKED
    default_message = "Requests to private or loopback addresses are blocked."


class PromptInjectionError(SecurityError):
    code = ErrorCode.PROMPT_INJECTION_DETECTED
    default_message = "Untrusted content tried to issue instructions and was quarantined."


# --- Models / services -------------------------------------------------------


class LLMUnavailableError(PriviaError):
    code = ErrorCode.LLM_UNAVAILABLE
    http_status = 503
    default_message = "No language model is currently available."


class LLMInvalidOutputError(PriviaError):
    code = ErrorCode.LLM_INVALID_OUTPUT
    http_status = 502
    default_message = "The model returned output that did not match the required schema."


class CloudDisabledError(PriviaError):
    code = ErrorCode.CLOUD_DISABLED
    http_status = 403
    default_message = "Cloud processing is disabled. Enable it in the Privacy Center first."


class SttUnavailableError(PriviaError):
    code = ErrorCode.STT_UNAVAILABLE
    http_status = 503
    default_message = "Speech-to-text is not available. You can keep typing instead."


class TtsUnavailableError(PriviaError):
    code = ErrorCode.TTS_UNAVAILABLE
    http_status = 503
    default_message = "Text-to-speech is not available on this machine."


class IntegrationUnavailableError(PriviaError):
    code = ErrorCode.INTEGRATION_UNAVAILABLE
    http_status = 503
    default_message = "That integration is not configured or not reachable."
