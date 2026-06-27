from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.enums import (
    ConflictSeverity,
    EvidenceDirection,
    MatchDecision,
    PlatformSource,
    SourceRelationshipType,
    VerificationStatus,
)
from app.schemas.source_account import SourceAccount


class MatchEvidence(BaseModel):
    """
    One structured reason for or against linking a SourceAccount to a profile.

    The database stores signed weights:
    - positive evidence must have signal_weight > 0
    - negative evidence must have signal_weight < 0
    - neutral evidence must have signal_weight = 0
    """

    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
        populate_by_name=True,
    )

    id: UUID | None = None

    profile_source_link_id: UUID | None = None

    source_account_a_id: UUID | None = None
    source_account_b_id: UUID | None = None

    signal_type: str = Field(..., min_length=1, max_length=120)
    direction: EvidenceDirection

    signal_weight: float = Field(..., ge=-1.0, le=1.0)

    source_a: PlatformSource | None = None
    source_b: PlatformSource | None = None

    field_name: str | None = None
    field_value_a: str | None = None
    field_value_b: str | None = None

    explanation: str = Field(..., min_length=1, max_length=1000)

    @field_validator("signal_type", "field_name", "field_value_a", "field_value_b", mode="before")
    @classmethod
    def clean_optional_string_fields(cls, value: Any) -> str | None:
        if value is None:
            return None

        cleaned = str(value).strip()
        return cleaned or None

    @field_validator("explanation")
    @classmethod
    def clean_explanation(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("explanation must not be empty")
        return cleaned

    @model_validator(mode="after")
    def validate_weight_direction(self) -> MatchEvidence:
        if self.direction == EvidenceDirection.POSITIVE and self.signal_weight <= 0:
            raise ValueError("positive evidence must have signal_weight > 0")

        if self.direction == EvidenceDirection.NEGATIVE and self.signal_weight >= 0:
            raise ValueError("negative evidence must have signal_weight < 0")

        if self.direction == EvidenceDirection.NEUTRAL and self.signal_weight != 0:
            raise ValueError("neutral evidence must have signal_weight = 0")

        return self

    @property
    def is_positive(self) -> bool:
        return self.direction == EvidenceDirection.POSITIVE

    @property
    def is_negative(self) -> bool:
        return self.direction == EvidenceDirection.NEGATIVE

    def to_db_payload(self, profile_source_link_id: UUID) -> dict[str, Any]:
        data = self.model_dump(mode="json", exclude_none=True)
        data["profile_source_link_id"] = str(profile_source_link_id)
        data.pop("id", None)
        return data


class ConflictSourceValue(BaseModel):
    source: PlatformSource
    value: str = Field(..., min_length=1, max_length=500)


class ConflictRecord(BaseModel):
    """
    Structured field disagreement across source accounts.

    Missing values are not conflicts. A conflict requires at least two present
    values that meaningfully disagree.
    """

    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    id: UUID | None = None

    profile_id: UUID | None = None

    field_name: str = Field(..., min_length=1, max_length=120)
    severity: ConflictSeverity

    impact: float = Field(..., le=0, ge=-1.0)

    source_values: list[ConflictSourceValue] = Field(default_factory=list)

    explanation: str = Field(..., min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_conflict_values(self) -> ConflictRecord:
        if len(self.source_values) < 2:
            raise ValueError("a conflict requires at least two source values")

        return self

    @property
    def is_high_severity(self) -> bool:
        return self.severity == ConflictSeverity.HIGH

    def to_db_payload(self, profile_id: UUID) -> dict[str, Any]:
        return {
            "profile_id": str(profile_id),
            "field_name": self.field_name,
            "severity": self.severity.value,
            "impact": self.impact,
            "source_values": [
                item.model_dump(mode="json") for item in self.source_values
            ],
            "explanation": self.explanation,
        }


class ResolutionDecision(BaseModel):
    """
    Final decision for one source account during one resolution run.

    This maps directly to profile_source_links plus match_evidence and conflicts.
    """

    model_config = ConfigDict(validate_assignment=True)

    source_account: SourceAccount

    confidence_score: float = Field(..., ge=0, le=1)

    decision: MatchDecision
    relationship_type: SourceRelationshipType
    verification_status: VerificationStatus

    evidence: list[MatchEvidence] = Field(default_factory=list)
    conflicts: list[ConflictRecord] = Field(default_factory=list)

    positive_signal_count: int = Field(default=0, ge=0)
    negative_signal_count: int = Field(default=0, ge=0)
    has_high_conflict: bool = False

    @model_validator(mode="after")
    def derive_counts_and_validate(self) -> ResolutionDecision:
        if self.evidence:
            object.__setattr__(
                self,
                "positive_signal_count",
                sum(1 for item in self.evidence if item.is_positive),
            )
            object.__setattr__(
                self,
                "negative_signal_count",
                sum(1 for item in self.evidence if item.is_negative),
            )

        if self.conflicts:
            object.__setattr__(
                self,
                "has_high_conflict",
                any(item.is_high_severity for item in self.conflicts),
            )

        if self.decision == MatchDecision.AUTO_MATCH:
            if self.confidence_score < 0.85:
                raise ValueError("auto_match requires confidence_score >= 0.85")

            if self.positive_signal_count < 2:
                raise ValueError("auto_match requires at least 2 positive evidence signals")

            if self.has_high_conflict:
                raise ValueError("auto_match cannot have a high-severity conflict")

            if self.relationship_type not in {
                SourceRelationshipType.PRIMARY,
                SourceRelationshipType.SECONDARY,
                SourceRelationshipType.ALIAS,
            }:
                raise ValueError("auto_match has invalid relationship_type")

            if self.verification_status not in {
                VerificationStatus.CLAIMED_BY_INPUT,
                VerificationStatus.EVIDENCE_MATCHED,
                VerificationStatus.RECIPROCAL_LINK_VERIFIED,
                VerificationStatus.LIKELY_SAME_PERSON,
            }:
                raise ValueError("auto_match has invalid verification_status")

        if self.decision == MatchDecision.NEEDS_REVIEW:
            if self.relationship_type not in {
                SourceRelationshipType.POSSIBLE_ALIAS,
                SourceRelationshipType.SECONDARY,
                SourceRelationshipType.ALIAS,
            }:
                raise ValueError("needs_review has invalid relationship_type")

            if self.verification_status not in {
                VerificationStatus.NEEDS_REVIEW,
                VerificationStatus.LIKELY_SAME_PERSON,
                VerificationStatus.CLAIMED_BY_INPUT,
            }:
                raise ValueError("needs_review has invalid verification_status")

        if self.decision == MatchDecision.REJECT:
            if self.relationship_type != SourceRelationshipType.REJECTED:
                raise ValueError("reject decision requires relationship_type='rejected'")

            if self.verification_status != VerificationStatus.REJECTED:
                raise ValueError("reject decision requires verification_status='rejected'")

        return self

    def profile_source_link_payload(
        self,
        profile_id: UUID,
        source_account_id: UUID,
    ) -> dict[str, Any]:
        return {
            "profile_id": str(profile_id),
            "source_account_id": str(source_account_id),
            "confidence_score": round(self.confidence_score, 4),
            "decision": self.decision.value,
            "relationship_type": self.relationship_type.value,
            "verification_status": self.verification_status.value,
            "positive_signal_count": self.positive_signal_count,
            "negative_signal_count": self.negative_signal_count,
            "has_high_conflict": self.has_high_conflict,
        }


class ResolutionSummary(BaseModel):
    """
    Aggregate summary of one resolution run before building the API response.
    """

    model_config = ConfigDict(validate_assignment=True)

    decisions: list[ResolutionDecision] = Field(default_factory=list)

    @property
    def auto_matches(self) -> list[ResolutionDecision]:
        return [
            item for item in self.decisions
            if item.decision == MatchDecision.AUTO_MATCH
        ]

    @property
    def needs_review(self) -> list[ResolutionDecision]:
        return [
            item for item in self.decisions
            if item.decision == MatchDecision.NEEDS_REVIEW
        ]

    @property
    def rejected(self) -> list[ResolutionDecision]:
        return [
            item for item in self.decisions
            if item.decision == MatchDecision.REJECT
        ]

    @property
    def matched_sources(self) -> list[str]:
        return sorted({item.source_account.source.value for item in self.auto_matches})

    @property
    def needs_review_sources(self) -> list[str]:
        return sorted({item.source_account.source.value for item in self.needs_review})

    @property
    def rejected_sources(self) -> list[str]:
        return sorted({item.source_account.source.value for item in self.rejected})