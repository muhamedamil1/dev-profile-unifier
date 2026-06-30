from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

class APIWarning(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ProfileSourceAPI(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source: str
    handle: str | None = None
    display_name: str | None = None
    profile_url: str | None = None
    website_url: str | None = None
    relationship_type: str | None = None
    verification_status: str | None = None
    confidence_score: float | None = None
    decision: str | None = None
    reason: str | None = None
    rationale: str | None = None
    decision_payload: dict[str, Any] = Field(default_factory=dict)
    evidence_summary: dict[str, Any] = Field(default_factory=dict)
    evidence_confidence_score: float | None = None
    decision_confidence_score: float | None = None
    accepted_as_anchor: bool | None = None
    is_anchor: bool | None = None
    hn_conservative: bool | None = None
    decision_basis: str | None = None
    risk_level: str | None = None


class ReviewCandidateAPI(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source_account_key: str | None = None
    source: str | None = None
    handle: str | None = None
    display_name: str | None = None
    profile_url: str | None = None
    confidence_score: float | None = None
    reason: str | None = None
    decision_payload: dict[str, Any] = Field(default_factory=dict)


class AISummaryAPI(BaseModel):
    summary_id: UUID | None = None
    model: str | None = None
    prompt_version: str | None = None
    headline: str | None = None
    short_summary: str | None = None
    strengths: list[str] = Field(default_factory=list)
    source_note: str | None = None
    limitations: list[str] = Field(default_factory=list)
    used_fallback: bool = False
    safety_flags: list[str] = Field(default_factory=list)


class ProfileDetailResponse(BaseModel):
    profile_id: UUID
    resolution_run_id: UUID | None = None
    status: Literal["found", "not_found"] = "found"
    display_name: str | None = None
    headline: str | None = None
    location: str | None = None
    bio: str | None = None
    primary_avatar_url: str | None = None
    primary_website_url: str | None = None
    inferred_skills: list[str] = Field(default_factory=list)
    confidence_level: str | None = None
    profile_stage: str | None = None
    canonical_fields_pending: bool | None = None
    sources: list[ProfileSourceAPI] = Field(default_factory=list)
    review_candidates: list[ReviewCandidateAPI] = Field(default_factory=list)
    rejected_candidates: list[ReviewCandidateAPI] = Field(default_factory=list)
    ai_summary: AISummaryAPI | None = None
    evidence_summary: dict[str, Any] = Field(default_factory=dict)
    resolution_summary: dict[str, Any] = Field(default_factory=dict)
    warnings: list[APIWarning] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ProfileResolveAPIResponse(ProfileDetailResponse):
    request: dict[str, Any] | None = None
    outcome: str | None = None
    message: str | None = None
    resolution_status: str | None = None
    resolution_duration_ms: int | None = None
    raw_result_summary: dict[str, Any] = Field(default_factory=dict)


class ProfileResolveOptions(BaseModel):
    build_summary: bool = True
    allow_summary_fallback: bool = True
    replace_existing_summary: bool = True
    persist: bool = True


class ProfileResolveEnvelope(BaseModel):
    """Optional wrapper if the API wants request + execution options later.

    The route accepts ProfileResolveRequest directly for assignment compatibility.
    This schema is kept for internal tests and future expansion.
    """

    request: dict[str, Any]
    options: ProfileResolveOptions = Field(default_factory=ProfileResolveOptions)

