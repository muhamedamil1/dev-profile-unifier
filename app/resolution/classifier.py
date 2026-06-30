from __future__ import annotations

from typing import Any

from app.schemas.classification import (
    AccountClassification,
    ClassificationResult,
    ClassificationThresholds,
    DecisionBasis,
    DecisionRiskLevel,
)
from app.schemas.enums import MatchDecision, PlatformSource
from app.schemas.scoring import (
    ConfidenceScore,
    ScoreComponentKind,
    ScoringResult,
    ScoreTargetType,
)
from app.schemas.source_account import SourceAccount


STRONG_IDENTITY_GROUPS = {
    "website",
    "profile_link",
    "email",
}

WEAK_IDENTITY_GROUPS = {
    "handle",
    "location",
    "bio",
    "topics",
}

REQUEST_IDENTITY_GROUPS = {
    "name",
    "email",
}

ANCHOR_PAIR_CORROBORATION_GROUPS = {
    "name",
    "website",
    "profile_link",
    "email",
}

HN_REQUIRED_STRONG_GROUPS = {
    "website",
    "profile_link",
    "email",
}

AUTO_BLOCKING_CONFLICT_TYPES = {
    "email_conflict",
}

REJECT_STRONG_CONFLICT_TYPES = {
    "email_conflict",
    "name_conflict",
    "website_conflict",
}


class DecisionClassifier:
    """
    Converts deterministic scores into match decisions.

    This class does not:
    - persist decisions
    - create canonical profiles
    - call LLMs
    - fetch external APIs
    """

    def classify(
        self,
        *,
        accounts: list[SourceAccount],
        scoring_result: ScoringResult,
        auto_match_threshold: float = 0.85,
        needs_review_threshold: float = 0.60,
        minimum_auto_match_independent_groups: int = 2,
    ) -> ClassificationResult:
        thresholds = ClassificationThresholds(
            auto_match_threshold=auto_match_threshold,
            needs_review_threshold=needs_review_threshold,
            confidence_cap=scoring_result.confidence_cap,
            minimum_auto_match_independent_groups=minimum_auto_match_independent_groups,
        )

        ordered_accounts = sorted(
            accounts,
            key=lambda account: account.expected_source_account_key(),
        )

        anchor_keys = {
            key.lower()
            for key in scoring_result.anchor_account_keys
        }

        account_scores = scoring_result.account_score_by_key
        pair_scores = scoring_result.pair_score_by_key

        classifications: list[AccountClassification] = []

        for account in ordered_accounts:
            account_key = account.expected_source_account_key()
            account_score = account_scores.get(account_key)

            if account_score is None:
                classification = self._classify_missing_score(
                    account=account,
                    thresholds=thresholds,
                )
            elif account_key in anchor_keys:
                classification = self._classify_anchor(
                    account=account,
                    account_score=account_score,
                    anchor_keys=anchor_keys,
                    account_scores=account_scores,
                    pair_scores=pair_scores,
                    thresholds=thresholds,
                )
            else:
                classification = self._classify_non_anchor(
                    account=account,
                    account_score=account_score,
                    anchor_keys=anchor_keys,
                    pair_scores=pair_scores,
                    thresholds=thresholds,
                )

            classifications.append(classification)

        return ClassificationResult(
            classifications=classifications,
            thresholds=thresholds,
            anchor_account_keys=sorted(anchor_keys),
        )

    def _classify_anchor(
        self,
        *,
        account: SourceAccount,
        account_score: ConfidenceScore,
        anchor_keys: set[str],
        account_scores: dict[str, ConfidenceScore],
        pair_scores: dict[str, ConfidenceScore],
        thresholds: ClassificationThresholds,
    ) -> AccountClassification:
        account_key = account.expected_source_account_key()

        multi_anchor_review = self._multi_anchor_review_classification(
            account=account,
            account_key=account_key,
            account_score=account_score,
            anchor_keys=anchor_keys,
            account_scores=account_scores,
            pair_scores=pair_scores,
        )

        if multi_anchor_review is not None:
            return multi_anchor_review

        return self._make_classification(
            account=account,
            decision=MatchDecision.AUTO_MATCH,
            basis=DecisionBasis.ANCHOR_INPUT,
            risk_level=DecisionRiskLevel.LOW,
            evidence_confidence_score=account_score.confidence_score,
            decision_confidence_score=max(
                account_score.confidence_score,
                thresholds.auto_match_threshold,
            ),
            account_score=account_score,
            best_pair_score=None,
            is_anchor=True,
            accepted_as_anchor=True,
            conflict_types=[],
            blocking_conflict_types=[],
            rationale=[
                "Account matched a platform identifier directly provided in the request.",
                "Accepted as a user-provided anchor for this resolution run.",
                "This is not external ownership verification; it is an evidence-based anchor decision.",
                *self._anchor_context_rationale(
                    account=account,
                    account_key=account_key,
                    anchor_keys=anchor_keys,
                    pair_scores=pair_scores,
                ),
            ],
            metadata={
                "anchor_policy": "direct_identifier_anchor",
                "decision_confidence_policy": "anchor_floor_applied",
                "anchor_floor": thresholds.auto_match_threshold,
                "evidence_score_before_anchor_policy": account_score.confidence_score,
                **self._anchor_context_metadata(
                    account=account,
                    account_key=account_key,
                    anchor_keys=anchor_keys,
                    pair_scores=pair_scores,
                ),
            },
        )

    def _classify_non_anchor(
        self,
        *,
        account: SourceAccount,
        account_score: ConfidenceScore,
        anchor_keys: set[str],
        pair_scores: dict[str, ConfidenceScore],
        thresholds: ClassificationThresholds,
    ) -> AccountClassification:
        account_key = account.expected_source_account_key()

        if not anchor_keys:
            return self._classify_without_anchor(
                account=account,
                account_score=account_score,
                pair_scores=pair_scores,
                thresholds=thresholds,
            )

        best_anchor_pair = self._best_anchor_pair(
            account_key=account_key,
            anchor_keys=anchor_keys,
            pair_scores=pair_scores,
        )
        anchor_pairs = self._anchor_pairs_for_account(
            account_key=account_key,
            anchor_keys=anchor_keys,
            pair_scores=pair_scores,
        )
        blocking_anchor_pair = self._most_blocking_anchor_pair(anchor_pairs=anchor_pairs)

        if best_anchor_pair is None:
            return self._make_classification(
                account=account,
                decision=MatchDecision.REJECT,
                basis=DecisionBasis.REJECTED_NO_SUPPORT,
                risk_level=DecisionRiskLevel.HIGH,
                evidence_confidence_score=account_score.confidence_score,
                decision_confidence_score=0.0,
                account_score=account_score,
                best_pair_score=None,
                is_anchor=False,
                accepted_as_anchor=False,
                conflict_types=[],
                blocking_conflict_types=[],
                rationale=[
                    "No account-pair evidence connects this discovered account to a user-provided anchor.",
                    "Rejected to avoid merging unsupported candidates.",
                ],
                metadata={
                    "anchor_policy": "no_pair_to_anchor",
                },
            )

        if self._should_reject_from_conflicts(best_anchor_pair):
            return self._make_classification(
                account=account,
                decision=MatchDecision.REJECT,
                basis=DecisionBasis.REJECTED_CONFLICT,
                risk_level=DecisionRiskLevel.HIGH,
                evidence_confidence_score=best_anchor_pair.confidence_score,
                decision_confidence_score=best_anchor_pair.confidence_score,
                account_score=account_score,
                best_pair_score=best_anchor_pair,
                is_anchor=False,
                accepted_as_anchor=False,
                conflict_types=self._conflict_types(best_anchor_pair),
                blocking_conflict_types=self._blocking_conflict_types(best_anchor_pair),
                rationale=[
                    "The account has strong contradictions against the best anchor connection.",
                    "Rejected because the conflict risk is higher than the supporting evidence.",
                ],
                metadata={
                    "anchor_policy": "conflict_rejection",
                },
            )

        if blocking_anchor_pair is not None:
            if self._should_reject_from_conflicts(blocking_anchor_pair):
                return self._make_classification(
                    account=account,
                    decision=MatchDecision.REJECT,
                    basis=DecisionBasis.REJECTED_CONFLICT,
                    risk_level=DecisionRiskLevel.HIGH,
                    evidence_confidence_score=blocking_anchor_pair.confidence_score,
                    decision_confidence_score=blocking_anchor_pair.confidence_score,
                    account_score=account_score,
                    best_pair_score=blocking_anchor_pair,
                    is_anchor=False,
                    accepted_as_anchor=False,
                    conflict_types=self._conflict_types(blocking_anchor_pair),
                    blocking_conflict_types=self._blocking_conflict_types(blocking_anchor_pair),
                    rationale=[
                        "The account has a blocking conflict with at least one user-provided anchor.",
                        "Even if it matches another anchor, the system rejects it because the conflict policy is reject-worthy.",
                    ],
                    metadata={
                        "anchor_policy": "blocking_conflict_with_any_anchor",
                        "matched_anchor_pair_key": best_anchor_pair.target_key,
                    },
                )

            return self._make_classification(
                account=account,
                decision=MatchDecision.NEEDS_REVIEW,
                basis=DecisionBasis.BLOCKING_CONFLICT_REVIEW,
                risk_level=DecisionRiskLevel.HIGH,
                evidence_confidence_score=max(
                    best_anchor_pair.confidence_score,
                    blocking_anchor_pair.confidence_score,
                ),
                decision_confidence_score=max(
                    best_anchor_pair.confidence_score,
                    blocking_anchor_pair.confidence_score,
                ),
                account_score=account_score,
                best_pair_score=blocking_anchor_pair,
                is_anchor=False,
                accepted_as_anchor=False,
                conflict_types=self._conflict_types(blocking_anchor_pair),
                blocking_conflict_types=self._blocking_conflict_types(blocking_anchor_pair),
                rationale=[
                    "The account has a blocking conflict with at least one user-provided anchor.",
                    "Even if it matches another anchor, the system keeps it for review to avoid a false merge.",
                ],
                metadata={
                    "anchor_policy": "blocking_conflict_with_any_anchor",
                    "matched_anchor_pair_key": best_anchor_pair.target_key,
                },
            )

        if self._can_auto_match_to_anchor(
            pair_score=best_anchor_pair,
            thresholds=thresholds,
        ):
            return self._make_classification(
                account=account,
                decision=MatchDecision.AUTO_MATCH,
                basis=DecisionBasis.STRONG_ANCHOR_PAIR,
                risk_level=DecisionRiskLevel.LOW,
                evidence_confidence_score=best_anchor_pair.confidence_score,
                decision_confidence_score=best_anchor_pair.confidence_score,
                account_score=account_score,
                best_pair_score=best_anchor_pair,
                is_anchor=False,
                accepted_as_anchor=False,
                conflict_types=self._conflict_types(best_anchor_pair),
                blocking_conflict_types=self._blocking_conflict_types(best_anchor_pair),
                rationale=[
                    "Account has strong evidence connecting it to a user-provided anchor.",
                    "Auto-match requirements were met: threshold, independent groups, strong signal, and no blocking conflict.",
                ],
                metadata={
                    "anchor_policy": "strong_pair_to_anchor",
                },
            )

        if self._should_needs_review(
            account_score=account_score,
            pair_score=best_anchor_pair,
            thresholds=thresholds,
        ):
            return self._make_classification(
                account=account,
                decision=MatchDecision.NEEDS_REVIEW,
                basis=DecisionBasis.AMBIGUOUS_ANCHOR_PAIR,
                risk_level=DecisionRiskLevel.MEDIUM,
                evidence_confidence_score=max(
                    account_score.confidence_score,
                    best_anchor_pair.confidence_score,
                ),
                decision_confidence_score=max(
                    account_score.confidence_score,
                    best_anchor_pair.confidence_score,
                ),
                account_score=account_score,
                best_pair_score=best_anchor_pair,
                is_anchor=False,
                accepted_as_anchor=False,
                conflict_types=self._conflict_types(best_anchor_pair),
                blocking_conflict_types=self._blocking_conflict_types(best_anchor_pair),
                rationale=[
                    "Account has some evidence connecting it to an anchor, but not enough for safe auto-match.",
                    "Marked needs_review to avoid a false merge.",
                ],
                metadata={
                    "anchor_policy": "ambiguous_pair_to_anchor",
                },
            )

        return self._make_classification(
            account=account,
            decision=MatchDecision.REJECT,
            basis=DecisionBasis.REJECTED_WEAK_ONLY,
            risk_level=DecisionRiskLevel.HIGH,
            evidence_confidence_score=max(
                account_score.confidence_score,
                best_anchor_pair.confidence_score,
            ),
            decision_confidence_score=max(
                account_score.confidence_score,
                best_anchor_pair.confidence_score,
            ),
            account_score=account_score,
            best_pair_score=best_anchor_pair,
            is_anchor=False,
            accepted_as_anchor=False,
            conflict_types=self._conflict_types(best_anchor_pair),
            blocking_conflict_types=self._blocking_conflict_types(best_anchor_pair),
            rationale=[
                "Account only has weak or insufficient support relative to the anchor.",
                "Rejected to avoid merging a weak discovered candidate.",
            ],
            metadata={
                "anchor_policy": "weak_pair_rejection",
            },
        )

    def _classify_without_anchor(
        self,
        *,
        account: SourceAccount,
        account_score: ConfidenceScore,
        pair_scores: dict[str, ConfidenceScore],
        thresholds: ClassificationThresholds,
    ) -> AccountClassification:
        account_key = account.expected_source_account_key()
        best_pair = self._best_any_pair(
            account_key=account_key,
            pair_scores=pair_scores,
        )

        best_pair_score = best_pair.confidence_score if best_pair else 0.0
        best_available_score = max(account_score.confidence_score, best_pair_score)

        if best_pair is not None and (
            self._has_auto_blocking_conflict(best_pair)
            or self._should_reject_from_conflicts(best_pair)
        ):
            return self._make_classification(
                account=account,
                decision=MatchDecision.REJECT,
                basis=DecisionBasis.REJECTED_CONFLICT,
                risk_level=DecisionRiskLevel.HIGH,
                evidence_confidence_score=best_available_score,
                decision_confidence_score=best_available_score,
                account_score=account_score,
                best_pair_score=best_pair,
                is_anchor=False,
                accepted_as_anchor=False,
                conflict_types=self._conflict_types(best_pair),
                blocking_conflict_types=self._blocking_conflict_types(best_pair),
                rationale=[
                    "No user-provided anchor exists and the strongest available pair has severe contradictions.",
                    "Rejected safely instead of treating a conflicting candidate as a likely match.",
                ],
                metadata={
                    "anchor_policy": "no_anchor_conflict_rejection",
                },
            )

        if best_pair is not None and self._is_weak_signal_only_pair(best_pair):
            return self._make_classification(
                account=account,
                decision=MatchDecision.REJECT,
                basis=DecisionBasis.REJECTED_WEAK_ONLY,
                risk_level=DecisionRiskLevel.HIGH,
                evidence_confidence_score=best_available_score,
                decision_confidence_score=best_available_score,
                account_score=account_score,
                best_pair_score=best_pair,
                is_anchor=False,
                accepted_as_anchor=False,
                conflict_types=self._conflict_types(best_pair),
                blocking_conflict_types=self._blocking_conflict_types(best_pair),
                rationale=[
                    "No user-provided anchor exists and available evidence is weak-signal-only.",
                    "Rejected to avoid merging name-generated or weakly discovered candidates.",
                ],
                metadata={
                    "anchor_policy": "no_anchor_weak_signal_rejection",
                },
            )

        if best_available_score >= thresholds.needs_review_threshold:
            return self._make_classification(
                account=account,
                decision=MatchDecision.NEEDS_REVIEW,
                basis=DecisionBasis.NO_ANCHOR_REVIEW,
                risk_level=DecisionRiskLevel.MEDIUM,
                evidence_confidence_score=best_available_score,
                decision_confidence_score=best_available_score,
                account_score=account_score,
                best_pair_score=best_pair,
                is_anchor=False,
                accepted_as_anchor=False,
                conflict_types=self._conflict_types(best_pair) if best_pair else [],
                blocking_conflict_types=self._blocking_conflict_types(best_pair) if best_pair else [],
                rationale=[
                    "No user-provided anchor exists for this run.",
                    "The account has enough evidence to require review, but no-anchor mode avoids auto-match.",
                ],
                metadata={
                    "anchor_policy": "no_anchor_conservative_review",
                },
            )

        return self._make_classification(
            account=account,
            decision=MatchDecision.REJECT,
            basis=DecisionBasis.REJECTED_NO_SUPPORT,
            risk_level=DecisionRiskLevel.HIGH,
            evidence_confidence_score=best_available_score,
            decision_confidence_score=best_available_score,
            account_score=account_score,
            best_pair_score=best_pair,
            is_anchor=False,
            accepted_as_anchor=False,
            conflict_types=self._conflict_types(best_pair) if best_pair else [],
            blocking_conflict_types=self._blocking_conflict_types(best_pair) if best_pair else [],
            rationale=[
                "No user-provided anchor exists and available evidence is below review threshold.",
                "Rejected to avoid merging name-generated candidates without strong support.",
            ],
            metadata={
                "anchor_policy": "no_anchor_rejection",
            },
        )

    def _classify_missing_score(
        self,
        *,
        account: SourceAccount,
        thresholds: ClassificationThresholds,
    ) -> AccountClassification:
        return AccountClassification(
            source_account_id=account.id,
            source_account_key=account.expected_source_account_key(),
            source=account.source,
            decision=MatchDecision.REJECT,
            decision_basis=DecisionBasis.REJECTED_NO_SUPPORT,
            risk_level=DecisionRiskLevel.HIGH,
            evidence_confidence_score=0.0,
            decision_confidence_score=0.0,
            account_score=0.0,
            best_pair_score=None,
            is_anchor=False,
            accepted_as_anchor=False,
            rationale=[
                "No score was available for this account.",
                "Rejected safely instead of guessing.",
            ],
            metadata={
                "thresholds": thresholds.model_dump(),
            },
        )

    def _can_auto_match_to_anchor(
        self,
        *,
        pair_score: ConfidenceScore,
        thresholds: ClassificationThresholds,
    ) -> bool:
        if pair_score.confidence_score < thresholds.auto_match_threshold:
            return False

        if pair_score.weak_signal_only:
            return False

        if len(pair_score.independent_positive_groups) < thresholds.minimum_auto_match_independent_groups:
            return False

        if not set(pair_score.strong_positive_groups) & STRONG_IDENTITY_GROUPS:
            return False

        if self._has_auto_blocking_conflict(pair_score):
            return False

        if pair_score.hn_conservative:
            if not set(pair_score.strong_positive_groups) & HN_REQUIRED_STRONG_GROUPS:
                return False

            if pair_score.hn_requires_strong_evidence and pair_score.weak_signal_only:
                return False

        return True

    def _should_needs_review(
        self,
        *,
        account_score: ConfidenceScore,
        pair_score: ConfidenceScore,
        thresholds: ClassificationThresholds,
    ) -> bool:
        if pair_score.confidence_score >= thresholds.needs_review_threshold:
            return True

        if account_score.confidence_score >= thresholds.needs_review_threshold:
            return True

        if pair_score.positive_signal_count > 0:
            return True

        if pair_score.conflict_count > 0:
            return True

        return False

    def _should_reject_from_conflicts(self, pair_score: ConfidenceScore) -> bool:
        conflict_types = set(self._conflict_types(pair_score))

        if "email_conflict" in conflict_types and self._has_email_hash_conflict(pair_score):
            return True

        if {"name_conflict", "website_conflict"} <= conflict_types:
            if pair_score.confidence_score < 0.60:
                return True

        if len(conflict_types & REJECT_STRONG_CONFLICT_TYPES) >= 2:
            if pair_score.confidence_score < 0.50:
                return True

        return False

    def _has_auto_blocking_conflict(self, pair_score: ConfidenceScore) -> bool:
        for component in pair_score.components:
            if component.kind != ScoreComponentKind.CONFLICT_PENALTY:
                continue

            severity = str(component.metadata.get("severity") or "").lower()
            if severity in {"high", "critical"}:
                return True

            if (
                component.signal_type == "email_conflict"
                and component.metadata.get("conflict_basis") == "email_hash"
            ):
                return True

        return False

    def _has_email_hash_conflict(self, pair_score: ConfidenceScore) -> bool:
        for component in pair_score.components:
            if component.kind != ScoreComponentKind.CONFLICT_PENALTY:
                continue

            if component.signal_type != "email_conflict":
                continue

            if component.metadata.get("conflict_basis") == "email_hash":
                return True

        return False

    def _conflict_types(self, pair_score: ConfidenceScore | None) -> list[str]:
        if pair_score is None:
            return []

        return sorted(
            {
                component.signal_type
                for component in pair_score.components
                if component.kind == ScoreComponentKind.CONFLICT_PENALTY
            }
        )

    def _blocking_conflict_types(self, pair_score: ConfidenceScore | None) -> list[str]:
        if pair_score is None:
            return []

        blocking: set[str] = set()

        for component in pair_score.components:
            if component.kind != ScoreComponentKind.CONFLICT_PENALTY:
                continue

            severity = str(component.metadata.get("severity") or "").lower()

            if (
                component.signal_type == "email_conflict"
                and component.metadata.get("conflict_basis") == "email_hash"
            ):
                blocking.add(component.signal_type)

            if severity in {"high", "critical"}:
                blocking.add(component.signal_type)

        return sorted(blocking)

    def _best_anchor_pair(
        self,
        *,
        account_key: str,
        anchor_keys: set[str],
        pair_scores: dict[str, ConfidenceScore],
    ) -> ConfidenceScore | None:
        candidates = self._anchor_pairs_for_account(
            account_key=account_key,
            anchor_keys=anchor_keys,
            pair_scores=pair_scores,
        )

        if not candidates:
            return None

        return max(candidates, key=self._pair_preference_key)

    def _best_any_pair(
        self,
        *,
        account_key: str,
        pair_scores: dict[str, ConfidenceScore],
    ) -> ConfidenceScore | None:
        candidates = [
            score
            for score in pair_scores.values()
            if score.source_account_key == account_key
            or score.target_account_key == account_key
        ]

        if not candidates:
            return None

        return max(candidates, key=self._pair_preference_key)

    def _anchor_pairs_for_account(
        self,
        *,
        account_key: str,
        anchor_keys: set[str],
        pair_scores: dict[str, ConfidenceScore],
    ) -> list[ConfidenceScore]:
        candidates: list[ConfidenceScore] = []

        for anchor_key in sorted(anchor_keys):
            if anchor_key == account_key:
                continue

            pair_key = self._pair_key(account_key, anchor_key)
            score = pair_scores.get(pair_key)

            if score is not None:
                candidates.append(score)

        return candidates

    def _most_blocking_anchor_pair(
        self,
        *,
        anchor_pairs: list[ConfidenceScore],
    ) -> ConfidenceScore | None:
        blocking = [
            pair_score
            for pair_score in anchor_pairs
            if self._has_auto_blocking_conflict(pair_score)
            or self._should_reject_from_conflicts(pair_score)
            or (pair_score.conflict_count > 0 and pair_score.confidence_score < 0.60)
        ]

        if not blocking:
            return None

        return max(
            blocking,
            key=lambda item: (
                len(self._blocking_conflict_types(item)),
                self._should_reject_from_conflicts(item),
                item.conflict_count,
                abs(item.conflict_penalty),
                item.target_key,
            ),
        )

    def _most_problematic_anchor_pair(
        self,
        *,
        account_key: str,
        anchor_keys: set[str],
        pair_scores: dict[str, ConfidenceScore],
    ) -> ConfidenceScore | None:
        problematic: list[ConfidenceScore] = []

        for other_anchor in anchor_keys:
            if other_anchor == account_key:
                continue

            pair_key = self._pair_key(account_key, other_anchor)
            pair_score = pair_scores.get(pair_key)

            if pair_score is None:
                continue

            if self._has_auto_blocking_conflict(pair_score):
                problematic.append(pair_score)
                continue

            if pair_score.conflict_count > 0 and pair_score.confidence_score < 0.60:
                problematic.append(pair_score)

        if not problematic:
            return None

        return max(
            problematic,
            key=lambda item: (
                len(self._blocking_conflict_types(item)),
                abs(item.conflict_penalty),
                item.target_key,
            ),
        )

    def _has_uncorroborated_multi_anchor_context(
        self,
        *,
        account_key: str,
        anchor_keys: set[str],
        pair_scores: dict[str, ConfidenceScore],
    ) -> bool:
        if len(anchor_keys) <= 1:
            return False

        anchor_pairs = self._anchor_pairs_for_account(
            account_key=account_key,
            anchor_keys=anchor_keys,
            pair_scores=pair_scores,
        )

        return not any(pair_score.positive_signal_count > 0 for pair_score in anchor_pairs)

    def _multi_anchor_review_classification(
        self,
        *,
        account: SourceAccount,
        account_key: str,
        account_score: ConfidenceScore,
        anchor_keys: set[str],
        account_scores: dict[str, ConfidenceScore],
        pair_scores: dict[str, ConfidenceScore],
    ) -> AccountClassification | None:
        """
        Apply the multi-anchor consistency gate.

        A direct platform identifier means the user intentionally supplied the
        account for this run. It does not prove that every supplied platform
        account belongs to the same person. When two or more direct anchors are
        present, an anchor must have either request-identity support
        (name/email) or account-pair corroboration with another anchor before it
        can be accepted into the canonical profile.
        """

        if len(anchor_keys) <= 1:
            return None

        anchor_pairs = self._anchor_pairs_for_account(
            account_key=account_key,
            anchor_keys=anchor_keys,
            pair_scores=pair_scores,
        )
        problematic_pair = self._most_problematic_anchor_pair(
            account_key=account_key,
            anchor_keys=anchor_keys,
            pair_scores=pair_scores,
        )
        corroborating_pair = self._best_corroborating_anchor_pair(anchor_pairs)
        best_available_pair = problematic_pair or corroborating_pair or self._best_pair_from_list(anchor_pairs)

        has_request_identity = self._has_request_identity_support(account_score)
        other_request_identity_keys = self._other_anchor_keys_with_request_identity(
            current_key=account_key,
            anchor_keys=anchor_keys,
            account_scores=account_scores,
        )

        if problematic_pair is not None:
            other_key = self._other_account_key(account_key, problematic_pair)
            other_score = account_scores.get(other_key or "")
            other_has_request_identity = self._has_request_identity_support(other_score)

            # If this anchor has request-name/email support and the conflicting
            # anchor only has a direct identifier, keep this anchor accepted and
            # let the lower-support conflicting anchor be held for review.
            if has_request_identity and not other_has_request_identity:
                return None

            conflict_types = self._conflict_types(problematic_pair)
            blocking_types = self._blocking_conflict_types(problematic_pair)

            return self._make_classification(
                account=account,
                decision=MatchDecision.NEEDS_REVIEW,
                basis=DecisionBasis.BLOCKING_CONFLICT_REVIEW,
                risk_level=DecisionRiskLevel.HIGH,
                evidence_confidence_score=account_score.confidence_score,
                decision_confidence_score=account_score.confidence_score,
                account_score=account_score,
                best_pair_score=problematic_pair,
                is_anchor=True,
                accepted_as_anchor=False,
                conflict_types=conflict_types,
                blocking_conflict_types=blocking_types,
                rationale=[
                    "This account was directly provided by the user, but it conflicts with another directly provided anchor account.",
                    "Direct input is treated as user intent, not external ownership verification.",
                    "The system keeps this anchor out of accepted canonical sources until the contradictory direct inputs are reviewed.",
                ],
                metadata={
                    "anchor_policy": "conflicting_direct_inputs_require_review",
                    "multi_anchor_gate": "failed_conflicting_anchor_pair",
                    "has_request_identity_support": has_request_identity,
                    "other_anchor_has_request_identity_support": other_has_request_identity,
                    "conflicting_anchor_key": other_key,
                },
            )

        if corroborating_pair is not None:
            return None

        if has_request_identity:
            return None

        if other_request_identity_keys:
            return self._make_classification(
                account=account,
                decision=MatchDecision.NEEDS_REVIEW,
                basis=DecisionBasis.AMBIGUOUS_ANCHOR_PAIR,
                risk_level=DecisionRiskLevel.HIGH,
                evidence_confidence_score=account_score.confidence_score,
                decision_confidence_score=account_score.confidence_score,
                account_score=account_score,
                best_pair_score=best_available_pair,
                is_anchor=True,
                accepted_as_anchor=False,
                conflict_types=self._conflict_types(best_available_pair),
                blocking_conflict_types=self._blocking_conflict_types(best_available_pair),
                rationale=[
                    "This account was directly provided by the user, but another direct anchor has stronger request-identity evidence.",
                    "No corroborating account-pair evidence connects this anchor to the stronger requested identity anchor.",
                    "The system preserves it for review instead of accepting it into the canonical profile.",
                ],
                metadata={
                    "anchor_policy": "multi_anchor_requires_request_identity_or_corroboration",
                    "multi_anchor_gate": "failed_weaker_than_request_identity_anchor",
                    "has_request_identity_support": False,
                    "request_identity_anchor_keys": other_request_identity_keys,
                },
            )

        return self._make_classification(
            account=account,
            decision=MatchDecision.NEEDS_REVIEW,
            basis=DecisionBasis.AMBIGUOUS_ANCHOR_PAIR,
            risk_level=DecisionRiskLevel.MEDIUM,
            evidence_confidence_score=account_score.confidence_score,
            decision_confidence_score=account_score.confidence_score,
            account_score=account_score,
            best_pair_score=best_available_pair,
            is_anchor=True,
            accepted_as_anchor=False,
            conflict_types=self._conflict_types(best_available_pair),
            blocking_conflict_types=self._blocking_conflict_types(best_available_pair),
            rationale=[
                "Multiple direct platform identifiers were provided, but this anchor has no request-name/email support and no corroborating account-pair evidence.",
                "Direct input is user intent only; it is not enough to merge multiple accounts into one identity.",
                "The account is preserved for review and excluded from accepted canonical sources.",
            ],
            metadata={
                "anchor_policy": "multi_anchor_requires_request_identity_or_corroboration",
                "multi_anchor_gate": "failed_uncorroborated_direct_anchor",
                "has_request_identity_support": False,
            },
        )

    def _has_request_identity_support(self, account_score: ConfidenceScore | None) -> bool:
        if account_score is None:
            return False

        groups = {str(group).lower() for group in account_score.independent_positive_groups}
        return bool(groups & REQUEST_IDENTITY_GROUPS)

    def _other_anchor_keys_with_request_identity(
        self,
        *,
        current_key: str,
        anchor_keys: set[str],
        account_scores: dict[str, ConfidenceScore],
    ) -> list[str]:
        supported: list[str] = []

        for anchor_key in sorted(anchor_keys):
            if anchor_key == current_key:
                continue
            if self._has_request_identity_support(account_scores.get(anchor_key)):
                supported.append(anchor_key)

        return supported

    def _best_corroborating_anchor_pair(
        self,
        anchor_pairs: list[ConfidenceScore],
    ) -> ConfidenceScore | None:
        candidates = [
            pair_score
            for pair_score in anchor_pairs
            if self._is_corroborating_anchor_pair(pair_score)
        ]

        return self._best_pair_from_list(candidates)

    def _best_pair_from_list(
        self,
        candidates: list[ConfidenceScore],
    ) -> ConfidenceScore | None:
        if not candidates:
            return None
        return max(candidates, key=self._pair_preference_key)

    def _is_corroborating_anchor_pair(self, pair_score: ConfidenceScore) -> bool:
        if pair_score.confidence_score <= 0:
            return False

        if self._has_auto_blocking_conflict(pair_score):
            return False

        if self._should_reject_from_conflicts(pair_score):
            return False

        if pair_score.conflict_count > 0 and not pair_score.strong_positive_groups:
            return False

        groups = {str(group).lower() for group in pair_score.independent_positive_groups}
        if not groups & ANCHOR_PAIR_CORROBORATION_GROUPS:
            return False

        if groups == {"name"}:
            return pair_score.confidence_score >= 0.20 and pair_score.conflict_count == 0

        return True

    def _anchor_context_rationale(
        self,
        *,
        account: SourceAccount,
        account_key: str,
        anchor_keys: set[str],
        pair_scores: dict[str, ConfidenceScore],
    ) -> list[str]:
        rationale: list[str] = []

        if account.source == PlatformSource.HACKERNEWS:
            rationale.append(
                "Hacker News profiles are sparse, so this user-provided anchor should be treated conservatively downstream."
            )

        if self._has_uncorroborated_multi_anchor_context(
            account_key=account_key,
            anchor_keys=anchor_keys,
            pair_scores=pair_scores,
        ):
            rationale.append(
                "Multiple direct anchors were provided, but this anchor has no corroborating pair evidence with the other anchors."
            )

        return rationale

    def _anchor_context_metadata(
        self,
        *,
        account: SourceAccount,
        account_key: str,
        anchor_keys: set[str],
        pair_scores: dict[str, ConfidenceScore],
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {}

        if account.source == PlatformSource.HACKERNEWS:
            metadata["hn_conservative"] = True

        if self._has_uncorroborated_multi_anchor_context(
            account_key=account_key,
            anchor_keys=anchor_keys,
            pair_scores=pair_scores,
        ):
            metadata["uncorroborated_multi_anchor"] = True

        return metadata

    def _is_weak_signal_only_pair(self, pair_score: ConfidenceScore) -> bool:
        return pair_score.weak_signal_only or not pair_score.strong_positive_groups

    def _pair_preference_key(self, item: ConfidenceScore) -> tuple[float, int, int, int, float, float, str]:
        return (
            item.confidence_score,
            len(item.strong_positive_groups),
            len(item.independent_positive_groups),
            -item.conflict_count,
            item.positive_score,
            -abs(item.conflict_penalty),
            item.target_key,
        )

    def _make_classification(
        self,
        *,
        account: SourceAccount,
        decision: MatchDecision,
        basis: DecisionBasis,
        risk_level: DecisionRiskLevel,
        evidence_confidence_score: float,
        decision_confidence_score: float,
        account_score: ConfidenceScore,
        best_pair_score: ConfidenceScore | None,
        is_anchor: bool,
        accepted_as_anchor: bool,
        conflict_types: list[str],
        blocking_conflict_types: list[str],
        rationale: list[str],
        metadata: dict[str, Any],
    ) -> AccountClassification:
        best_anchor_account_key = self._other_account_key(
            account.expected_source_account_key(),
            best_pair_score,
        )

        pair_groups = best_pair_score.independent_positive_groups if best_pair_score else []
        pair_strong_groups = best_pair_score.strong_positive_groups if best_pair_score else []
        pair_weak_groups = best_pair_score.weak_positive_groups if best_pair_score else []
        hn_conservative = best_pair_score.hn_conservative if best_pair_score else account_score.hn_conservative
        hn_requires_strong_evidence = (
            best_pair_score.hn_requires_strong_evidence
            if best_pair_score
            else account_score.hn_requires_strong_evidence
        )
        final_rationale = list(rationale)

        if hn_conservative and not any("hacker news" in item.lower() for item in final_rationale):
            final_rationale.append(
                "Hacker News profiles are sparse, so HN-derived matches require conservative handling and stronger corroboration."
            )

        return AccountClassification(
            source_account_id=account.id,
            source_account_key=account.expected_source_account_key(),
            source=account.source,
            decision=decision,
            decision_basis=basis,
            risk_level=risk_level,
            evidence_confidence_score=round(evidence_confidence_score, 4),
            decision_confidence_score=round(decision_confidence_score, 4),
            account_score=round(account_score.confidence_score, 4),
            best_pair_score=round(best_pair_score.confidence_score, 4) if best_pair_score else None,
            is_anchor=is_anchor,
            accepted_as_anchor=accepted_as_anchor,
            best_anchor_account_key=best_anchor_account_key,
            best_pair_key=best_pair_score.target_key if best_pair_score else None,
            independent_positive_groups=pair_groups or account_score.independent_positive_groups,
            strong_positive_groups=pair_strong_groups or account_score.strong_positive_groups,
            weak_positive_groups=pair_weak_groups or account_score.weak_positive_groups,
            weak_signal_only=best_pair_score.weak_signal_only if best_pair_score else account_score.weak_signal_only,
            hn_conservative=hn_conservative,
            hn_requires_strong_evidence=hn_requires_strong_evidence,
            conflict_types=conflict_types,
            blocking_conflict_types=blocking_conflict_types,
            rationale=final_rationale,
            metadata={
                **metadata,
                "account_score_explanation": account_score.explanation,
                "best_pair_score_explanation": best_pair_score.explanation if best_pair_score else [],
            },
        )

    def _other_account_key(
        self,
        current_key: str,
        pair_score: ConfidenceScore | None,
    ) -> str | None:
        if pair_score is None:
            return None

        if pair_score.source_account_key == current_key:
            return pair_score.target_account_key

        if pair_score.target_account_key == current_key:
            return pair_score.source_account_key

        return None

    def _pair_key(self, left_key: str, right_key: str) -> str:
        return "::".join(sorted([left_key.lower(), right_key.lower()]))