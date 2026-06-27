from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.enums import (
    ConflictSeverity,
    EvidenceDirection,
    MatchDecision,
    ProfileConfidenceLevel,
    ResolutionStatus,
    SourceRelationshipType,
    VerificationStatus,
)
from app.schemas.metrics import LLMHealthMetrics, ProfileHealthMetrics, RateLimitState, SourceHealthMetric


class FailedSource(BaseModel):
    source: str
    reason: str
    detail: str | None = None


class ResolveResponse(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    profile_id: UUID | None
    resolution_run_id: UUID

    status: ResolutionStatus
    confidence_level: ProfileConfidenceLevel

    sources_checked: list[str] = Field(default_factory=list)
    matched_sources: list[str] = Field(default_factory=list)
    needs_review_sources: list[str] = Field(default_factory=list)
    rejected_sources: list[str] = Field(default_factory=list)
    failed_sources: list[FailedSource] = Field(default_factory=list)

    duration_ms: int = Field(..., ge=0)


class EvidenceItem(BaseModel):
    signal: str
    direction: EvidenceDirection
    weight: float
    explanation: str


class SourceActivity(BaseModel):
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


class LinkedSourceAccount(BaseModel):
    source: str
    handle: str | None = None
    source_user_id: str | None = None

    profile_url: str | None = None
    avatar_url: str | None = None
    website_url: str | None = None

    decision: MatchDecision
    relationship_type: SourceRelationshipType
    confidence_score: float = Field(..., ge=0, le=1)
    verification_status: VerificationStatus

    evidence: list[EvidenceItem] = Field(default_factory=list)

    activity: SourceActivity | None = None


class SourceGroups(BaseModel):
    matched: list[LinkedSourceAccount] = Field(default_factory=list)
    needs_review: list[LinkedSourceAccount] = Field(default_factory=list)
    rejected: list[LinkedSourceAccount] = Field(default_factory=list)


class ConflictItem(BaseModel):
    field: str
    severity: ConflictSeverity
    impact: float = Field(..., le=0)
    source_values: list[dict[str, Any]] = Field(default_factory=list)
    explanation: str


class ActivitySummary(BaseModel):
    github_repos: int | None = Field(default=None, ge=0)
    devto_articles: int | None = Field(default=None, ge=0)
    stackoverflow_reputation: int | None = Field(default=None, ge=0)
    hackernews_items: int | None = Field(default=None, ge=0)


class ProfileResponse(BaseModel):
    id: UUID

    display_name: str | None = None
    headline: str | None = None
    location: str | None = None
    bio: str | None = None

    primary_website_url: str | None = None
    primary_avatar_url: str | None = None

    inferred_skills: list[str] = Field(default_factory=list)

    confidence_level: ProfileConfidenceLevel

    summary: str | None = None

    sources: SourceGroups = Field(default_factory=SourceGroups)

    conflicts: list[ConflictItem] = Field(default_factory=list)

    activity_summary: ActivitySummary | None = None

    resolution_run_id: UUID | None = None


class HealthResponse(BaseModel):
    status: str
    timestamp: datetime

    service: str
    version: str
    environment: str

    profiles: ProfileHealthMetrics = Field(default_factory=ProfileHealthMetrics)

    external_api_calls: dict[str, SourceHealthMetric] = Field(default_factory=dict)

    github_rate_limit: RateLimitState | None = None

    llm: LLMHealthMetrics = Field(default_factory=LLMHealthMetrics)

    checks: dict[str, str] = Field(default_factory=dict)