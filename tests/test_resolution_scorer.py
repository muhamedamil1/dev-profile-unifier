from __future__ import annotations

import pytest

from app.resolution.scorer import ResolutionScorer
from app.schemas.conflicts import CONFLICT_PENALTIES, ConflictType, DetectedConflict
from app.schemas.enums import ConflictSeverity, PlatformSource
from app.schemas.evidence import (
    EvidenceIndependenceGroup,
    EvidenceTargetType,
    EvidenceType,
    ExtractedEvidence,
)
from app.schemas.scoring import ScoreComponentKind
from app.schemas.source_account import SourceAccount


@pytest.fixture
def scorer() -> ResolutionScorer:
    return ResolutionScorer()


def account(source: PlatformSource, identifier: str, *, handle: str | None = None) -> SourceAccount:
    return SourceAccount(
        source=source,
        source_user_id=identifier,
        handle=handle or identifier,
        profile_url=f"https://example.com/{source.value}/{identifier}",
    )


def evidence(
    evidence_type: EvidenceType,
    group: EvidenceIndependenceGroup,
    weight: float,
    source_account: SourceAccount,
    *,
    target_type: EvidenceTargetType = EvidenceTargetType.REQUEST,
    target_account: SourceAccount | None = None,
    metadata: dict | None = None,
) -> ExtractedEvidence:
    return ExtractedEvidence(
        evidence_type=evidence_type,
        target_type=target_type,
        source_account_key=source_account.expected_source_account_key(),
        source=source_account.source,
        target_account_key=(
            target_account.expected_source_account_key()
            if target_account is not None
            else None
        ),
        target_source=target_account.source if target_account is not None else None,
        weight=weight,
        independence_group=group,
        reason=f"{evidence_type.value} test evidence",
        metadata=metadata or {},
    )


def conflict(
    conflict_type: ConflictType,
    left: SourceAccount,
    right: SourceAccount,
) -> DetectedConflict:
    severity = (
        ConflictSeverity.HIGH
        if conflict_type == ConflictType.EMAIL_CONFLICT
        else ConflictSeverity.MEDIUM
    )
    return DetectedConflict(
        conflict_type=conflict_type,
        severity=severity,
        penalty=CONFLICT_PENALTIES[conflict_type],
        source_account_key=left.expected_source_account_key(),
        source=left.source,
        target_account_key=right.expected_source_account_key(),
        target_source=right.source,
        description=f"{conflict_type.value} test conflict",
    )


def pair_score(result, left: SourceAccount, right: SourceAccount):
    key = "::".join(
        sorted([
            left.expected_source_account_key(),
            right.expected_source_account_key(),
        ])
    )
    return result.pair_score_by_key[key]


def explanation_text(score) -> str:
    return " ".join(score.explanation).lower()


def test_positive_score_above_one_does_not_fail_validation(
    scorer: ResolutionScorer,
) -> None:
    github = account(PlatformSource.GITHUB, "101", handle="amil122")
    items = [
        evidence(EvidenceType.INPUT_HANDLE_MATCH, EvidenceIndependenceGroup.INPUT_IDENTIFIER, 0.25, github),
        evidence(EvidenceType.EXACT_NAME_MATCH, EvidenceIndependenceGroup.NAME, 0.20, github),
        evidence(EvidenceType.SAME_WEBSITE, EvidenceIndependenceGroup.WEBSITE, 0.30, github),
        evidence(EvidenceType.RECIPROCAL_PROFILE_LINK, EvidenceIndependenceGroup.PROFILE_LINK, 0.45, github),
        evidence(EvidenceType.EMAIL_HINT_MATCH, EvidenceIndependenceGroup.EMAIL, 0.35, github),
        evidence(EvidenceType.TOPIC_OVERLAP, EvidenceIndependenceGroup.TOPICS, 0.12, github),
    ]

    result = scorer.score(accounts=[github], evidence=items, conflicts=[])
    score = result.account_score_by_key[github.expected_source_account_key()]

    assert score.positive_score > 1.0
    assert score.confidence_score == 0.97


def test_profile_link_group_cap_counts_applied_and_raw_signals(
    scorer: ResolutionScorer,
) -> None:
    github = account(PlatformSource.GITHUB, "101")
    devto = account(PlatformSource.DEVTO, "202")
    items = [
        evidence(EvidenceType.DIRECT_PROFILE_LINK, EvidenceIndependenceGroup.PROFILE_LINK, 0.40, github, target_type=EvidenceTargetType.ACCOUNT_PAIR, target_account=devto),
        evidence(EvidenceType.DIRECT_PROFILE_LINK, EvidenceIndependenceGroup.PROFILE_LINK, 0.40, github, target_type=EvidenceTargetType.ACCOUNT_PAIR, target_account=devto),
        evidence(EvidenceType.RECIPROCAL_PROFILE_LINK, EvidenceIndependenceGroup.PROFILE_LINK, 0.45, github, target_type=EvidenceTargetType.ACCOUNT_PAIR, target_account=devto),
    ]

    result = scorer.score(accounts=[github, devto], evidence=items, conflicts=[])
    score = pair_score(result, github, devto)

    assert score.positive_score == 0.45
    assert score.positive_signal_count == 1
    assert score.raw_positive_signal_count == 3
    discarded = [
        component
        for component in score.components
        if component.kind == ScoreComponentKind.GROUP_CAP_DISCARDED
    ]
    assert len(discarded) == 2


def test_topic_overlap_group_cap_is_applied(scorer: ResolutionScorer) -> None:
    github = account(PlatformSource.GITHUB, "101")
    item = evidence(
        EvidenceType.TOPIC_OVERLAP,
        EvidenceIndependenceGroup.TOPICS,
        0.50,
        github,
    )

    result = scorer.score(accounts=[github], evidence=[item], conflicts=[])
    score = result.account_score_by_key[github.expected_source_account_key()]

    assert score.positive_score == 0.12
    assert score.components[0].applied_weight == 0.12


def test_conflicts_reduce_pair_score_and_count_applied_conflicts(
    scorer: ResolutionScorer,
) -> None:
    github = account(PlatformSource.GITHUB, "101")
    devto = account(PlatformSource.DEVTO, "202")
    items = [
        evidence(EvidenceType.SIMILAR_HANDLE, EvidenceIndependenceGroup.HANDLE, 0.12, github, target_type=EvidenceTargetType.ACCOUNT_PAIR, target_account=devto),
    ]
    conflicts = [
        conflict(ConflictType.NAME_CONFLICT, github, devto),
        conflict(ConflictType.WEBSITE_CONFLICT, github, devto),
    ]

    result = scorer.score(accounts=[github, devto], evidence=items, conflicts=conflicts)
    score = pair_score(result, github, devto)

    assert score.confidence_score == 0.0
    assert score.conflict_count == 2
    assert score.raw_conflict_count == 2


def test_hn_weak_pair_is_marked_conservative(scorer: ResolutionScorer) -> None:
    github = account(PlatformSource.GITHUB, "amil122", handle="amil122")
    hn = account(PlatformSource.HACKERNEWS, "amil122", handle="amil122")
    items = [
        evidence(
            EvidenceType.SIMILAR_HANDLE,
            EvidenceIndependenceGroup.HANDLE,
            0.12,
            github,
            target_type=EvidenceTargetType.ACCOUNT_PAIR,
            target_account=hn,
            metadata={"hn_conservative": True, "weak_identity_signal": True},
        ),
    ]

    result = scorer.score(accounts=[github, hn], evidence=items, conflicts=[])
    score = pair_score(result, github, hn)

    assert score.confidence_score == 0.12
    assert score.hn_conservative is True
    assert score.weak_signal_only is True
    assert score.hn_requires_strong_evidence is True


def test_anchor_detection_helper_uses_normalized_account_keys(
    scorer: ResolutionScorer,
) -> None:
    github = account(PlatformSource.GITHUB, "101", handle="amil122")
    devto = account(PlatformSource.DEVTO, "202")
    item = evidence(
        EvidenceType.INPUT_HANDLE_MATCH,
        EvidenceIndependenceGroup.INPUT_IDENTIFIER,
        0.25,
        github,
    )

    result = scorer.score(accounts=[github, devto], evidence=[item], conflicts=[])

    assert github.expected_source_account_key() in result.anchor_account_keys
    assert result.is_anchor(f"  {github.expected_source_account_key().upper()}  ") is True
    assert result.is_anchor(devto.expected_source_account_key()) is False


def test_scoring_is_deterministic_regardless_of_account_order(
    scorer: ResolutionScorer,
) -> None:
    github = account(PlatformSource.GITHUB, "101")
    devto = account(PlatformSource.DEVTO, "202")
    items = [
        evidence(EvidenceType.SAME_WEBSITE, EvidenceIndependenceGroup.WEBSITE, 0.30, github, target_type=EvidenceTargetType.ACCOUNT_PAIR, target_account=devto),
    ]

    first = scorer.score(accounts=[github, devto], evidence=items, conflicts=[])
    second = scorer.score(accounts=[devto, github], evidence=items, conflicts=[])

    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_cap_explanation_uses_score_before_cap(scorer: ResolutionScorer) -> None:
    github = account(PlatformSource.GITHUB, "101")
    devto = account(PlatformSource.DEVTO, "202")
    high_positive_items = [
        evidence(EvidenceType.SAME_WEBSITE, EvidenceIndependenceGroup.WEBSITE, 0.30, github, target_type=EvidenceTargetType.ACCOUNT_PAIR, target_account=devto),
        evidence(EvidenceType.RECIPROCAL_PROFILE_LINK, EvidenceIndependenceGroup.PROFILE_LINK, 0.45, github, target_type=EvidenceTargetType.ACCOUNT_PAIR, target_account=devto),
        evidence(EvidenceType.EMAIL_HINT_MATCH, EvidenceIndependenceGroup.EMAIL, 0.35, github, target_type=EvidenceTargetType.ACCOUNT_PAIR, target_account=devto),
    ]

    capped = scorer.score(accounts=[github, devto], evidence=high_positive_items, conflicts=[])
    capped_score = pair_score(capped, github, devto)
    assert capped_score.score_before_cap > 0.97
    assert "capped" in explanation_text(capped_score)

    reduced = scorer.score(
        accounts=[github, devto],
        evidence=high_positive_items,
        conflicts=[
            conflict(ConflictType.NAME_CONFLICT, github, devto),
            conflict(ConflictType.WEBSITE_CONFLICT, github, devto),
        ],
    )
    reduced_score = pair_score(reduced, github, devto)

    assert reduced_score.positive_score > 0.97
    assert reduced_score.score_before_cap < 0.97
    assert "capped" not in explanation_text(reduced_score)