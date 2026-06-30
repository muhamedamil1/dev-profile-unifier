from __future__ import annotations

from dataclasses import dataclass

from app.llm.match_review_prompts import source_account_key
from app.resolution.ambiguity_reviewer import (
    final_link_fields_after_review,
    merge_llm_review_into_decision_payload,
)
from app.schemas.llm_review import (
    AmbiguityReviewOutcome,
    LLMIdentityReviewResult,
    LLMReviewConfidence,
    LLMReviewPolicyAction,
    LLMReviewRecommendation,
)


@dataclass
class FakeAccount:
    source: str
    source_user_id: str

    def expected_source_account_key(self) -> str:
        return f"{self.source}:{self.source_user_id}"


def test_resolution_service_persistence_contract_for_promoted_account():
    account = FakeAccount(source="devto", source_user_id="202")
    key = source_account_key(account)
    outcome = AmbiguityReviewOutcome(
        source_account_key=key,
        anchor_account_key="github:101",
        eligible=True,
        reviewed=True,
        deterministic_score=0.76,
        llm_result=LLMIdentityReviewResult(
            recommendation=LLMReviewRecommendation.LIKELY_SAME_PERSON,
            confidence=LLMReviewConfidence.MEDIUM,
            rationale=["Same website and profile link evidence."],
            risk_flags=[],
            used_evidence_types=["same_website", "direct_profile_link"],
        ),
        final_policy_action=LLMReviewPolicyAction.PROMOTED_TO_AUTO_MATCH,
        promoted=True,
        prompt_version="identity_match_review_v1_2026_06_hardened",
        model="gemini-test",
    )

    outcome_by_key = {key: outcome}
    review_outcome = outcome_by_key.get(source_account_key(account))
    link_fields = final_link_fields_after_review(
        original_decision="needs_review",
        original_relationship_type="possible_alias",
        original_verification_status="needs_review",
        original_confidence_score=0.61,
        outcome=review_outcome,
    )
    payload = merge_llm_review_into_decision_payload({"deterministic": True}, review_outcome)

    assert link_fields == {
        "decision": "auto_match",
        "relationship_type": "secondary",
        "verification_status": "likely_same_person",
        "confidence_score": 0.85,
    }
    assert payload["deterministic"] is True
    assert payload["llm_review"]["final_policy_action"] == "promoted_to_auto_match"
    assert payload["llm_review"]["deterministic_score_before_review"] == 0.76


def test_resolution_service_persistence_contract_for_no_review_account():
    account = FakeAccount(source="github", source_user_id="101")
    review_outcome = None

    link_fields = final_link_fields_after_review(
        original_decision="auto_match",
        original_relationship_type="primary",
        original_verification_status="claimed_by_input",
        original_confidence_score=0.90,
        outcome=review_outcome,
    )
    payload = merge_llm_review_into_decision_payload({"deterministic": True}, review_outcome)

    assert link_fields == {
        "decision": "auto_match",
        "relationship_type": "primary",
        "verification_status": "claimed_by_input",
        "confidence_score": 0.90,
    }
    assert payload == {"deterministic": True}
