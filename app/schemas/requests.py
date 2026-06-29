from __future__ import annotations

import hashlib
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.enums import PlatformSource


_SIMPLE_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_EMAIL_SEARCH_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)


def _clean_optional_platform_value(value: Any) -> str | None:
    if value is None:
        return None

    cleaned = str(value).strip()
    if not cleaned:
        return None

    if cleaned.startswith("@"):
        cleaned = cleaned[1:].strip()

    return cleaned or None


class ProfileResolveRequest(BaseModel):
    """
    Request body for POST /profiles/resolve.

    Platform fields may contain either handles/IDs or profile URLs.
    CandidateDiscoveryService is responsible for extracting and validating
    platform-specific identifiers.
    """

    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="forbid",
    )

    name: str = Field(..., min_length=1, max_length=200)

    github: str | None = Field(default=None, max_length=500)
    devto: str | None = Field(default=None, max_length=500)
    hackernews: str | None = Field(default=None, max_length=500)
    stackoverflow_user_id: str | None = Field(default=None, max_length=500)

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

    @field_validator("github", "devto", "hackernews", "stackoverflow_user_id", mode="before")
    @classmethod
    def clean_platform_values(cls, value: Any) -> str | None:
        return _clean_optional_platform_value(value)

    @field_validator("stackoverflow_user_id")
    @classmethod
    def validate_stackoverflow_user_or_url(cls, value: str | None) -> str | None:
        if value is None:
            return None

        lower_value = value.lower()

        if value.isdigit():
            return value

        if "stackoverflow.com/" in lower_value:
            return value

        raise ValueError(
            "stackoverflow_user_id must be a numeric user ID or a Stack Overflow user profile URL"
        )

    @field_validator("email_hint", mode="before")
    @classmethod
    def clean_email_hint(cls, value: Any) -> str | None:
        if value is None:
            return None

        cleaned = str(value).strip().lower()
        if not cleaned:
            return None

        email_match = _EMAIL_SEARCH_RE.search(cleaned)
        if email_match:
            return email_match.group(0).lower()

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
    def require_name(self) -> ProfileResolveRequest:
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

        The raw email_hint is never stored.
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
