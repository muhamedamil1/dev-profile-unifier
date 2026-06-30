from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.llm.match_review_prompts import (
    IDENTITY_REVIEW_PROMPT_VERSION,
    build_identity_review_prompt,
    build_identity_review_prompt_payload,
    parse_identity_review_json,
    source_account_key,
    validate_used_evidence_types,
)
from app.schemas.llm_review import (
    AmbiguityReviewBatchResult,
    AmbiguityReviewOutcome,
    LLMReviewConfidence,
    LLMReviewPolicyAction,
    LLMReviewRecommendation,
    LLMReviewSafetyFlag,
    LLMReviewSkipReason,
)

from app.llm.metadata import merge_llm_metric_metadata, metadata_from_llm_error, metadata_from_llm_result


try:
    from app.llm.gemini_client import GeminiClientError, estimate_tokens
except Exception:  # pragma: no cover - supports repos before Phase 9 is applied.
    class GeminiClientError(Exception):
        pass

    def estimate_tokens(text: str) -> int:
        return max(1, len(text.split()))


STRONG_PROMOTION_GROUPS = {
    "website",
    "profile_link",
    "email",
    "same_website",
    "direct_profile_link",
    "reciprocal_profile_link",
    "email_hint_match",
    "email_domain_match",
}

REJECTION_SUPPORTING_CONFLICT_TYPES = {
    "email_conflict",
    "name_conflict",
    "website_conflict",
}

AUTO_MATCH_LINK_CONFIDENCE_FLOOR = 0.85


BLOCKING_CONFLICT_HINTS = {
    "email_conflict",
    "name_conflict",
    "website_conflict",
    "high",
    "blocking",
}


@dataclass(frozen=True)
class AmbiguityReviewConfig:
    enabled: bool = False
    review_min_score: float = 0.55
    review_max_score: float = 0.84
    promotion_min_score: float = 0.72
    prompt_version: str = IDENTITY_REVIEW_PROMPT_VERSION

    @classmethod
    def from_settings(cls, settings: Any | None) -> "AmbiguityReviewConfig":
        if settings is None:
            return cls()

        return cls(
            enabled=bool(_setting(settings, "enable_llm_ambiguity_review", _setting(settings, "ENABLE_LLM_AMBIGUITY_REVIEW", False))),
            review_min_score=float(_setting(settings, "llm_review_min_score", _setting(settings, "LLM_REVIEW_MIN_SCORE", 0.55))),
            review_max_score=float(_setting(settings, "llm_review_max_score", _setting(settings, "LLM_REVIEW_MAX_SCORE", 0.84))),
            promotion_min_score=float(_setting(settings, "llm_review_promotion_min_score", _setting(settings, "LLM_REVIEW_PROMOTION_MIN_SCORE", 0.72))),
            prompt_version=str(_setting(settings, "llm_review_prompt_version", _setting(settings, "LLM_REVIEW_PROMPT_VERSION", IDENTITY_REVIEW_PROMPT_VERSION))),
        )


class GeminiAmbiguityReviewer:
    """
    Optional Phase 7F reviewer for ambiguous public identity matches.

    It is advisory only. It reviews only needs_review candidates in a bounded
    score band, and a deterministic policy still decides whether its
    recommendation can promote/reject the candidate.
    """

    def __init__(
        self,
        *,
        gemini_client: Any | None,
        metrics_repo: Any | None = None,
        settings: Any | None = None,
        config: AmbiguityReviewConfig | None = None,
    ) -> None:
        self.gemini_client = gemini_client
        self.metrics_repo = metrics_repo
        self.config = config or AmbiguityReviewConfig.from_settings(settings)

    def run_reviews(
        self,
        *,
        accounts: list[Any],
        classification_result: Any,
        scoring_result: Any,
        evidence_result: Any,
        conflict_result: Any,
        resolution_run_id: UUID | str | None = None,
        persist_metrics: bool = True,
    ) -> AmbiguityReviewBatchResult:
        if not self.config.enabled:
            return AmbiguityReviewBatchResult(enabled=False)

        account_by_key = {source_account_key(account): account for account in accounts}
        classifications = _classification_map(classification_result)
        outcomes: list[AmbiguityReviewOutcome] = []

        for candidate_key, classification in classifications.items():
            candidate = account_by_key.get(candidate_key)
            if candidate is None:
                continue

            # Phase 7F is deliberately scoped to deterministic needs_review only.
            if _decision_value(_obj_value(classification, "decision")) != "needs_review":
                continue

            outcome = self.review_candidate(
                candidate_account=candidate,
                candidate_classification=classification,
                account_by_key=account_by_key,
                scoring_result=scoring_result,
                evidence_result=evidence_result,
                conflict_result=conflict_result,
                resolution_run_id=resolution_run_id,
                persist_metrics=persist_metrics,
            )
            outcomes.append(outcome)

        attempted = sum(1 for item in outcomes if item.reviewed)
        succeeded = sum(1 for item in outcomes if item.reviewed and not item.error_message)
        promoted = sum(1 for item in outcomes if item.final_policy_action == LLMReviewPolicyAction.PROMOTED_TO_AUTO_MATCH)
        rejected = sum(1 for item in outcomes if item.final_policy_action == LLMReviewPolicyAction.REJECTED_AFTER_REVIEW)
        failed = sum(1 for item in outcomes if item.error_message)
        kept = sum(
            1
            for item in outcomes
            if item.final_policy_action
            in {
                LLMReviewPolicyAction.KEPT_NEEDS_REVIEW,
                LLMReviewPolicyAction.REVIEW_FAILED_KEEP_NEEDS_REVIEW,
            }
        )
        eligible_count = sum(1 for item in outcomes if item.eligible)
        not_eligible_count = sum(1 for item in outcomes if not item.eligible)
        skipped_by_reason: dict[str, int] = {}
        for item in outcomes:
            if item.skipped_reason:
                key = item.skipped_reason.value
                skipped_by_reason[key] = skipped_by_reason.get(key, 0) + 1

        return AmbiguityReviewBatchResult(
            enabled=True,
            attempted=attempted,
            succeeded=succeeded,
            promoted_to_auto_match=promoted,
            kept_needs_review=kept,
            rejected_after_review=rejected,
            failed=failed,
            eligible_count=eligible_count,
            not_eligible_count=not_eligible_count,
            reviewed_count=attempted,
            skipped_by_reason=skipped_by_reason,
            outcomes=outcomes,
        )

    def review_candidate(
        self,
        *,
        candidate_account: Any,
        candidate_classification: Any,
        account_by_key: dict[str, Any],
        scoring_result: Any,
        evidence_result: Any,
        conflict_result: Any,
        resolution_run_id: UUID | str | None = None,
        persist_metrics: bool = True,
    ) -> AmbiguityReviewOutcome:
        candidate_key = source_account_key(candidate_account)
        score = _classification_score(candidate_classification, scoring_result, candidate_key)
        anchor_key = _anchor_key(candidate_classification, scoring_result, candidate_key)
        anchor_account = account_by_key.get(anchor_key or "")
        blocking_conflict_types = _string_list(_obj_value(candidate_classification, "blocking_conflict_types"))
        conflict_types = _string_list(_obj_value(candidate_classification, "conflict_types"))
        independent_groups = _string_list(_obj_value(candidate_classification, "independent_positive_groups"))
        strong_groups = _string_list(_obj_value(candidate_classification, "strong_positive_groups"))
        weak_groups = _string_list(_obj_value(candidate_classification, "weak_positive_groups"))
        weak_signal_only = bool(_obj_value(candidate_classification, "weak_signal_only", False))
        hn_conservative = bool(_obj_value(candidate_classification, "hn_conservative", False))
        llm_metadata: dict[str, Any] = {}

        skipped = self._skip_reason(
            candidate_key=candidate_key,
            candidate_account=candidate_account,
            anchor_key=anchor_key,
            anchor_account=anchor_account,
            score=score,
            weak_signal_only=weak_signal_only,
            hn_conservative=hn_conservative,
            blocking_conflict_types=blocking_conflict_types,
            independent_groups=independent_groups,
            strong_groups=strong_groups,
        )
        if skipped:
            return AmbiguityReviewOutcome(
                source_account_key=candidate_key,
                anchor_account_key=anchor_key,
                eligible=False,
                reviewed=False,
                skipped_reason=skipped,
                deterministic_score=score,
                final_policy_action=LLMReviewPolicyAction.NOT_ELIGIBLE,
                prompt_version=self.config.prompt_version,
                model=_model_name(self.gemini_client),
                metadata={
                    "independent_positive_groups": independent_groups,
                    "strong_positive_groups": strong_groups,
                    "blocking_conflict_types": blocking_conflict_types,
                    "conflict_types": conflict_types,
                    **llm_metadata,
                },
            )

        positive_evidence = _pair_evidence(evidence_result, anchor_account, candidate_account)
        pair_conflicts = _pair_conflicts(conflict_result, anchor_account, candidate_account)
        if not positive_evidence:
            return AmbiguityReviewOutcome(
                source_account_key=candidate_key,
                anchor_account_key=anchor_key,
                eligible=False,
                reviewed=False,
                skipped_reason=LLMReviewSkipReason.MISSING_EVIDENCE_PACKET,
                deterministic_score=score,
                final_policy_action=LLMReviewPolicyAction.NOT_ELIGIBLE,
                prompt_version=self.config.prompt_version,
                model=_model_name(self.gemini_client),
                metadata={
                    "independent_positive_groups": independent_groups,
                    "strong_positive_groups": strong_groups,
                    "blocking_conflict_types": blocking_conflict_types,
                    "conflict_types": conflict_types,
                    **llm_metadata,
                },
            )

        payload = build_identity_review_prompt_payload(
            anchor_account=anchor_account,
            candidate_account=candidate_account,
            deterministic_score=score,
            positive_evidence=positive_evidence,
            conflicts=pair_conflicts,
            independent_positive_groups=independent_groups,
            strong_positive_groups=strong_groups,
            weak_positive_groups=weak_groups,
            weak_signal_only=weak_signal_only,
            hn_conservative=hn_conservative,
            blocking_conflict_types=blocking_conflict_types,
            resolution_run_id=resolution_run_id,
        )
        prompt_text = build_identity_review_prompt(payload)
        input_tokens = estimate_tokens(prompt_text)
        started = time.perf_counter()
        llm_generation = None
        llm_error = None
        llm_attempted = False

        try:
            if self.gemini_client is None:
                raise GeminiClientError("Gemini client is not configured.")
            if hasattr(self.gemini_client, "available") and not self.gemini_client.available:
                raise GeminiClientError("Gemini API key is not configured.")

            llm_attempted = True
            generated = self.gemini_client.generate_text(prompt=prompt_text)
            llm_generation = generated
            llm_metadata = metadata_from_llm_result(generated)
            raw_text = str(_obj_value(generated, "text", ""))
            duration_ms = int(_obj_value(generated, "duration_ms", int((time.perf_counter() - started) * 1000)) or 0)
            output_tokens = int(_obj_value(generated, "output_tokens", estimate_tokens(raw_text)) or 0)
            model_name = str(_obj_value(generated, "model", _model_name(self.gemini_client)) or _model_name(self.gemini_client))

            parsed_review = parse_identity_review_json(raw_text)
            validate_used_evidence_types(result=parsed_review, payload=payload)

            policy_action = self._policy_action(
                llm_result=parsed_review,
                deterministic_score=score,
                independent_groups=independent_groups,
                strong_groups=strong_groups,
                blocking_conflict_types=blocking_conflict_types,
                conflict_types=conflict_types,
            )

            outcome = AmbiguityReviewOutcome(
                source_account_key=candidate_key,
                anchor_account_key=anchor_key,
                eligible=True,
                reviewed=True,
                deterministic_score=score,
                llm_result=parsed_review,
                final_policy_action=policy_action,
                promoted=policy_action == LLMReviewPolicyAction.PROMOTED_TO_AUTO_MATCH,
                rejected=policy_action == LLMReviewPolicyAction.REJECTED_AFTER_REVIEW,
                prompt_version=self.config.prompt_version,
                model=model_name,
                prompt_text=prompt_text,
                duration_ms=duration_ms,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                metadata={
                    "independent_positive_groups": independent_groups,
                    "strong_positive_groups": strong_groups,
                    "blocking_conflict_types": blocking_conflict_types,
                    "conflict_types": conflict_types,
                    **llm_metadata,
                },
            )
            if persist_metrics:
                self._record_metric(
                    resolution_run_id=resolution_run_id,
                    status_code=200,
                    duration_ms=duration_ms,
                    error_message=None,
                    outcome=outcome,
                    llm_result=llm_generation,
                    llm_error=llm_error,
                )
            return outcome

        except Exception as exc:
            llm_error = exc
            if not llm_metadata:
                llm_metadata = metadata_from_llm_error(exc)
            safety_flags = [LLMReviewSafetyFlag.LLM_ERROR]
            name = type(exc).__name__
            message = str(exc).lower()
            if "validationerror" in name.lower():
                safety_flags.append(LLMReviewSafetyFlag.EXTRA_KEYS)
            elif "json" in name.lower() or "json" in message:
                safety_flags.append(LLMReviewSafetyFlag.INVALID_JSON)
            elif "empty" in message:
                safety_flags.append(LLMReviewSafetyFlag.EMPTY_RESPONSE)
            elif "forbidden" in message or "ownership" in message:
                safety_flags.append(LLMReviewSafetyFlag.FORBIDDEN_CLAIM)
            elif "unsupported evidence" in message:
                safety_flags.append(LLMReviewSafetyFlag.EVIDENCE_REFERENCE_MISMATCH)

            duration_ms = int((time.perf_counter() - started) * 1000)
            outcome = AmbiguityReviewOutcome(
                source_account_key=candidate_key,
                anchor_account_key=anchor_key,
                eligible=True,
                reviewed=True,
                deterministic_score=score,
                final_policy_action=LLMReviewPolicyAction.REVIEW_FAILED_KEEP_NEEDS_REVIEW,
                prompt_version=self.config.prompt_version,
                model=_model_name(self.gemini_client),
                prompt_text=prompt_text,
                duration_ms=duration_ms,
                input_tokens=input_tokens,
                output_tokens=0,
                safety_flags=safety_flags,
                error_message="Gemini ambiguity review failed; keeping deterministic needs_review decision.",
                metadata={
                    "error_type": name,
                    **llm_metadata,
                },
            )
            if persist_metrics and llm_attempted:
                self._record_metric(
                    resolution_run_id=resolution_run_id,
                    status_code=502,
                    duration_ms=duration_ms,
                    error_message=outcome.error_message,
                    outcome=outcome,
                    llm_result=llm_generation,
                    llm_error=llm_error,
                )
            return outcome

    def _skip_reason(
        self,
        *,
        candidate_key: str,
        candidate_account: Any,
        anchor_key: str | None,
        anchor_account: Any | None,
        score: float,
        weak_signal_only: bool,
        hn_conservative: bool,
        blocking_conflict_types: list[str],
        independent_groups: list[str],
        strong_groups: list[str],
    ) -> LLMReviewSkipReason | None:
        if self.gemini_client is None:
            return LLMReviewSkipReason.NO_GEMINI_CLIENT
        if hasattr(self.gemini_client, "available") and not self.gemini_client.available:
            return LLMReviewSkipReason.GEMINI_UNAVAILABLE
        if not anchor_key or anchor_account is None:
            return LLMReviewSkipReason.MISSING_ANCHOR
        if score < self.config.review_min_score or score > self.config.review_max_score:
            return LLMReviewSkipReason.SCORE_OUTSIDE_REVIEW_BAND
        if weak_signal_only:
            return LLMReviewSkipReason.WEAK_SIGNAL_ONLY
        if hn_conservative and not strong_groups:
            return LLMReviewSkipReason.HN_HANDLE_ONLY
        if _source(candidate_account) == "hackernews" and not strong_groups:
            return LLMReviewSkipReason.HN_HANDLE_ONLY
        if _has_blocking_conflict(blocking_conflict_types):
            return LLMReviewSkipReason.BLOCKING_CONFLICT
        if not independent_groups and not strong_groups:
            return LLMReviewSkipReason.NO_MEANINGFUL_EVIDENCE
        return None

    def _policy_action(
        self,
        *,
        llm_result: Any,
        deterministic_score: float,
        independent_groups: list[str],
        strong_groups: list[str],
        blocking_conflict_types: list[str],
        conflict_types: list[str],
    ) -> LLMReviewPolicyAction:
        if llm_result.recommendation == LLMReviewRecommendation.LIKELY_SAME_PERSON:
            if (
                llm_result.confidence in {LLMReviewConfidence.MEDIUM, LLMReviewConfidence.HIGH}
                and deterministic_score >= self.config.promotion_min_score
                and len(set(independent_groups)) >= 2
                and _has_strong_promotion_group(strong_groups)
                and not _has_blocking_conflict(blocking_conflict_types)
            ):
                return LLMReviewPolicyAction.PROMOTED_TO_AUTO_MATCH
            return LLMReviewPolicyAction.KEPT_NEEDS_REVIEW

        if llm_result.recommendation == LLMReviewRecommendation.LIKELY_DIFFERENT_PERSON:
            if _has_blocking_conflict(blocking_conflict_types) or _has_rejection_supporting_conflict(conflict_types):
                return LLMReviewPolicyAction.REJECTED_AFTER_REVIEW
            return LLMReviewPolicyAction.KEPT_NEEDS_REVIEW

        return LLMReviewPolicyAction.KEPT_NEEDS_REVIEW

    def _record_metric(
        self,
        *,
        resolution_run_id: UUID | str | None,
        status_code: int,
        duration_ms: int,
        error_message: str | None,
        outcome: AmbiguityReviewOutcome,
        llm_result: Any | None = None,
        llm_error: BaseException | None = None,
    ) -> None:
        if self.metrics_repo is None or not hasattr(self.metrics_repo, "record_metric"):
            return

        try:
            metric_metadata = merge_llm_metric_metadata(
                {
                    "feature": "ambiguity_identity_review",
                    "review_type": "ambiguity_identity_review",
                    "prompt_version": outcome.prompt_version,
                    "model": outcome.model,
                    "source_account_key": outcome.source_account_key,
                    "anchor_account_key": outcome.anchor_account_key,
                    "final_policy_action": outcome.final_policy_action.value,
                    "promoted": outcome.promoted,
                    "rejected": outcome.rejected,
                    "safety_flags": [flag.value for flag in outcome.safety_flags],
                    "input_tokens": outcome.input_tokens,
                    "output_tokens": outcome.output_tokens,
                    "error_type": type(llm_error).__name__ if llm_error else None,
                },
                result=llm_result,
                error=llm_error,
            )
            self.metrics_repo.record_metric(
                resolution_run_id=str(resolution_run_id) if resolution_run_id else None,
                source="gemini",
                endpoint="/llm/identity-match-review",
                http_method="POST",
                status_code=status_code,
                duration_ms=duration_ms,
                error_message=error_message,
                metadata=metric_metadata,
            )
        except Exception:
            return


def final_decision_after_review(original_decision: str, outcome: AmbiguityReviewOutcome | None) -> str:
    if outcome is None:
        return original_decision
    if outcome.final_policy_action == LLMReviewPolicyAction.PROMOTED_TO_AUTO_MATCH:
        return "auto_match"
    if outcome.final_policy_action == LLMReviewPolicyAction.REJECTED_AFTER_REVIEW:
        return "reject"
    return original_decision


def final_link_fields_after_review(
    *,
    original_decision: str,
    original_relationship_type: str | None,
    original_verification_status: str | None,
    original_confidence_score: float | None,
    outcome: AmbiguityReviewOutcome | None,
) -> dict[str, Any]:
    """Return safe profile_source_links field values after Phase 7F review.

    The stored link confidence represents the final persisted decision and must
    satisfy the profile_source_links database contract. Raw deterministic evidence
    scores remain in decision_payload for auditability.
    """

    decision = final_decision_after_review(original_decision, outcome)
    relationship_type = original_relationship_type
    verification_status = original_verification_status
    confidence_score = original_confidence_score

    if outcome and outcome.final_policy_action == LLMReviewPolicyAction.PROMOTED_TO_AUTO_MATCH:
        relationship_type = relationship_type if relationship_type in {"primary", "alias", "secondary"} else "secondary"
        verification_status = verification_status if verification_status in {"claimed_by_input", "evidence_matched", "reciprocal_link_verified"} else "likely_same_person"
        confidence_score = outcome.deterministic_score if outcome.deterministic_score else original_confidence_score

    if outcome and outcome.final_policy_action == LLMReviewPolicyAction.REJECTED_AFTER_REVIEW:
        relationship_type = "rejected"
        verification_status = "rejected"
        confidence_score = outcome.deterministic_score if outcome.deterministic_score else original_confidence_score

    if decision == "auto_match" and confidence_score is not None:
        confidence_score = max(confidence_score, AUTO_MATCH_LINK_CONFIDENCE_FLOOR)

    return {
        "decision": decision,
        "relationship_type": relationship_type,
        "verification_status": verification_status,
        "confidence_score": confidence_score,
    }


def merge_llm_review_into_decision_payload(
    decision_payload: dict[str, Any] | None,
    outcome: AmbiguityReviewOutcome | None,
) -> dict[str, Any]:
    payload = dict(decision_payload or {})
    if outcome:
        payload.update(outcome.decision_payload_patch())
    return payload


def _setting(settings: Any, key: str, default: Any = None) -> Any:
    if isinstance(settings, dict):
        return settings.get(key, default)
    return getattr(settings, key, default)


def _obj_value(obj: Any, attr: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(attr, default)
    return getattr(obj, attr, default)


def _decision_value(value: Any) -> str:
    if hasattr(value, "value"):
        return str(value.value)
    return str(value or "")


def _source(account: Any) -> str:
    value = _obj_value(account, "source")
    if hasattr(value, "value"):
        return str(value.value)
    return str(value or "").lower()


def _classification_map(classification_result: Any) -> dict[str, Any]:
    direct = _obj_value(classification_result, "classification_by_key")
    if isinstance(direct, dict):
        return direct

    items = _obj_value(classification_result, "classifications")
    if isinstance(items, list):
        result = {}
        for item in items:
            key = _obj_value(item, "source_account_key")
            if key:
                result[str(key)] = item
        return result

    return {}


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (set, tuple)):
        value = list(value)
    if not isinstance(value, list):
        return []
    return [str(item.value if hasattr(item, "value") else item) for item in value if item]


def _classification_score(classification: Any, scoring_result: Any, candidate_key: str) -> float:
    for attr in ("best_pair_score", "account_score", "confidence_score", "score"):
        value = _obj_value(classification, attr)
        if value is not None:
            try:
                return round(float(value), 4)
            except (TypeError, ValueError):
                pass

    account_scores = _obj_value(scoring_result, "account_scores")
    if isinstance(account_scores, dict):
        score_obj = account_scores.get(candidate_key)
        for attr in ("final_score", "score", "confidence_score"):
            value = _obj_value(score_obj, attr)
            if value is not None:
                try:
                    return round(float(value), 4)
                except (TypeError, ValueError):
                    pass
    return 0.0


def _anchor_key(classification: Any, scoring_result: Any, candidate_key: str) -> str | None:
    for attr in (
        "best_anchor_account_key",
        "anchor_account_key",
        "best_pair_account_key",
        "matched_anchor_key",
    ):
        value = _obj_value(classification, attr)
        if value and str(value) != candidate_key:
            return str(value)

    metadata = _obj_value(classification, "metadata")
    if isinstance(metadata, dict):
        for key in ("best_anchor_account_key", "anchor_account_key", "best_pair_account_key"):
            value = metadata.get(key)
            if value and str(value) != candidate_key:
                return str(value)

    anchor_keys = _obj_value(scoring_result, "anchor_account_keys")
    if isinstance(anchor_keys, (list, set, tuple)):
        for value in anchor_keys:
            if str(value) != candidate_key:
                return str(value)

    return None


def _pair_evidence(evidence_result: Any, anchor_account: Any, candidate_account: Any) -> list[Any]:
    evidence_items = _obj_value(evidence_result, "evidence", [])
    if not isinstance(evidence_items, list):
        return []

    anchor_id = str(_obj_value(anchor_account, "id", ""))
    candidate_id = str(_obj_value(candidate_account, "id", ""))
    anchor_source = _source(anchor_account)
    candidate_source = _source(candidate_account)

    matched: list[Any] = []
    positives: list[Any] = []
    for item in evidence_items:
        direction = _decision_value(_obj_value(item, "direction"))
        if direction and direction != "positive":
            continue
        positives.append(item)
        ids = {
            str(_obj_value(item, "source_account_a_id", "")),
            str(_obj_value(item, "source_account_b_id", "")),
        }
        sources = {
            _decision_value(_obj_value(item, "source_a")),
            _decision_value(_obj_value(item, "source_b")),
        }
        if anchor_id and candidate_id and {anchor_id, candidate_id}.issubset(ids):
            matched.append(item)
        elif anchor_source and candidate_source and {anchor_source, candidate_source}.issubset(sources):
            matched.append(item)

    return matched or positives[:20]


def _pair_conflicts(conflict_result: Any, anchor_account: Any, candidate_account: Any) -> list[Any]:
    conflict_items = _obj_value(conflict_result, "conflicts", [])
    if not isinstance(conflict_items, list):
        return []

    anchor_id = str(_obj_value(anchor_account, "id", ""))
    candidate_id = str(_obj_value(candidate_account, "id", ""))
    anchor_source = _source(anchor_account)
    candidate_source = _source(candidate_account)

    matched: list[Any] = []
    for item in conflict_items:
        ids = {
            str(_obj_value(item, "source_account_a_id", "")),
            str(_obj_value(item, "source_account_b_id", "")),
        }
        sources = {
            _decision_value(_obj_value(item, "source_a")),
            _decision_value(_obj_value(item, "source_b")),
        }
        if anchor_id and candidate_id and {anchor_id, candidate_id}.issubset(ids):
            matched.append(item)
        elif anchor_source and candidate_source and {anchor_source, candidate_source}.issubset(sources):
            matched.append(item)
    return matched


def _has_strong_promotion_group(groups: list[str]) -> bool:
    normalized = {item.lower() for item in groups}
    return bool(normalized.intersection(STRONG_PROMOTION_GROUPS))


def _has_blocking_conflict(blocking_conflict_types: list[str]) -> bool:
    normalized = {item.lower() for item in blocking_conflict_types}
    if normalized.intersection(BLOCKING_CONFLICT_HINTS):
        return True
    return any("high" in item or "blocking" in item for item in normalized)


def _has_rejection_supporting_conflict(conflict_types: list[str]) -> bool:
    normalized = {item.lower() for item in conflict_types}
    return bool(normalized.intersection(REJECTION_SUPPORTING_CONFLICT_TYPES))


def _model_name(gemini_client: Any | None) -> str | None:
    if gemini_client is None:
        return None
    return str(getattr(gemini_client, "model_name", None) or getattr(gemini_client, "model", None) or "gemini")
