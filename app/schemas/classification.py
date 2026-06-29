from __future__ import annotations

from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.enums import MatchDecision, PlatformSource


class DecisionBasis(str, Enum):
    ANCHOR_INPUT = "anchor_input"
    STRONG_ANCHOR_PAIR = "strong_anchor_pair"
    AMBIGUOUS_ANCHOR_PAIR = "ambiguous_anchor_pair"
    NO_ANCHOR_REVIEW = "no_anchor_review"
    BLOCKING_CONFLICT_REVIEW = "blocking_conflict_review"
    REJECTED_CONFLICT = "rejected_conflict"
    REJECTED_NO_SUPPORT = "rejected_no_support"
    REJECTED_WEAK_ONLY = "rejected_weak_only"


class DecisionRiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class AccountClassification(BaseModel):
    """
    Final classifier output for a single source account.

    This is still not canonical profile creation.
    It only says whether this account should be included, reviewed, or rejected.
    """

    model_config = ConfigDict(validate_assignment=True)

    source_account_id: UUID | None = None
    source_account_key: str
    source: PlatformSource

    decision: MatchDecision
    decision_basis: DecisionBasis
    risk_level: DecisionRiskLevel

    evidence_confidence_score: float = Field(..., ge=0.0, le=1.0)
    decision_confidence_score: float = Field(..., ge=0.0, le=1.0)

    account_score: float = Field(..., ge=0.0, le=1.0)
    best_pair_score: float | None = Field(default=None, ge=0.0, le=1.0)

    is_anchor: bool = False
    accepted_as_anchor: bool = False
    best_anchor_account_key: str | None = None
    best_pair_key: str | None = None

    independent_positive_groups: list[str] = Field(default_factory=list)
    strong_positive_groups: list[str] = Field(default_factory=list)
    weak_positive_groups: list[str] = Field(default_factory=list)

    weak_signal_only: bool = False
    hn_conservative: bool = False
    hn_requires_strong_evidence: bool = False

    conflict_types: list[str] = Field(default_factory=list)
    blocking_conflict_types: list[str] = Field(default_factory=list)

    rationale: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "source_account_key",
        "best_anchor_account_key",
        "best_pair_key",
        mode="before",
    )
    @classmethod
    def clean_keys(cls, value: Any) -> str | None:
        if value is None:
            return None

        cleaned = str(value).strip().lower()
        return cleaned or None


class ClassificationThresholds(BaseModel):
    auto_match_threshold: float = 0.85
    needs_review_threshold: float = 0.60
    confidence_cap: float = 0.97
    minimum_auto_match_independent_groups: int = 2


class ClassificationResult(BaseModel):
    classifications: list[AccountClassification] = Field(default_factory=list)
    thresholds: ClassificationThresholds
    anchor_account_keys: list[str] = Field(default_factory=list)

    @property
    def auto_matched_account_keys(self) -> list[str]:
        return [
            item.source_account_key
            for item in self.classifications
            if item.decision == MatchDecision.AUTO_MATCH
        ]

    @property
    def needs_review_account_keys(self) -> list[str]:
        return [
            item.source_account_key
            for item in self.classifications
            if item.decision == MatchDecision.NEEDS_REVIEW
        ]

    @property
    def rejected_account_keys(self) -> list[str]:
        return [
            item.source_account_key
            for item in self.classifications
            if item.decision == MatchDecision.REJECT
        ]

    @property
    def has_review_items(self) -> bool:
        return bool(self.needs_review_account_keys)

    @property
    def has_rejections(self) -> bool:
        return bool(self.rejected_account_keys)

    @property
    def classification_by_key(self) -> dict[str, AccountClassification]:
        return {
            item.source_account_key: item
            for item in self.classifications
        }
