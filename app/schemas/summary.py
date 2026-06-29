from __future__ import annotations

from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SummaryGenerationStatus(StrEnum):
    GENERATED = "generated"
    FALLBACK = "fallback"
    BLOCKED_PROFILE_NOT_READY = "blocked_profile_not_ready"


class SummarySafetyFlag(StrEnum):
    FORBIDDEN_CLAIM_REMOVED = "forbidden_claim_removed"
    INVALID_JSON_FALLBACK = "invalid_json_fallback"
    GEMINI_UNAVAILABLE_FALLBACK = "gemini_unavailable_fallback"
    EMPTY_RESPONSE_FALLBACK = "empty_response_fallback"


class SummaryPromptPayload(BaseModel):
    profile_id: UUID
    display_name: str | None = None
    headline: str | None = None
    bio: str | None = None
    location: str | None = None
    primary_website_url: str | None = None
    inferred_skills: list[str] = Field(default_factory=list)
    platform_profiles: list[dict[str, Any]] = Field(default_factory=list)
    deterministic_facts: list[dict[str, Any]] = Field(default_factory=list)
    field_sources: dict[str, Any] = Field(default_factory=dict)
    review_candidate_count: int = 0
    rejected_candidate_count: int = 0
    confidence_level: str | None = None


class StructuredProfileSummary(BaseModel):
    """Strict structured Gemini output contract.

    Extra keys are forbidden so ownership-like hallucinated fields such as
    `verified_accounts` or `confirmed_owner` cannot silently pass validation.
    """

    model_config = ConfigDict(extra="forbid")

    headline: str = Field(default="")
    short_summary: str = Field(default="")
    strengths: list[str] = Field(default_factory=list, max_length=8)
    source_note: str = Field(default="")
    limitations: list[str] = Field(default_factory=list, max_length=6)

    @field_validator("headline", "short_summary", "source_note", mode="before")
    @classmethod
    def clean_text(cls, value: Any) -> str:
        if value is None:
            return ""
        return " ".join(str(value).strip().split())

    @field_validator("strengths", "limitations", mode="before")
    @classmethod
    def clean_list(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            value = [value]
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in value:
            text = " ".join(str(item).strip().split())
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(text)
        return cleaned


class SummaryGenerationResult(BaseModel):
    profile_id: UUID
    status: SummaryGenerationStatus
    summary_id: UUID | None = None
    model: str
    prompt_version: str
    prompt_text: str
    summary: StructuredProfileSummary
    raw_model_text: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0
    safety_flags: list[SummarySafetyFlag] = Field(default_factory=list)
    persisted: bool = False
    used_fallback: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
