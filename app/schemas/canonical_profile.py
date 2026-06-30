from __future__ import annotations

from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class CanonicalBuildStatus(StrEnum):
    BUILT = "built"
    BLOCKED_NO_AUTO_MATCH = "blocked_no_auto_match"
    PROFILE_NOT_FOUND = "profile_not_found"


class CanonicalFieldSelection(BaseModel):
    field_name: str
    value: Any = None
    strategy: str
    confidence_score: float = Field(default=0.0, ge=0.0, le=1.0)
    source_account_keys: list[str] = Field(default_factory=list)
    source_account_ids: list[UUID] = Field(default_factory=list)


class CanonicalPlatformProfile(BaseModel):
    source: str
    source_account_key: str
    source_account_id: UUID
    handle: str | None = None
    profile_url: str | None = None
    decision: str
    relationship_type: str | None = None
    verification_status: str | None = None
    confidence_score: float = 0.0
    decision_payload: dict[str, Any] = Field(default_factory=dict)
    reason: str | None = None
    evidence_confidence_score: float | None = None
    decision_confidence_score: float | None = None
    accepted_as_anchor: bool | None = None
    hn_conservative: bool | None = None
    decision_basis: str | None = None
    risk_level: str | None = None
    is_anchor: bool = False


class CanonicalReviewCandidate(BaseModel):
    source: str
    source_account_key: str
    source_account_id: UUID
    handle: str | None = None
    profile_url: str | None = None
    decision: str
    confidence_score: float = 0.0
    reason: str | None = None


class CanonicalActivitySummary(BaseModel):
    accepted_source_count: int = 0
    review_source_count: int = 0
    rejected_source_count: int = 0
    accepted_sources: list[str] = Field(default_factory=list)
    review_sources: list[str] = Field(default_factory=list)
    rejected_sources: list[str] = Field(default_factory=list)


class CanonicalProfileBuildResult(BaseModel):
    canonical_profile_id: UUID
    status: CanonicalBuildStatus
    updated: bool = False

    display_name: str | None = None
    headline: str | None = None
    location: str | None = None
    bio: str | None = None
    primary_avatar_url: str | None = None
    primary_website_url: str | None = None
    inferred_skills: list[str] = Field(default_factory=list)
    confidence_level: str = "low"

    field_sources: dict[str, CanonicalFieldSelection] = Field(default_factory=dict)
    platform_profiles: list[CanonicalPlatformProfile] = Field(default_factory=list)
    review_candidates: list[CanonicalReviewCandidate] = Field(default_factory=list)
    rejected_candidates: list[CanonicalReviewCandidate] = Field(default_factory=list)
    activity_summary: CanonicalActivitySummary = Field(default_factory=CanonicalActivitySummary)

    profile_payload: dict[str, Any] = Field(default_factory=dict)
