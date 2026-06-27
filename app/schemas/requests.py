from __future__ import annotations

import hashlib
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.enums import PlatformSource


_SIMPLE_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_GITHUB_HANDLE_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
_DEVTO_HANDLE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,49}$")
_HACKERNEWS_HANDLE_RE = re.compile(r"^[A-Za-z0-9_-]{1,100}$")


def _clean_optional_handle(value: Any) -> str | None:
    if value is None:
        return None

    cleaned = str(value).strip().lstrip("@")

    if not cleaned:
        return None

    return cleaned


class ProfileResolveRequest(BaseModel):
    """
    Request body for POST /profiles/resolve.

    The API accepts a required name and optional platform identifiers.
    Public APIs cannot prove account ownership, so these fields are treated as
    matching hints, not absolute verification.
    """

    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="forbid",
    )

    name: str = Field(..., min_length=1, max_length=200)

    github: str | None = Field(default=None, max_length=100)
    devto: str | None = Field(default=None, max_length=100)
    hackernews: str | None = Field(default=None, max_length=100)
    stackoverflow_user_id: str | None = Field(default=None, max_length=40)

    email_hint: str | None = Field(
        default=None,
        max_length=320,
        description=(
            "Optional email hint used only for hashed comparison. "
            "The raw email should not be stored in resolution_runs."
        ),
    )

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        cleaned = re.sub(r"\s+", " ", value.strip())
        if not cleaned:
            raise ValueError("name must not be empty")
        return cleaned

    @field_validator("github", "devto", "hackernews", mode="before")
    @classmethod
    def clean_platform_handles(cls, value: Any) -> str | None:
        return _clean_optional_handle(value)

    @field_validator("github")
    @classmethod
    def validate_github_handle(cls, value: str | None) -> str | None:
        if value is None:
            return None

        if "/" in value or "\\" in value:
            raise ValueError("github handle must not be a URL or path")

        if not _GITHUB_HANDLE_RE.fullmatch(value):
            raise ValueError(
                "github handle must be 1-39 characters, use letters/numbers/hyphens, "
                "and not start or end with a hyphen"
            )

        return value

    @field_validator("devto")
    @classmethod
    def validate_devto_handle(cls, value: str | None) -> str | None:
        if value is None:
            return None

        if "/" in value or "\\" in value:
            raise ValueError("devto handle must not be a URL or path")

        if not _DEVTO_HANDLE_RE.fullmatch(value):
            raise ValueError("devto handle contains invalid characters")

        return value

    @field_validator("hackernews")
    @classmethod
    def validate_hackernews_handle(cls, value: str | None) -> str | None:
        if value is None:
            return None

        if "/" in value or "\\" in value:
            raise ValueError("hackernews handle must not be a URL or path")

        if not _HACKERNEWS_HANDLE_RE.fullmatch(value):
            raise ValueError("hackernews handle contains invalid characters")

        return value

    @field_validator("stackoverflow_user_id", mode="before")
    @classmethod
    def clean_stackoverflow_user_id(cls, value: Any) -> str | None:
        cleaned = _clean_optional_handle(value)
        if cleaned is None:
            return None
        return cleaned

    @field_validator("stackoverflow_user_id")
    @classmethod
    def validate_stackoverflow_user_id(cls, value: str | None) -> str | None:
        if value is None:
            return None

        if not re.fullmatch(r"\d+", value):
            raise ValueError("stackoverflow_user_id must be numeric")

        return value

    @field_validator("email_hint", mode="before")
    @classmethod
    def clean_email_hint(cls, value: Any) -> str | None:
        if value is None:
            return None

        cleaned = str(value).strip().lower()
        if not cleaned:
            return None

        return cleaned

    @field_validator("email_hint")
    @classmethod
    def validate_email_hint(cls, value: str | None) -> str | None:
        if value is None:
            return None

        if not _SIMPLE_EMAIL_RE.fullmatch(value):
            raise ValueError("email_hint must be a valid email-like value")

        return value

    @model_validator(mode="after")
    def require_at_least_name(self) -> ProfileResolveRequest:
        if not self.name.strip():
            raise ValueError("name must not be empty")
        return self

    @property
    def provided_sources(self) -> list[PlatformSource]:
        sources: list[PlatformSource] = []

        if self.github:
            sources.append(PlatformSource.GITHUB)
        if self.devto:
            sources.append(PlatformSource.DEVTO)
        if self.hackernews:
            sources.append(PlatformSource.HACKERNEWS)
        if self.stackoverflow_user_id:
            sources.append(PlatformSource.STACKOVERFLOW)

        return sources

    def platform_inputs(self) -> dict[PlatformSource, str]:
        inputs: dict[PlatformSource, str] = {}

        if self.github:
            inputs[PlatformSource.GITHUB] = self.github
        if self.devto:
            inputs[PlatformSource.DEVTO] = self.devto
        if self.hackernews:
            inputs[PlatformSource.HACKERNEWS] = self.hackernews
        if self.stackoverflow_user_id:
            inputs[PlatformSource.STACKOVERFLOW] = self.stackoverflow_user_id

        return inputs

    def email_hint_sha256(self) -> str | None:
        if not self.email_hint:
            return None

        return hashlib.sha256(self.email_hint.strip().lower().encode("utf-8")).hexdigest()

    def safe_input_payload(self) -> dict[str, Any]:
        """
        Safe request payload for resolution_runs.input_payload.

        This intentionally avoids storing the raw email_hint. If an email hint is
        provided, only its SHA-256 hash is stored.
        """
        payload: dict[str, Any] = {
            "name": self.name,
        }

        if self.github:
            payload["github"] = self.github

        if self.devto:
            payload["devto"] = self.devto

        if self.hackernews:
            payload["hackernews"] = self.hackernews

        if self.stackoverflow_user_id:
            payload["stackoverflow_user_id"] = self.stackoverflow_user_id

        email_hash = self.email_hint_sha256()
        if email_hash:
            payload["email_hint_sha256"] = email_hash
            payload["email_hint_present"] = True
        else:
            payload["email_hint_present"] = False

        return payload
