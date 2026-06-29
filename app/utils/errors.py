from __future__ import annotations

from typing import Any


class DevProfileUnifierError(Exception):
    """
    Base application exception.

    Every custom exception has:
    - a stable machine-readable code
    - an HTTP status code
    - a safe public message
    - optional non-secret details
    """

    code = "internal_error"
    status_code = 500
    public_message = "An unexpected application error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.public_message
        self.details = details or {}
        super().__init__(self.message)

    def to_response(self, request_id: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "error": {
                "code": self.code,
                "message": self.message,
            }
        }

        if self.details:
            payload["error"]["details"] = self.details

        if request_id:
            payload["request_id"] = request_id

        return payload



class AppError(DevProfileUnifierError):
    code = "application_error"
    status_code = 400
    public_message = "Application request could not be completed."

    def __init__(
        self,
        *,
        message: str,
        public_message: str | None = None,
        details: dict[str, Any] | None = None,
        status_code: int | None = None,
        code: str | None = None,
    ) -> None:
        self.public_message = public_message or message
        if status_code is not None:
            self.status_code = status_code
        if code is not None:
            self.code = code
        super().__init__(message, details=details)

    def to_response(self, request_id: str | None = None) -> dict[str, Any]:
        payload = super().to_response(request_id=request_id)
        payload["error"]["message"] = self.public_message
        return payload

class SettingsError(DevProfileUnifierError):
    code = "settings_error"
    status_code = 500
    public_message = "Application settings are invalid or incomplete."


class PlatformAPIError(DevProfileUnifierError):
    code = "platform_api_error"
    status_code = 502
    public_message = "External platform API request failed."

    def __init__(
        self,
        message: str = "External platform API request failed.",
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, details=details)


class PlatformNotFoundError(PlatformAPIError):
    code = "platform_not_found"
    status_code = 404
    public_message = "External platform account was not found."


class PlatformRateLimitError(PlatformAPIError):
    code = "platform_rate_limited"
    status_code = 429
    public_message = "External platform rate limit was reached."


class PlatformTimeoutError(PlatformAPIError):
    code = "platform_timeout"
    status_code = 504
    public_message = "External platform request timed out."


class ResolutionFailedError(DevProfileUnifierError):
    code = "resolution_failed"
    status_code = 422
    public_message = "Profile resolution failed."

    def __init__(
        self,
        message: str = "Profile resolution failed.",
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, details=details)


class ProfileNotFoundError(DevProfileUnifierError):
    code = "profile_not_found"
    status_code = 404
    public_message = "Profile not found."

    def __init__(self, profile_id: str) -> None:
        super().__init__(
            "Profile not found.",
            details={"profile_id": profile_id},
        )


class StorageError(DevProfileUnifierError):
    code = "storage_error"
    status_code = 500
    public_message = "Database operation failed."

    def __init__(
        self,
        message: str = "Database operation failed.",
        *,
        details: dict[str, Any] | None = None,
        internal_details: dict[str, Any] | None = None,
    ) -> None:
        self.internal_details = internal_details or {}
        super().__init__(message, details=details or {})


class LLMError(DevProfileUnifierError):
    code = "llm_error"
    status_code = 502
    public_message = "LLM summary generation failed."

    def __init__(
        self,
        message: str = "LLM summary generation failed.",
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, details=details)
