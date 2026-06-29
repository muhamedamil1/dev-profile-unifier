from __future__ import annotations

from app.resolution.classifier import DecisionClassifier
from app.schemas.classification import DecisionBasis
from app.schemas.enums import MatchDecision, PlatformSource
from app.schemas.scoring import (
    ConfidenceScore,
    ScoreComponent,
    ScoreComponentKind,
    ScoreTargetType,
    ScoringResult,
)
from app.schemas.source_account import SourceAccount


def account(source: PlatformSource, identifier: str, *, handle: str | None = None) -> SourceAccount:
    return SourceAccount(
        source=source,
        source_user_id=identifier,
        handle=handle or identifier,
        profile_url=f"https://example.com/{source.value}/{identifier}",
    )


def pair_key(left: SourceAccount, right: SourceAccount) -> str:
    return "::".join(sorted([left.expected_source_account_key(), right.expected_source_account_key()]))


def account_score(
    item: SourceAccount,
    *,
    confidence: float = 0.0,
    groups: list[str] | None = None,
) -> ConfidenceScore:
    groups = groups or []
    return ConfidenceScore(
        target_type=ScoreTargetType.ACCOUNT,
        target_key=item.expected_source_account_key(),
        source_account_key=item.expected_source_account_key(),
        source=item.source,
        positive_score=confidence,
        conflict_penalty=0.0,
        score_before_cap=confidence,
        confidence_score=confidence,
        positive_signal_count=len(groups),
        raw_positive_signal_count=len(groups),
        conflict_count=0,
        raw_conflict_count=0,
        independent_positive_groups=groups,
        strong_positive_groups=[group for group in groups if group in {"input_identifier", "email"}],
        weak_positive_groups=[group for group in groups if group in {"handle", "location", "bio", "topics"}],
        weak_signal_only=bool(groups) and not any(group in {"input_identifier", "email"} for group in groups),
        hn_conservative=item.source == PlatformSource.HACKERNEWS,
        hn_requires_strong_evidence=item.source == PlatformSource.HACKERNEWS,
        explanation=[f"account score for {item.expected_source_account_key()}"],
    )


def positive_component(left: SourceAccount, right: SourceAccount, group: str) -> ScoreComponent:
    return ScoreComponent(
        kind=ScoreComponentKind.POSITIVE_EVIDENCE,
        signal_type=f"{group}_match",
        raw_weight=0.3,
        applied_weight=0.3,
        reason=f"{group} test evidence",
        independence_group=group,
        source_account_key=left.expected_source_account_key(),
        target_account_key=right.expected_source_account_key(),
    )


def conflict_component(
    left: SourceAccount,
    right: SourceAccount,
    signal_type: str,
    *,
    severity: str = "medium",
    conflict_basis: str | None = None,
) -> ScoreComponent:
    metadata = {"severity": severity}
    if conflict_basis is not None:
        metadata["conflict_basis"] = conflict_basis

    return ScoreComponent(
        kind=ScoreComponentKind.CONFLICT_PENALTY,
        signal_type=signal_type,
        raw_weight=-0.35,
        applied_weight=-0.35,
        reason=f"{signal_type} test conflict",
        source_account_key=left.expected_source_account_key(),
        target_account_key=right.expected_source_account_key(),
        metadata=metadata,
    )


def pair_score(
    left: SourceAccount,
    right: SourceAccount,
    *,
    confidence: float,
    groups: list[str] | None = None,
    conflicts: list[ScoreComponent] | None = None,
    conflict_penalty: float = 0.0,
    hn_conservative: bool | None = None,
) -> ConfidenceScore:
    groups = groups or []
    conflicts = conflicts or []
    positive_components = [positive_component(left, right, group) for group in groups]
    strong_groups = [group for group in groups if group in {"website", "profile_link", "email"}]
    weak_groups = [group for group in groups if group in {"handle", "location", "bio", "topics"}]
    conservative = (
        left.source == PlatformSource.HACKERNEWS
        or right.source == PlatformSource.HACKERNEWS
        if hn_conservative is None
        else hn_conservative
    )

    return ConfidenceScore(
        target_type=ScoreTargetType.ACCOUNT_PAIR,
        target_key=pair_key(left, right),
        source_account_key=left.expected_source_account_key(),
        source=left.source,
        target_account_key=right.expected_source_account_key(),
        target_source=right.source,
        positive_score=max(confidence - conflict_penalty, 0.0),
        conflict_penalty=conflict_penalty,
        score_before_cap=confidence,
        confidence_score=confidence,
        positive_signal_count=len(positive_components),
        raw_positive_signal_count=len(positive_components),
        conflict_count=len(conflicts),
        raw_conflict_count=len(conflicts),
        independent_positive_groups=groups,
        strong_positive_groups=strong_groups,
        weak_positive_groups=weak_groups,
        weak_signal_only=bool(groups) and not strong_groups,
        hn_conservative=conservative,
        hn_requires_strong_evidence=conservative and bool(groups) and not strong_groups,
        components=[*positive_components, *conflicts],
        explanation=[f"pair score for {pair_key(left, right)}"],
    )


def scoring(
    accounts: list[SourceAccount],
    *,
    anchors: list[SourceAccount] | None = None,
    pairs: list[ConfidenceScore] | None = None,
    account_scores: dict[str, ConfidenceScore] | None = None,
) -> ScoringResult:
    account_scores = account_scores or {}
    return ScoringResult(
        account_scores=[
            account_scores.get(item.expected_source_account_key())
            or account_score(
                item,
                confidence=0.25 if anchors and item in anchors else 0.0,
                groups=["input_identifier"] if anchors and item in anchors else [],
            )
            for item in accounts
        ],
        pair_scores=pairs or [],
        anchor_account_keys=[item.expected_source_account_key() for item in anchors or []],
    )


def classify(accounts: list[SourceAccount], result: ScoringResult):
    return DecisionClassifier().classify(accounts=accounts, scoring_result=result).classification_by_key


def text(item) -> str:
    return " ".join(item.rationale).lower()


def test_anchor_accepted_but_not_verified() -> None:
    github = account(PlatformSource.GITHUB, "amil122", handle="amil122")

    by_key = classify([github], scoring([github], anchors=[github]))
    item = by_key[github.expected_source_account_key()]

    assert item.decision == MatchDecision.AUTO_MATCH
    assert item.decision_basis == DecisionBasis.ANCHOR_INPUT
    assert item.accepted_as_anchor is True
    assert "not external ownership verification" in text(item)
    assert item.metadata["decision_confidence_policy"] == "anchor_floor_applied"
    assert item.metadata["anchor_floor"] == 0.85
    assert item.metadata["evidence_score_before_anchor_policy"] == 0.25


def test_strong_discovered_account_auto_matches_anchor() -> None:
    github = account(PlatformSource.GITHUB, "amil122")
    devto = account(PlatformSource.DEVTO, "amil122")
    result = scoring(
        [github, devto],
        anchors=[github],
        pairs=[pair_score(github, devto, confidence=0.9, groups=["website", "profile_link"])],
    )

    item = classify([github, devto], result)[devto.expected_source_account_key()]

    assert item.decision == MatchDecision.AUTO_MATCH
    assert item.decision_basis == DecisionBasis.STRONG_ANCHOR_PAIR


def test_hn_handle_only_does_not_auto_match() -> None:
    github = account(PlatformSource.GITHUB, "amil122")
    hn = account(PlatformSource.HACKERNEWS, "amil122")
    result = scoring(
        [github, hn],
        anchors=[github],
        pairs=[pair_score(github, hn, confidence=0.12, groups=["handle"])],
    )

    item = classify([github, hn], result)[hn.expected_source_account_key()]

    assert item.decision != MatchDecision.AUTO_MATCH
    assert item.hn_conservative is True
    assert "conservative" in text(item) or "weak" in text(item)


def test_strong_pair_to_one_anchor_blocked_by_conflict_with_another_anchor() -> None:
    github = account(PlatformSource.GITHUB, "amil122")
    stackoverflow = account(PlatformSource.STACKOVERFLOW, "4242")
    devto = account(PlatformSource.DEVTO, "amil122")
    email_hash_conflict = conflict_component(
        stackoverflow,
        devto,
        "email_conflict",
        severity="medium",
        conflict_basis="email_hash",
    )
    result = scoring(
        [github, stackoverflow, devto],
        anchors=[github, stackoverflow],
        pairs=[
            pair_score(github, devto, confidence=0.9, groups=["website", "profile_link"]),
            pair_score(stackoverflow, devto, confidence=0.55, conflicts=[email_hash_conflict], conflict_penalty=-0.35),
        ],
    )

    item = classify([github, stackoverflow, devto], result)[devto.expected_source_account_key()]

    assert item.decision in {MatchDecision.NEEDS_REVIEW, MatchDecision.REJECT}
    assert item.decision != MatchDecision.AUTO_MATCH
    assert "blocking conflict with at least one user-provided anchor" in text(item)


def test_no_anchor_strong_pair_becomes_review_not_auto_match() -> None:
    github = account(PlatformSource.GITHUB, "amil122")
    devto = account(PlatformSource.DEVTO, "amil122")
    result = scoring(
        [github, devto],
        pairs=[pair_score(github, devto, confidence=0.9, groups=["website", "profile_link"])],
    )

    by_key = classify([github, devto], result)

    assert by_key[github.expected_source_account_key()].decision == MatchDecision.NEEDS_REVIEW
    assert by_key[devto.expected_source_account_key()].decision == MatchDecision.NEEDS_REVIEW


def test_no_anchor_weak_pair_rejected() -> None:
    github = account(PlatformSource.GITHUB, "amil122")
    hn = account(PlatformSource.HACKERNEWS, "amil122")
    result = scoring(
        [github, hn],
        pairs=[pair_score(github, hn, confidence=0.12, groups=["handle"])],
    )

    by_key = classify([github, hn], result)

    assert by_key[github.expected_source_account_key()].decision == MatchDecision.REJECT
    assert by_key[hn.expected_source_account_key()].decision == MatchDecision.REJECT


def test_conflict_rejection_prevents_same_handle_false_merge() -> None:
    github = account(PlatformSource.GITHUB, "amil122")
    devto = account(PlatformSource.DEVTO, "amil122")
    conflicts = [
        conflict_component(github, devto, "name_conflict", severity="medium"),
        conflict_component(github, devto, "website_conflict", severity="medium"),
    ]
    result = scoring(
        [github, devto],
        anchors=[github],
        pairs=[pair_score(github, devto, confidence=0.45, groups=["handle"], conflicts=conflicts, conflict_penalty=-0.45)],
    )

    item = classify([github, devto], result)[devto.expected_source_account_key()]

    assert item.decision != MatchDecision.AUTO_MATCH
    assert item.decision in {MatchDecision.NEEDS_REVIEW, MatchDecision.REJECT}


def test_email_domain_conflict_does_not_always_block_auto_match() -> None:
    github = account(PlatformSource.GITHUB, "amil122")
    devto = account(PlatformSource.DEVTO, "amil122")
    domain_conflict = conflict_component(
        github,
        devto,
        "email_conflict",
        severity="medium",
        conflict_basis="email_domain",
    )
    result = scoring(
        [github, devto],
        anchors=[github],
        pairs=[
            pair_score(
                github,
                devto,
                confidence=0.9,
                groups=["website", "profile_link"],
                conflicts=[domain_conflict],
                conflict_penalty=-0.1,
            )
        ],
    )

    item = classify([github, devto], result)[devto.expected_source_account_key()]

    assert item.decision == MatchDecision.AUTO_MATCH
    assert "email_conflict" in item.conflict_types
    assert "email_conflict" not in item.blocking_conflict_types


def test_email_hash_conflict_blocks_auto_match() -> None:
    github = account(PlatformSource.GITHUB, "amil122")
    devto = account(PlatformSource.DEVTO, "amil122")
    hash_conflict = conflict_component(
        github,
        devto,
        "email_conflict",
        severity="medium",
        conflict_basis="email_hash",
    )
    result = scoring(
        [github, devto],
        anchors=[github],
        pairs=[
            pair_score(
                github,
                devto,
                confidence=0.9,
                groups=["website", "profile_link"],
                conflicts=[hash_conflict],
                conflict_penalty=-0.35,
            )
        ],
    )

    item = classify([github, devto], result)[devto.expected_source_account_key()]

    assert item.decision != MatchDecision.AUTO_MATCH
    assert "email_conflict" in item.blocking_conflict_types


def test_classification_is_deterministic_regardless_of_account_order() -> None:
    github = account(PlatformSource.GITHUB, "amil122")
    devto = account(PlatformSource.DEVTO, "amil122")
    hn = account(PlatformSource.HACKERNEWS, "amil122")
    pairs = [
        pair_score(github, devto, confidence=0.9, groups=["website", "profile_link"]),
        pair_score(github, hn, confidence=0.12, groups=["handle"]),
    ]
    first_result = scoring([github, devto, hn], anchors=[github], pairs=pairs)
    second_result = scoring([hn, devto, github], anchors=[github], pairs=pairs)

    first = DecisionClassifier().classify(accounts=[github, devto, hn], scoring_result=first_result)
    second = DecisionClassifier().classify(accounts=[hn, devto, github], scoring_result=second_result)

    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_direct_hn_anchor_mentions_sparse_profile_policy() -> None:
    hn = account(PlatformSource.HACKERNEWS, "amil122")

    item = classify([hn], scoring([hn], anchors=[hn]))[hn.expected_source_account_key()]

    assert item.decision == MatchDecision.AUTO_MATCH
    assert item.accepted_as_anchor is True
    assert item.metadata["hn_conservative"] is True
    assert "hacker news profiles are sparse" in text(item)


def test_multi_anchor_without_corroboration_is_flagged_but_still_anchor() -> None:
    github = account(PlatformSource.GITHUB, "amil122")
    stackoverflow = account(PlatformSource.STACKOVERFLOW, "4242")
    result = scoring([github, stackoverflow], anchors=[github, stackoverflow], pairs=[])

    by_key = classify([github, stackoverflow], result)
    github_item = by_key[github.expected_source_account_key()]

    assert github_item.decision == MatchDecision.AUTO_MATCH
    assert github_item.accepted_as_anchor is True
    assert github_item.metadata["uncorroborated_multi_anchor"] is True
    assert "no corroborating pair evidence" in text(github_item)
