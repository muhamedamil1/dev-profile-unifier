from __future__ import annotations

from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class LLMReviewRecommendation(str, Enum):
    LIKELY_SAME_PERSON = "likely_same_person"
    UNCERTAIN = "uncertain"
    LIKELY_DIFFERENT_PERSON = "likely_different_person"


class LLMReviewConfidence(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class LLMReviewPolicyAction(str, Enum):
    NOT_ELIGIBLE = "not_eligible"
    REVIEW_FAILED_KEEP_NEEDS_REVIEW = "review_failed_keep_needs_review"
    KEPT_NEEDS_REVIEW = "kept_needs_review"
    PROMOTED_TO_AUTO_MATCH = "promoted_to_auto_match"
    REJECTED_AFTER_REVIEW = "rejected_after_review"


class LLMReviewSkipReason(str, Enum):
    FEATURE_DISABLED = "feature_disabled"
    NO_GEMINI_CLIENT = "no_gemini_client"
    GEMINI_UNAVAILABLE = "gemini_unavailable"
    NOT_NEEDS_REVIEW = "not_needs_review"
    MISSING_ANCHOR = "missing_anchor"
    SCORE_OUTSIDE_REVIEW_BAND = "score_outside_review_band"
    WEAK_SIGNAL_ONLY = "weak_signal_only"
    HN_HANDLE_ONLY = "hn_handle_only"
    BLOCKING_CONFLICT = "blocking_conflict"
    NO_MEANINGFUL_EVIDENCE = "no_meaningful_evidence"
    MISSING_EVIDENCE_PACKET = "missing_evidence_packet"


class LLMReviewSafetyFlag(str, Enum):
    INVALID_JSON = "invalid_json"
    EXTRA_KEYS = "extra_keys"
    FORBIDDEN_CLAIM = "forbidden_claim"
    EMPTY_RESPONSE = "empty_response"
    LLM_ERROR = "llm_error"
    EVIDENCE_REFERENCE_MISMATCH = "evidence_reference_mismatch"


class EvidencePacketItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signal_type: str
    direction: str
    weight: float | None = None
    field_name: str | None = None
    field_value_a: str | None = None
    field_value_b: str | None = None
    explanation: str | None = None


class ConflictPacketItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conflict_type: str
    severity: str | None = None
    penalty: float | None = None
    field_name: str | None = None
    field_value_a: str | None = None
    field_value_b: str | None = None
    explanation: str | None = None


class ReviewAccountSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    handle: str | None = None
    source_user_id: str | None = None
    display_name: str | None = None
    bio: str | None = None
    location: str | None = None
    website_url: str | None = None
    profile_url: str | None = None
    topics: list[str] = Field(default_factory=list, max_length=20)
    outbound_links: list[str] = Field(default_factory=list, max_length=20)


class LLMIdentityReviewPromptPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolution_run_id: UUID | None = None
    candidate_source_account_key: str
    anchor_source_account_key: str
    anchor_account: ReviewAccountSummary
    candidate_account: ReviewAccountSummary
    deterministic_score: float = Field(ge=0.0, le=0.97)
    independent_positive_groups: list[str] = Field(default_factory=list, max_length=10)
    strong_positive_groups: list[str] = Field(default_factory=list, max_length=10)
    weak_positive_groups: list[str] = Field(default_factory=list, max_length=10)
    weak_signal_only: bool = False
    hn_conservative: bool = False
    blocking_conflict_types: list[str] = Field(default_factory=list, max_length=10)
    positive_evidence: list[EvidencePacketItem] = Field(default_factory=list, max_length=20)
    conflicts: list[ConflictPacketItem] = Field(default_factory=list, max_length=20)


class LLMIdentityReviewResult(BaseModel):
    """
    Strict structured result returned by Gemini for an ambiguous public identity match.

    This is advisory only. The deterministic policy layer decides whether the
    recommendation can affect the final classification.
    """

    model_config = ConfigDict(extra="forbid")

    recommendation: LLMReviewRecommendation
    confidence: LLMReviewConfidence
    rationale: list[str] = Field(default_factory=list, max_length=5)
    risk_flags: list[str] = Field(default_factory=list, max_length=5)
    used_evidence_types: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("rationale", "risk_flags", "used_evidence_types")
    @classmethod
    def clean_text_list(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for value in values:
            text = " ".join(str(value).strip().split())
            if text and text not in cleaned:
                cleaned.append(text[:500])
        return cleaned


class AmbiguityReviewOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_account_key: str
    anchor_account_key: str | None = None
    eligible: bool = False
    reviewed: bool = False
    skipped_reason: LLMReviewSkipReason | None = None
    deterministic_score: float = 0.0
    llm_result: LLMIdentityReviewResult | None = None
    final_policy_action: LLMReviewPolicyAction
    promoted: bool = False
    rejected: bool = False
    safety_flags: list[LLMReviewSafetyFlag] = Field(default_factory=list)
    error_message: str | None = None
    prompt_version: str | None = None
    model: str | None = None
    prompt_text: str | None = None
    duration_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)

    def decision_payload_patch(self) -> dict[str, Any]:
        return {
            "llm_review": {
                "enabled": True,
                "eligible": self.eligible,
                "reviewed": self.reviewed,
                "skipped_reason": self.skipped_reason.value if self.skipped_reason else None,
                "model": self.model,
                "prompt_version": self.prompt_version,
                "recommendation": self.llm_result.recommendation.value if self.llm_result else None,
                "confidence": self.llm_result.confidence.value if self.llm_result else None,
                "rationale": self.llm_result.rationale if self.llm_result else [],
                "risk_flags": self.llm_result.risk_flags if self.llm_result else [],
                "used_evidence_types": self.llm_result.used_evidence_types if self.llm_result else [],
                "deterministic_score_before_review": self.deterministic_score,
                "final_policy_action": self.final_policy_action.value,
                "promoted": self.promoted,
                "rejected": self.rejected,
                "safety_flags": [flag.value for flag in self.safety_flags],
                "error_message": self.error_message,
                "metadata": self.metadata,
            }
        }


class AmbiguityReviewBatchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    attempted: int = 0
    succeeded: int = 0
    promoted_to_auto_match: int = 0
    kept_needs_review: int = 0
    rejected_after_review: int = 0
    failed: int = 0
    eligible_count: int = 0
    not_eligible_count: int = 0
    reviewed_count: int = 0
    skipped_by_reason: dict[str, int] = Field(default_factory=dict)
    outcomes: list[AmbiguityReviewOutcome] = Field(default_factory=list)

    @property
    def outcome_by_key(self) -> dict[str, AmbiguityReviewOutcome]:
        return {outcome.source_account_key: outcome for outcome in self.outcomes}

    def result_summary_patch(self) -> dict[str, Any]:
        return {
            "llm_ambiguity_review_enabled": self.enabled,
            "llm_ambiguity_reviews_attempted": self.attempted,
            "llm_ambiguity_reviews_succeeded": self.succeeded,
            "llm_promoted_to_auto_match": self.promoted_to_auto_match,
            "llm_kept_needs_review": self.kept_needs_review,
            "llm_rejected_after_review": self.rejected_after_review,
            "llm_ambiguity_reviews_failed": self.failed,
            "llm_ambiguity_review_eligible_count": self.eligible_count,
            "llm_ambiguity_review_not_eligible_count": self.not_eligible_count,
            "llm_ambiguity_review_reviewed_count": self.reviewed_count,
            "llm_ambiguity_review_skipped_by_reason": dict(self.skipped_by_reason),
        }
