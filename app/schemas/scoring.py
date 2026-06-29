from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.enums import PlatformSource


class ScoreTargetType(str, Enum):
    ACCOUNT = "account"
    ACCOUNT_PAIR = "account_pair"


class ScoreComponentKind(str, Enum):
    POSITIVE_EVIDENCE = "positive_evidence"
    CONFLICT_PENALTY = "conflict_penalty"
    GROUP_CAP_DISCARDED = "group_cap_discarded"
    CONFLICT_DUPLICATE_DISCARDED = "conflict_duplicate_discarded"


class ScoreComponent(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    kind: ScoreComponentKind
    signal_type: str
    raw_weight: float
    applied_weight: float
    reason: str

    independence_group: str | None = None

    source_account_key: str | None = None
    target_account_key: str | None = None

    metadata: dict[str, Any] = Field(default_factory=dict)


class ConfidenceScore(BaseModel):
    """
    Deterministic score for either:
    1. one account against the request
    2. one account pair against each other

    This is not a final decision.
    """

    model_config = ConfigDict(validate_assignment=True)

    target_type: ScoreTargetType
    target_key: str

    source_account_key: str | None = None
    source: PlatformSource | None = None

    target_account_key: str | None = None
    target_source: PlatformSource | None = None

    positive_score: float = Field(..., ge=0.0, le=2.0)
    conflict_penalty: float = Field(..., ge=-2.0, le=0.0)
    score_before_cap: float = Field(..., ge=-1.0, le=2.0)
    confidence_score: float = Field(..., ge=0.0, le=1.0)

    positive_signal_count: int = Field(..., ge=0)
    raw_positive_signal_count: int = Field(default=0, ge=0)
    conflict_count: int = Field(..., ge=0)
    raw_conflict_count: int = Field(default=0, ge=0)

    independent_positive_groups: list[str] = Field(default_factory=list)
    strong_positive_groups: list[str] = Field(default_factory=list)
    weak_positive_groups: list[str] = Field(default_factory=list)

    weak_signal_only: bool = False
    hn_conservative: bool = False
    hn_requires_strong_evidence: bool = False

    components: list[ScoreComponent] = Field(default_factory=list)
    explanation: list[str] = Field(default_factory=list)

    @field_validator("target_key", "source_account_key", "target_account_key", mode="before")
    @classmethod
    def clean_keys(cls, value: Any) -> str | None:
        if value is None:
            return None

        cleaned = str(value).strip().lower()
        return cleaned or None


class ScoringResult(BaseModel):
    account_scores: list[ConfidenceScore] = Field(default_factory=list)
    pair_scores: list[ConfidenceScore] = Field(default_factory=list)

    confidence_cap: float = 0.97
    anchor_account_keys: list[str] = Field(default_factory=list)

    @property
    def account_score_by_key(self) -> dict[str, ConfidenceScore]:
        return {
            score.target_key: score
            for score in self.account_scores
        }

    @property
    def pair_score_by_key(self) -> dict[str, ConfidenceScore]:
        return {
            score.target_key: score
            for score in self.pair_scores
        }

    def is_anchor(self, account_key: str) -> bool:
        normalized_key = account_key.strip().lower()
        return normalized_key in {
            key.strip().lower()
            for key in self.anchor_account_keys
        }
