from __future__ import annotations

from itertools import combinations
from typing import Any

from app.schemas.conflicts import ConflictType, DetectedConflict
from app.schemas.evidence import (
    EvidenceIndependenceGroup,
    EvidenceTargetType,
    EvidenceType,
    ExtractedEvidence,
)
from app.schemas.enums import PlatformSource
from app.schemas.scoring import (
    ConfidenceScore,
    ScoreComponent,
    ScoreComponentKind,
    ScoreTargetType,
    ScoringResult,
)
from app.schemas.source_account import SourceAccount


CONFIDENCE_FLOOR = 0.0
CONFIDENCE_CAP = 0.97
MAX_PAIR_CONFLICT_PENALTY = -0.95


GROUP_SCORE_CAPS: dict[str, float] = {
    EvidenceIndependenceGroup.INPUT_IDENTIFIER.value: 0.25,
    EvidenceIndependenceGroup.NAME.value: 0.20,
    EvidenceIndependenceGroup.WEBSITE.value: 0.30,
    EvidenceIndependenceGroup.PROFILE_LINK.value: 0.45,
    EvidenceIndependenceGroup.HANDLE.value: 0.12,
    EvidenceIndependenceGroup.EMAIL.value: 0.35,
    EvidenceIndependenceGroup.LOCATION.value: 0.08,
    EvidenceIndependenceGroup.BIO.value: 0.08,
    EvidenceIndependenceGroup.TOPICS.value: 0.12,
}


STRONG_ACCOUNT_GROUPS = {
    EvidenceIndependenceGroup.INPUT_IDENTIFIER.value,
    EvidenceIndependenceGroup.EMAIL.value,
}

STRONG_PAIR_GROUPS = {
    EvidenceIndependenceGroup.WEBSITE.value,
    EvidenceIndependenceGroup.PROFILE_LINK.value,
    EvidenceIndependenceGroup.EMAIL.value,
}

WEAK_GROUPS = {
    EvidenceIndependenceGroup.HANDLE.value,
    EvidenceIndependenceGroup.LOCATION.value,
    EvidenceIndependenceGroup.BIO.value,
    EvidenceIndependenceGroup.TOPICS.value,
}

ANCHOR_EVIDENCE_TYPES = {
    EvidenceType.INPUT_HANDLE_MATCH.value,
    EvidenceType.EMAIL_HINT_MATCH.value,
}


class ResolutionScorer:
    """
    Deterministic scoring engine.

    This class does not classify decisions.
    It only converts evidence/conflicts into confidence scores.
    """

    def score(
        self,
        *,
        accounts: list[SourceAccount],
        evidence: list[ExtractedEvidence],
        conflicts: list[DetectedConflict],
    ) -> ScoringResult:
        ordered_accounts = sorted(
            accounts,
            key=lambda account: account.expected_source_account_key(),
        )

        request_evidence_by_account = self._request_evidence_by_account(evidence)
        pair_evidence_by_key = self._pair_evidence_by_key(evidence)
        conflicts_by_pair_key = self._conflicts_by_pair_key(conflicts)

        account_scores = [
            self._score_account(
                account=account,
                evidence=request_evidence_by_account.get(
                    account.expected_source_account_key(),
                    [],
                ),
            )
            for account in ordered_accounts
        ]

        pair_scores: list[ConfidenceScore] = []

        for left, right in combinations(ordered_accounts, 2):
            pair_key = self._pair_key(
                left.expected_source_account_key(),
                right.expected_source_account_key(),
            )

            pair_scores.append(
                self._score_pair(
                    left=left,
                    right=right,
                    evidence=pair_evidence_by_key.get(pair_key, []),
                    conflicts=conflicts_by_pair_key.get(pair_key, []),
                )
            )

        anchor_account_keys = self._anchor_account_keys(evidence)

        return ScoringResult(
            account_scores=account_scores,
            pair_scores=pair_scores,
            confidence_cap=CONFIDENCE_CAP,
            anchor_account_keys=anchor_account_keys,
        )

    def _score_account(
        self,
        *,
        account: SourceAccount,
        evidence: list[ExtractedEvidence],
    ) -> ConfidenceScore:
        positive_score, positive_components, discarded_components = self._score_positive_evidence(
            evidence=evidence,
        )

        components = [
            *positive_components,
            *discarded_components,
        ]

        conflict_penalty = 0.0
        score_before_cap = positive_score + conflict_penalty
        final_score = self._clamp(score_before_cap)

        independent_groups = self._independent_groups(positive_components)
        strong_groups = sorted(set(independent_groups) & STRONG_ACCOUNT_GROUPS)
        weak_groups = sorted(set(independent_groups) & WEAK_GROUPS)

        hn_conservative = account.source == PlatformSource.HACKERNEWS or self._has_hn_metadata(evidence)

        weak_signal_only = bool(independent_groups) and not strong_groups

        explanation = self._account_explanation(
            account=account,
            positive_score=positive_score,
            score_before_cap=score_before_cap,
            final_score=final_score,
            independent_groups=independent_groups,
            weak_signal_only=weak_signal_only,
            hn_conservative=hn_conservative,
        )

        return ConfidenceScore(
            target_type=ScoreTargetType.ACCOUNT,
            target_key=account.expected_source_account_key(),
            source_account_key=account.expected_source_account_key(),
            source=account.source,
            positive_score=round(positive_score, 4),
            conflict_penalty=0.0,
            score_before_cap=round(score_before_cap, 4),
            confidence_score=round(final_score, 4),
            positive_signal_count=len(positive_components),
            raw_positive_signal_count=len(evidence),
            conflict_count=0,
            raw_conflict_count=0,
            independent_positive_groups=independent_groups,
            strong_positive_groups=strong_groups,
            weak_positive_groups=weak_groups,
            weak_signal_only=weak_signal_only,
            hn_conservative=hn_conservative,
            hn_requires_strong_evidence=hn_conservative and weak_signal_only,
            components=components,
            explanation=explanation,
        )

    def _score_pair(
        self,
        *,
        left: SourceAccount,
        right: SourceAccount,
        evidence: list[ExtractedEvidence],
        conflicts: list[DetectedConflict],
    ) -> ConfidenceScore:
        positive_score, positive_components, discarded_positive_components = self._score_positive_evidence(
            evidence=evidence,
        )

        conflict_penalty, conflict_components, discarded_conflict_components = self._score_conflicts(
            conflicts=conflicts,
        )

        components = [
            *positive_components,
            *discarded_positive_components,
            *conflict_components,
            *discarded_conflict_components,
        ]

        score_before_cap = positive_score + conflict_penalty
        final_score = self._clamp(score_before_cap)

        independent_groups = self._independent_groups(positive_components)
        strong_groups = sorted(set(independent_groups) & STRONG_PAIR_GROUPS)
        weak_groups = sorted(set(independent_groups) & WEAK_GROUPS)

        hn_conservative = (
            left.source == PlatformSource.HACKERNEWS
            or right.source == PlatformSource.HACKERNEWS
            or self._has_hn_metadata(evidence)
            or self._has_hn_metadata(conflicts)
        )

        weak_signal_only = bool(independent_groups) and not strong_groups

        explanation = self._pair_explanation(
            left=left,
            right=right,
            positive_score=positive_score,
            conflict_penalty=conflict_penalty,
            score_before_cap=score_before_cap,
            final_score=final_score,
            independent_groups=independent_groups,
            strong_groups=strong_groups,
            weak_signal_only=weak_signal_only,
            hn_conservative=hn_conservative,
        )

        return ConfidenceScore(
            target_type=ScoreTargetType.ACCOUNT_PAIR,
            target_key=self._pair_key(
                left.expected_source_account_key(),
                right.expected_source_account_key(),
            ),
            source_account_key=left.expected_source_account_key(),
            source=left.source,
            target_account_key=right.expected_source_account_key(),
            target_source=right.source,
            positive_score=round(positive_score, 4),
            conflict_penalty=round(conflict_penalty, 4),
            score_before_cap=round(score_before_cap, 4),
            confidence_score=round(final_score, 4),
            positive_signal_count=len(positive_components),
            raw_positive_signal_count=len(evidence),
            conflict_count=len(conflict_components),
            raw_conflict_count=len(conflicts),
            independent_positive_groups=independent_groups,
            strong_positive_groups=strong_groups,
            weak_positive_groups=weak_groups,
            weak_signal_only=weak_signal_only,
            hn_conservative=hn_conservative,
            hn_requires_strong_evidence=hn_conservative and weak_signal_only,
            components=components,
            explanation=explanation,
        )

    def _score_positive_evidence(
        self,
        *,
        evidence: list[ExtractedEvidence],
    ) -> tuple[float, list[ScoreComponent], list[ScoreComponent]]:
        best_by_group: dict[str, ExtractedEvidence] = {}

        for item in evidence:
            group = item.independence_group.value
            cap = GROUP_SCORE_CAPS.get(group, item.weight)
            effective_weight = min(item.weight, cap)

            existing = best_by_group.get(group)
            if existing is None:
                best_by_group[group] = item
                continue

            existing_cap = GROUP_SCORE_CAPS.get(
                existing.independence_group.value,
                existing.weight,
            )
            existing_effective_weight = min(existing.weight, existing_cap)

            if effective_weight > existing_effective_weight:
                best_by_group[group] = item

        applied_components: list[ScoreComponent] = []
        discarded_components: list[ScoreComponent] = []

        total = 0.0

        applied_ids = {id(item) for item in best_by_group.values()}

        for item in evidence:
            group = item.independence_group.value
            cap = GROUP_SCORE_CAPS.get(group, item.weight)
            applied_weight = min(item.weight, cap)

            if id(item) in applied_ids:
                total += applied_weight
                applied_components.append(
                    self._positive_component(
                        item=item,
                        raw_weight=item.weight,
                        applied_weight=applied_weight,
                    )
                )
            else:
                discarded_components.append(
                    self._discarded_positive_component(
                        item=item,
                        raw_weight=item.weight,
                    )
                )

        return round(total, 4), applied_components, discarded_components

    def _score_conflicts(
        self,
        *,
        conflicts: list[DetectedConflict],
    ) -> tuple[float, list[ScoreComponent], list[ScoreComponent]]:
        best_by_type: dict[str, DetectedConflict] = {}

        for conflict in conflicts:
            conflict_type = conflict.conflict_type.value
            existing = best_by_type.get(conflict_type)

            if existing is None:
                best_by_type[conflict_type] = conflict
                continue

            if conflict.penalty < existing.penalty:
                best_by_type[conflict_type] = conflict

        applied_components: list[ScoreComponent] = []
        discarded_components: list[ScoreComponent] = []

        total = 0.0
        applied_ids = {id(item) for item in best_by_type.values()}

        for conflict in conflicts:
            if id(conflict) in applied_ids:
                total += conflict.penalty
                applied_components.append(
                    self._conflict_component(
                        conflict=conflict,
                        applied_weight=conflict.penalty,
                    )
                )
            else:
                discarded_components.append(
                    self._discarded_conflict_component(conflict=conflict)
                )

        total = max(total, MAX_PAIR_CONFLICT_PENALTY)

        return round(total, 4), applied_components, discarded_components

    def _positive_component(
        self,
        *,
        item: ExtractedEvidence,
        raw_weight: float,
        applied_weight: float,
    ) -> ScoreComponent:
        return ScoreComponent(
            kind=ScoreComponentKind.POSITIVE_EVIDENCE,
            signal_type=item.evidence_type.value,
            raw_weight=round(raw_weight, 4),
            applied_weight=round(applied_weight, 4),
            independence_group=item.independence_group.value,
            source_account_key=item.source_account_key,
            target_account_key=item.target_account_key,
            reason=item.reason,
            metadata={
                **item.metadata,
                "score_basis": "best_signal_per_independence_group",
            },
        )

    def _discarded_positive_component(
        self,
        *,
        item: ExtractedEvidence,
        raw_weight: float,
    ) -> ScoreComponent:
        return ScoreComponent(
            kind=ScoreComponentKind.GROUP_CAP_DISCARDED,
            signal_type=item.evidence_type.value,
            raw_weight=round(raw_weight, 4),
            applied_weight=0.0,
            independence_group=item.independence_group.value,
            source_account_key=item.source_account_key,
            target_account_key=item.target_account_key,
            reason=(
                "Signal was not added because a stronger signal from the same "
                "independence group was already applied."
            ),
            metadata={
                **item.metadata,
                "score_basis": "group_cap",
            },
        )

    def _conflict_component(
        self,
        *,
        conflict: DetectedConflict,
        applied_weight: float,
    ) -> ScoreComponent:
        return ScoreComponent(
            kind=ScoreComponentKind.CONFLICT_PENALTY,
            signal_type=conflict.conflict_type.value,
            raw_weight=round(conflict.penalty, 4),
            applied_weight=round(applied_weight, 4),
            source_account_key=conflict.source_account_key,
            target_account_key=conflict.target_account_key,
            reason=conflict.description,
            metadata={
                **conflict.metadata,
                "severity": conflict.severity.value,
                "score_basis": "conflict_penalty",
            },
        )

    def _discarded_conflict_component(
        self,
        *,
        conflict: DetectedConflict,
    ) -> ScoreComponent:
        return ScoreComponent(
            kind=ScoreComponentKind.CONFLICT_DUPLICATE_DISCARDED,
            signal_type=conflict.conflict_type.value,
            raw_weight=round(conflict.penalty, 4),
            applied_weight=0.0,
            source_account_key=conflict.source_account_key,
            target_account_key=conflict.target_account_key,
            reason=(
                "Conflict was not added because a stronger conflict of the same "
                "type was already applied for this pair."
            ),
            metadata={
                **conflict.metadata,
                "severity": conflict.severity.value,
                "score_basis": "conflict_type_cap",
            },
        )

    def _request_evidence_by_account(
        self,
        evidence: list[ExtractedEvidence],
    ) -> dict[str, list[ExtractedEvidence]]:
        grouped: dict[str, list[ExtractedEvidence]] = {}

        for item in evidence:
            if item.target_type != EvidenceTargetType.REQUEST:
                continue

            grouped.setdefault(item.source_account_key, []).append(item)

        return grouped

    def _pair_evidence_by_key(
        self,
        evidence: list[ExtractedEvidence],
    ) -> dict[str, list[ExtractedEvidence]]:
        grouped: dict[str, list[ExtractedEvidence]] = {}

        for item in evidence:
            if item.target_type != EvidenceTargetType.ACCOUNT_PAIR:
                continue

            if not item.target_account_key:
                continue

            pair_key = self._pair_key(
                item.source_account_key,
                item.target_account_key,
            )

            grouped.setdefault(pair_key, []).append(item)

        return grouped

    def _conflicts_by_pair_key(
        self,
        conflicts: list[DetectedConflict],
    ) -> dict[str, list[DetectedConflict]]:
        grouped: dict[str, list[DetectedConflict]] = {}

        for conflict in conflicts:
            pair_key = self._pair_key(
                conflict.source_account_key,
                conflict.target_account_key,
            )

            grouped.setdefault(pair_key, []).append(conflict)

        return grouped

    def _anchor_account_keys(
        self,
        evidence: list[ExtractedEvidence],
    ) -> list[str]:
        anchors = {
            item.source_account_key
            for item in evidence
            if item.target_type == EvidenceTargetType.REQUEST
            and item.evidence_type.value in ANCHOR_EVIDENCE_TYPES
        }

        return sorted(anchors)

    def _independent_groups(
        self,
        components: list[ScoreComponent],
    ) -> list[str]:
        groups = {
            component.independence_group
            for component in components
            if component.kind == ScoreComponentKind.POSITIVE_EVIDENCE
            and component.independence_group
            and component.applied_weight > 0
        }

        return sorted(groups)

    def _has_hn_metadata(self, items: list[Any]) -> bool:
        for item in items:
            metadata = getattr(item, "metadata", {}) or {}
            if metadata.get("hn_conservative") is True:
                return True

            source = getattr(item, "source", None)
            target_source = getattr(item, "target_source", None)

            if source == PlatformSource.HACKERNEWS or target_source == PlatformSource.HACKERNEWS:
                return True

        return False

    def _account_explanation(
        self,
        *,
        account: SourceAccount,
        positive_score: float,
        score_before_cap: float,
        final_score: float,
        independent_groups: list[str],
        weak_signal_only: bool,
        hn_conservative: bool,
    ) -> list[str]:
        explanation = [
            (
                f"Account {account.expected_source_account_key()} scored "
                f"{final_score:.2f} against the request."
            )
        ]

        if independent_groups:
            explanation.append(
                "Applied independent evidence groups: "
                + ", ".join(independent_groups)
                + "."
            )
        else:
            explanation.append("No direct request evidence was applied.")

        if weak_signal_only:
            explanation.append(
                "Only weak identity signals were applied; classifier should avoid auto-match from this alone."
            )

        if hn_conservative:
            explanation.append(
                "Hacker News evidence is conservative because HN profiles are sparse."
            )

        if score_before_cap > CONFIDENCE_CAP:
            explanation.append(
                f"Positive score exceeded the confidence cap and was capped at {CONFIDENCE_CAP}."
            )

        return explanation

    def _pair_explanation(
        self,
        *,
        left: SourceAccount,
        right: SourceAccount,
        positive_score: float,
        conflict_penalty: float,
        score_before_cap: float,
        final_score: float,
        independent_groups: list[str],
        strong_groups: list[str],
        weak_signal_only: bool,
        hn_conservative: bool,
    ) -> list[str]:
        pair_key = self._pair_key(
            left.expected_source_account_key(),
            right.expected_source_account_key(),
        )

        explanation = [
            f"Account pair {pair_key} scored {final_score:.2f}."
        ]

        if independent_groups:
            explanation.append(
                "Applied independent evidence groups: "
                + ", ".join(independent_groups)
                + "."
            )
        else:
            explanation.append("No positive account-pair evidence was applied.")

        if conflict_penalty < 0:
            explanation.append(
                f"Conflict penalties reduced the score by {abs(conflict_penalty):.2f}."
            )

        if strong_groups:
            explanation.append(
                "Strong connection groups present: "
                + ", ".join(strong_groups)
                + "."
            )

        if weak_signal_only:
            explanation.append(
                "Only weak pair signals were applied; classifier should avoid auto-match from this alone."
            )

        if hn_conservative:
            explanation.append(
                "Pair involves Hacker News or HN-derived evidence; classifier should require stronger corroboration."
            )

        if score_before_cap > CONFIDENCE_CAP:
            explanation.append(
                f"Positive score exceeded the confidence cap and was capped at {CONFIDENCE_CAP}."
            )

        return explanation

    def _pair_key(self, left_key: str, right_key: str) -> str:
        return "::".join(sorted([left_key.lower(), right_key.lower()]))

    def _clamp(self, value: float) -> float:
        return min(CONFIDENCE_CAP, max(CONFIDENCE_FLOOR, value))