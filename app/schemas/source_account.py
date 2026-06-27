from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.enums import PlatformSource


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []

    for value in values:
        cleaned = value.strip()
        if not cleaned:
            continue

        key = cleaned.lower()
        if key not in seen:
            seen.add(key)
            output.append(cleaned)

    return output


class RawSourceRecord(BaseModel):
    """
    Pydantic representation of raw_source_records.

    This is used before normalization so the system can preserve original API
    payloads and re-run resolution logic later without refetching data.
    """

    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    id: UUID | None = None
    resolution_run_id: UUID

    source: PlatformSource
    source_record_type: str = Field(..., min_length=1, max_length=120)

    source_user_id: str | None = None
    handle: str | None = None

    request_url: str | None = None
    profile_url: str | None = None

    http_status: int | None = Field(default=None, ge=100, le=599)

    raw_payload: dict[str, Any] | list[Any]

    payload_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @field_validator("source_record_type")
    @classmethod
    def clean_record_type(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("source_record_type must not be empty")
        return cleaned

    @field_validator("source_user_id", "handle", "request_url", "profile_url", mode="before")
    @classmethod
    def clean_optional_text(cls, value: Any) -> str | None:
        if value is None:
            return None

        cleaned = str(value).strip()
        return cleaned or None

    def calculate_payload_hash(self) -> str:
        """
        Deterministic SHA-256 hash of the raw API payload.

        sort_keys=True makes dict ordering stable across runs.
        """
        payload_json = json.dumps(
            self.raw_payload,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(payload_json.encode("utf-8")).hexdigest()

    def to_db_payload(self) -> dict[str, Any]:
        data = self.model_dump(mode="json", exclude_none=True)

        if not data.get("payload_sha256"):
            data["payload_sha256"] = self.calculate_payload_hash()

        return data


class SourceAccount(BaseModel):
    """
    Common normalized account shape across GitHub, Stack Overflow, dev.to,
    and Hacker News.

    Resolution logic should compare SourceAccount objects, not raw API payloads.
    """

    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    id: UUID | None = None

    source: PlatformSource
    source_user_id: str | None = None
    handle: str | None = None

    source_account_key: str | None = None

    display_name: str | None = None
    bio: str | None = None
    location: str | None = None
    website_url: str | None = None
    profile_url: str | None = None
    avatar_url: str | None = None

    email_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    company: str | None = None

    topics: list[str] = Field(default_factory=list)
    outbound_links: list[str] = Field(default_factory=list)

    activity_payload: dict[str, Any] = Field(default_factory=dict)

    raw_source_record_id: UUID | None = None

    @field_validator(
        "source_user_id",
        "handle",
        "source_account_key",
        "display_name",
        "bio",
        "location",
        "website_url",
        "profile_url",
        "avatar_url",
        "company",
        mode="before",
    )
    @classmethod
    def clean_optional_text(cls, value: Any) -> str | None:
        if value is None:
            return None

        cleaned = str(value).strip()
        return cleaned or None

    @field_validator("handle")
    @classmethod
    def normalize_handle_shape(cls, value: str | None) -> str | None:
        if value is None:
            return None

        cleaned = value.strip().lstrip("@")
        return cleaned or None

    @field_validator("topics", mode="before")
    @classmethod
    def clean_topics(cls, value: Any) -> list[str]:
        if value is None:
            return []

        if not isinstance(value, list):
            raise ValueError("topics must be a list")

        cleaned = [str(item).strip().lower() for item in value if str(item).strip()]
        return _dedupe_preserve_order(cleaned)

    @field_validator("outbound_links", mode="before")
    @classmethod
    def clean_outbound_links(cls, value: Any) -> list[str]:
        if value is None:
            return []

        if not isinstance(value, list):
            raise ValueError("outbound_links must be a list")

        cleaned = [str(item).strip() for item in value if str(item).strip()]
        return _dedupe_preserve_order(cleaned)

    @model_validator(mode="after")
    def validate_identity(self) -> SourceAccount:
        if not self.source_user_id and not self.handle:
            raise ValueError("SourceAccount requires either source_user_id or handle")

        return self

    def expected_source_account_key(self) -> str:
        """
        Mirrors the database trigger logic.

        The DB trigger remains the source of truth, but this helps repositories
        perform predictable upserts and tests without guessing.
        """
        identity_value = self.source_user_id or self.handle

        if not identity_value:
            raise ValueError("Cannot compute source_account_key without source_user_id or handle")

        return f"{self.source.value}:{identity_value}".lower()

    def to_db_payload(self) -> dict[str, Any]:
        data = self.model_dump(mode="json", exclude_none=True)

        data["source_account_key"] = self.expected_source_account_key()

        return data


class SourceAccountActivity(BaseModel):
    """
    Standard activity summary used inside API responses.

    Platform-specific fields can remain None. This keeps the response stable
    while still supporting source-specific activity.
    """

    model_config = ConfigDict(validate_assignment=True)

    public_repos: int | None = Field(default=None, ge=0)
    followers: int | None = Field(default=None, ge=0)
    top_languages: list[str] = Field(default_factory=list)

    articles_published: int | None = Field(default=None, ge=0)
    top_tags: list[str] = Field(default_factory=list)

    stackoverflow_reputation: int | None = Field(default=None, ge=0)
    stackoverflow_top_tags: list[str] = Field(default_factory=list)

    submissions: int | None = Field(default=None, ge=0)
    comments: int | None = Field(default=None, ge=0)

    extra: dict[str, Any] = Field(default_factory=dict)