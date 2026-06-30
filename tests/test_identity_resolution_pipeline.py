from __future__ import annotations

import inspect
import os
from uuid import UUID, uuid4

import pytest

from app.resolution.classifier import DecisionClassifier
from app.resolution.conflict_detector import ConflictDetector
from app.resolution.evidence import EvidenceExtractor
from app.resolution.scorer import ResolutionScorer
from app.schemas.enums import MatchDecision, PlatformSource
from app.schemas.evidence import EvidenceType
from app.schemas.requests import ProfileResolveRequest
from app.schemas.source_account import SourceAccount
from app.services.canonical_profile_service import CanonicalProfileService


def enum_value(enum_cls, value: str):
    upper_name = value.upper()

    if hasattr(enum_cls, upper_name):
        return getattr(enum_cls, upper_name)

    return enum_cls(value)


def decision_value(value: str):
    return enum_value(MatchDecision, value)


def source_value(value: str):
    return enum_value(PlatformSource, value)


def status_value(value):
    return value.value if hasattr(value, "value") else str(value)


def account_key(account: SourceAccount) -> str:
    return account.expected_source_account_key()


def make_source_account(
    *,
    source: str,
    source_user_id: str,
    handle: str,
    display_name: str | None = None,
    bio: str | None = None,
    location: str | None = None,
    website_url: str | None = None,
    profile_url: str | None = None,
    avatar_url: str | None = None,
    topics: list[str] | None = None,
    outbound_links: list[str] | None = None,
    activity_payload: dict | None = None,
) -> SourceAccount:
    return SourceAccount(
        id=uuid4(),
        source=source_value(source),
        source_user_id=source_user_id,
        handle=handle,
        display_name=display_name,
        bio=bio,
        location=location,
        website_url=website_url,
        profile_url=profile_url,
        avatar_url=avatar_url,
        topics=topics or [],
        outbound_links=outbound_links or [],
        activity_payload=activity_payload or {},
        raw_source_record_id=uuid4(),
    )


def source_account_to_row(account: SourceAccount) -> dict:
    payload = account.model_dump(mode="json")
    payload["source"] = account.source.value if hasattr(account.source, "value") else account.source
    return payload


def run_phase7_pipeline(
    *,
    request: ProfileResolveRequest,
    accounts: list[SourceAccount],
):
    evidence_result = EvidenceExtractor().extract(
        request=request,
        accounts=accounts,
    )

    conflict_result = ConflictDetector().detect(
        accounts=accounts,
    )

    scoring_result = ResolutionScorer().score(
        accounts=accounts,
        evidence=evidence_result.evidence,
        conflicts=conflict_result.conflicts,
    )

    classification_result = DecisionClassifier().classify(
        accounts=accounts,
        scoring_result=scoring_result,
        request=request,
    )

    return evidence_result, conflict_result, scoring_result, classification_result


class FakeProfilesRepo:
    def __init__(
        self,
        *,
        profile: dict,
        links: list[dict],
    ) -> None:
        self.profile = dict(profile)
        self.links = [dict(link) for link in links]
        self.update_calls: list[dict] = []

    def get_by_id(self, profile_id):
        if str(self.profile["id"]) == str(profile_id):
            return dict(self.profile)

        return None

    def get_by_resolution_run_id(self, resolution_run_id):
        if str(self.profile["resolution_run_id"]) == str(resolution_run_id):
            return dict(self.profile)

        return None

    def list_source_links_for_profile(self, profile_id):
        return [
            dict(link)
            for link in self.links
            if str(link["profile_id"]) == str(profile_id)
        ]

    def update_canonical_profile_fields(self, **kwargs):
        profile_id = kwargs.pop("profile_id")
        assert str(profile_id) == str(self.profile["id"])

        self.profile.update(kwargs)
        self.update_calls.append(dict(kwargs))

        return dict(self.profile)


class FakeSourceAccountsRepo:
    def __init__(self, accounts: list[SourceAccount]) -> None:
        self.accounts = {
            str(account.id): source_account_to_row(account)
            for account in accounts
        }

    def list_by_ids(self, source_account_ids):
        return [
            dict(self.accounts[str(source_account_id)])
            for source_account_id in source_account_ids
            if str(source_account_id) in self.accounts
        ]


class FakeResolutionRunsRepo:
    def __init__(self) -> None:
        self.summary: dict = {}
        self.patches: list[dict] = []

    def merge_result_summary(self, *, resolution_run_id, patch: dict):
        self.patches.append(
            {
                "resolution_run_id": str(resolution_run_id),
                "patch": dict(patch),
            }
        )
        self.summary = {**self.summary, **dict(patch)}
        return {"id": str(resolution_run_id), "result_summary": dict(self.summary)}


def make_canonical_service(
    *,
    profiles_repo: FakeProfilesRepo,
    source_accounts_repo: FakeSourceAccountsRepo,
    resolution_runs_repo: FakeResolutionRunsRepo | None = None,
) -> CanonicalProfileService:
    signature = inspect.signature(CanonicalProfileService)

    kwargs = {
        "profiles_repo": profiles_repo,
        "source_accounts_repo": source_accounts_repo,
    }

    if "resolution_runs_repo" in signature.parameters:
        kwargs["resolution_runs_repo"] = resolution_runs_repo or FakeResolutionRunsRepo()

    return CanonicalProfileService(**kwargs)


def make_link(
    *,
    profile_id: UUID,
    account: SourceAccount,
    decision: str,
    relationship_type: str = "supporting",
    confidence_score: float = 0.85,
    evidence_confidence_score: float = 0.85,
    is_anchor: bool = False,
    rationale: list[str] | None = None,
) -> dict:
    return {
        "id": str(uuid4()),
        "profile_id": str(profile_id),
        "source_account_id": str(account.id),
        "confidence_score": confidence_score,
        "decision": decision,
        "relationship_type": relationship_type,
        "verification_status": "unverified",
        "positive_signal_count": 2,
        "negative_signal_count": 0,
        "has_high_conflict": False,
        "decision_payload": {
            "decision_basis": "anchor_input" if is_anchor else "strong_anchor_pair",
            "risk_level": "low",
            "rationale": rationale or ["Test classification rationale"],
            "evidence_confidence_score": evidence_confidence_score,
            "account_score": evidence_confidence_score,
            "best_pair_score": confidence_score,
            "is_anchor": is_anchor,
            "accepted_as_anchor": is_anchor,
            "independent_positive_groups": ["website", "profile_link"],
            "strong_positive_groups": ["website", "profile_link"],
            "weak_positive_groups": [],
            "weak_signal_only": False,
            "hn_conservative": account.source == source_value("hackernews"),
            "conflict_types": [],
            "blocking_conflict_types": [],
        },
        "created_at": "2026-01-01T00:00:00+00:00",
    }


def test_phase7_direct_github_devto_anchors_with_reciprocal_links_auto_match():
    request = ProfileResolveRequest(
        name="Ben Halpern",
        github="benhalpern",
        devto="ben",
    )

    github = make_source_account(
        source="github",
        source_user_id="583231",
        handle="benhalpern",
        display_name="Ben Halpern",
        website_url="https://benhalpern.com",
        profile_url="https://github.com/benhalpern",
        outbound_links=["https://dev.to/ben"],
    )

    devto = make_source_account(
        source="devto",
        source_user_id="1",
        handle="ben",
        display_name="Ben Halpern",
        website_url="https://forem.com",
        profile_url="https://dev.to/ben",
        outbound_links=["https://github.com/benhalpern"],
    )

    evidence_result, conflict_result, _scoring_result, classification_result = run_phase7_pipeline(
        request=request,
        accounts=[github, devto],
    )

    evidence_types = {item.evidence_type for item in evidence_result.evidence}
    classifications = classification_result.classification_by_key

    assert EvidenceType.DIRECT_PROFILE_LINK in evidence_types
    assert EvidenceType.RECIPROCAL_PROFILE_LINK in evidence_types
    assert conflict_result.count <= 1

    for account in [github, devto]:
        item = classifications[account_key(account)]
        assert item.decision == decision_value("auto_match")
        assert item.decision_confidence_score >= 0.85
        assert "conflicts with another directly provided anchor" not in " ".join(item.rationale).lower()


def test_phase7_weak_website_mismatch_alone_does_not_demote_direct_anchors():
    request = ProfileResolveRequest(
        name="Ben Halpern",
        github="benhalpern",
        devto="ben",
    )

    github = make_source_account(
        source="github",
        source_user_id="583231",
        handle="benhalpern",
        display_name="Ben Halpern",
        website_url="https://benhalpern.com",
        profile_url="https://github.com/benhalpern",
    )

    devto = make_source_account(
        source="devto",
        source_user_id="1",
        handle="ben",
        display_name="Ben Halpern",
        website_url="https://forem.com",
        profile_url="https://dev.to/ben",
    )

    _evidence_result, conflict_result, scoring_result, classification_result = run_phase7_pipeline(
        request=request,
        accounts=[github, devto],
    )

    pair_score = scoring_result.pair_scores[0]
    classifications = classification_result.classification_by_key

    assert conflict_result.by_type.get("website_conflict", 0) == 1
    assert conflict_result.conflicts[0].severity.value == "low"
    assert conflict_result.conflicts[0].metadata["weak_identity_signal"] is True
    assert pair_score.conflict_count == 1

    for account in [github, devto]:
        item = classifications[account_key(account)]
        assert item.decision == decision_value("auto_match")
        assert item.decision_confidence_score >= 0.85
        assert item.evidence_confidence_score == 0.45
        assert item.blocking_conflict_types == []
        assert "conflicts with another directly provided anchor" not in " ".join(item.rationale).lower()


def test_phase7_multiple_legitimate_websites_do_not_demote_direct_anchors():
    request = ProfileResolveRequest(
        name="Ben Halpern",
        github="benhalpern",
        devto="ben",
    )

    github = make_source_account(
        source="github",
        source_user_id="583231",
        handle="benhalpern",
        display_name="Ben Halpern",
        website_url="https://dev.to/ben",
        profile_url="https://github.com/benhalpern",
    )

    devto = make_source_account(
        source="devto",
        source_user_id="1",
        handle="ben",
        display_name="Ben Halpern",
        website_url="https://benhalpern.com",
        profile_url="https://dev.to/ben",
    )

    _evidence_result, conflict_result, _scoring_result, classification_result = run_phase7_pipeline(
        request=request,
        accounts=[github, devto],
    )

    classifications = classification_result.classification_by_key

    assert conflict_result.by_type.get("website_conflict", 0) == 0
    assert classifications[account_key(github)].decision == decision_value("auto_match")
    assert classifications[account_key(devto)].decision == decision_value("auto_match")
    assert classifications[account_key(github)].blocking_conflict_types == []
    assert classifications[account_key(devto)].blocking_conflict_types == []


def test_phase7_strong_name_conflict_between_direct_anchors_blocks_auto_match():
    request = ProfileResolveRequest(
        name="Jon Skeet",
        github="simonw",
        stackoverflow_user_id="22656",
    )

    github = make_source_account(
        source="github",
        source_user_id="101",
        handle="simonw",
        display_name="Simon Willison",
        profile_url="https://github.com/simonw",
    )

    stackoverflow = make_source_account(
        source="stackoverflow",
        source_user_id="22656",
        handle="22656",
        display_name="Jon Skeet",
        profile_url="https://stackoverflow.com/users/22656/jon-skeet",
    )

    _evidence_result, conflict_result, _scoring_result, classification_result = run_phase7_pipeline(
        request=request,
        accounts=[github, stackoverflow],
    )

    classifications = classification_result.classification_by_key

    assert conflict_result.by_type.get("name_conflict", 0) == 1
    assert classifications[account_key(github)].decision == decision_value("needs_review")
    assert classifications[account_key(stackoverflow)].decision == decision_value("auto_match")
    assert "target_name_conflict" in classifications[account_key(github)].blocking_conflict_types
    assert classifications[account_key(stackoverflow)].blocking_conflict_types == []



def test_phase7_direct_anchor_conflicting_with_requested_name_is_not_auto_match():
    request = ProfileResolveRequest(
        name="Linus Torvalds",
        github="simonw",
    )

    github = make_source_account(
        source="github",
        source_user_id="101",
        handle="simonw",
        display_name="Simon Willison",
        profile_url="https://github.com/simonw",
    )

    _evidence_result, _conflict_result, _scoring_result, classification_result = run_phase7_pipeline(
        request=request,
        accounts=[github],
    )

    classification = classification_result.classification_by_key[account_key(github)]

    assert classification.decision == decision_value("needs_review")
    assert classification.accepted_as_anchor is False
    assert "target_name_conflict" in classification.blocking_conflict_types
    assert classification.metadata["target_identity_gate"] == "failed_name_conflict"


def test_phase7_sparse_hn_similarity_remains_conservative():
    request = ProfileResolveRequest(
        name="Muhammed Amil",
        github="amil122",
    )

    github = make_source_account(
        source="github",
        source_user_id="101",
        handle="amil122",
        display_name="Muhammed Amil",
        profile_url="https://github.com/amil122",
    )

    hackernews = make_source_account(
        source="hackernews",
        source_user_id="amil122",
        handle="amil122",
        display_name="amil122",
        profile_url="https://news.ycombinator.com/user?id=amil122",
    )

    _evidence_result, _conflict_result, _scoring_result, classification_result = run_phase7_pipeline(
        request=request,
        accounts=[github, hackernews],
    )

    hn_classification = classification_result.classification_by_key[account_key(hackernews)]

    assert hn_classification.decision != decision_value("auto_match")
    assert hn_classification.hn_conservative is True
    assert hn_classification.weak_signal_only is True


def test_phase7_pipeline_auto_matches_strong_discovered_account_but_not_hn_handle_only():
    request = ProfileResolveRequest(
        name="Muhammed Amil",
        github="amil122",
    )

    github = make_source_account(
        source="github",
        source_user_id="101",
        handle="amil122",
        display_name="Muhammed Amil",
        bio="AI backend developer building Python and FastAPI systems.",
        website_url="https://amil.dev",
        profile_url="https://github.com/amil122",
        avatar_url="https://avatars.githubusercontent.com/u/101",
        topics=["python", "fastapi", "supabase", "ai"],
        outbound_links=["https://dev.to/muhammedamil"],
    )

    devto = make_source_account(
        source="devto",
        source_user_id="202",
        handle="muhammedamil",
        display_name="Muhammed Amil",
        bio="FastAPI developer writing about backend automation and AI.",
        website_url="https://www.amil.dev/",
        profile_url="https://dev.to/muhammedamil",
        avatar_url="https://dev.to/avatar.png",
        topics=["python", "fastapi", "backend"],
        outbound_links=["https://github.com/amil122"],
    )

    hackernews = make_source_account(
        source="hackernews",
        source_user_id="amil122",
        handle="amil122",
        display_name="amil122",
        profile_url="https://news.ycombinator.com/user?id=amil122",
        topics=["python"],
    )

    evidence_result, conflict_result, scoring_result, classification_result = run_phase7_pipeline(
        request=request,
        accounts=[hackernews, devto, github],
    )

    classifications = classification_result.classification_by_key

    github_key = account_key(github)
    devto_key = account_key(devto)
    hn_key = account_key(hackernews)

    assert evidence_result.count > 0
    assert conflict_result.count == 0
    assert github_key in scoring_result.anchor_account_keys

    assert classifications[github_key].decision == decision_value("auto_match")
    assert classifications[github_key].is_anchor is True

    assert classifications[devto_key].decision == decision_value("auto_match")
    assert classifications[devto_key].best_pair_score is not None
    assert classifications[devto_key].best_pair_score >= 0.85

    assert classifications[hn_key].decision != decision_value("auto_match")
    assert classifications[hn_key].hn_conservative is True
    assert classifications[hn_key].weak_signal_only is True


def test_phase7_conflicts_prevent_false_auto_merge():
    request = ProfileResolveRequest(
        name="Muhammed Amil",
        github="amil122",
    )

    github = make_source_account(
        source="github",
        source_user_id="101",
        handle="amil122",
        display_name="Muhammed Amil",
        location="Bangalore, India",
        website_url="https://amil.dev",
        profile_url="https://github.com/amil122",
        topics=["python", "fastapi", "supabase", "backend", "ai", "automation"],
    )

    wrong_devto = make_source_account(
        source="devto",
        source_user_id="999",
        handle="amil122",
        display_name="David Lee",
        location="Toronto, Canada",
        website_url="https://davidlee.dev",
        profile_url="https://dev.to/amil122",
        topics=["react", "css", "frontend", "vue", "design", "animation"],
    )

    _evidence_result, conflict_result, _scoring_result, classification_result = run_phase7_pipeline(
        request=request,
        accounts=[github, wrong_devto],
    )

    wrong_key = account_key(wrong_devto)
    wrong_classification = classification_result.classification_by_key[wrong_key]

    assert conflict_result.count > 0
    assert wrong_classification.decision != decision_value("auto_match")
    assert wrong_classification.conflict_types


def test_phase8_builds_canonical_profile_from_auto_match_accounts_only():
    profile_id = uuid4()
    run_id = uuid4()

    github = make_source_account(
        source="github",
        source_user_id="101",
        handle="amil122",
        display_name="Muhammed Amil",
        bio="AI backend developer building Python and FastAPI systems.",
        location="Bangalore, India",
        website_url="https://amil.dev",
        profile_url="https://github.com/amil122",
        avatar_url="https://avatars.githubusercontent.com/u/101",
        topics=["python", "fastapi", "supabase", "ai"],
        activity_payload={"top_languages": ["Python"], "repo_topics": ["FastAPI", "Supabase"]},
    )

    devto = make_source_account(
        source="devto",
        source_user_id="202",
        handle="muhammedamil",
        display_name="Muhammed Amil",
        bio="FastAPI developer writing about backend automation and AI.",
        location="Bangalore, India",
        website_url="https://www.amil.dev/",
        profile_url="https://dev.to/muhammedamil",
        avatar_url="https://dev.to/avatar.png",
        topics=["python", "fastapi", "backend"],
        activity_payload={"article_tags": "python, fastapi, automation"},
    )

    hackernews_review = make_source_account(
        source="hackernews",
        source_user_id="amil122",
        handle="amil122",
        display_name="amil122",
        profile_url="https://news.ycombinator.com/user?id=amil122",
        topics=["startups"],
    )

    rejected_devto = make_source_account(
        source="devto",
        source_user_id="999",
        handle="fake-polished",
        display_name="Perfect Looking Fake Name",
        bio="This polished fake profile must never contribute to canonical fields.",
        website_url="https://fake.dev",
        profile_url="https://dev.to/fake-polished",
        avatar_url="https://fake.dev/avatar.png",
        topics=["fake", "polished"],
    )

    profile = {
        "id": str(profile_id),
        "resolution_run_id": str(run_id),
        "display_name": "Muhammed Amil",
        "headline": None,
        "location": None,
        "bio": None,
        "primary_avatar_url": None,
        "primary_website_url": None,
        "inferred_skills": [],
        "confidence_level": "medium",
        "profile_payload": {
            "profile_stage": "resolution_shell",
            "resolution_summary": {"phase": "7E", "auto_match_count": 2},
            "max_evidence_confidence_score": 0.95,
            "max_decision_confidence_score": 0.95,
        },
    }

    links = [
        make_link(
            profile_id=profile_id,
            account=github,
            decision="auto_match",
            relationship_type="primary",
            confidence_score=0.95,
            evidence_confidence_score=0.95,
            is_anchor=True,
        ),
        make_link(
            profile_id=profile_id,
            account=devto,
            decision="auto_match",
            relationship_type="supporting",
            confidence_score=0.95,
            evidence_confidence_score=0.95,
        ),
        make_link(
            profile_id=profile_id,
            account=hackernews_review,
            decision="needs_review",
            relationship_type="ambiguous",
            confidence_score=0.12,
            evidence_confidence_score=0.12,
            rationale=["HN handle-only candidate requires review."],
        ),
        make_link(
            profile_id=profile_id,
            account=rejected_devto,
            decision="reject",
            relationship_type="rejected",
            confidence_score=0.10,
            evidence_confidence_score=0.10,
            rationale=["Rejected conflicting account."],
        ),
    ]

    runs_repo = FakeResolutionRunsRepo()
    profiles_repo = FakeProfilesRepo(profile=profile, links=links)
    source_accounts_repo = FakeSourceAccountsRepo(
        accounts=[github, devto, hackernews_review, rejected_devto]
    )

    service = make_canonical_service(
        profiles_repo=profiles_repo,
        source_accounts_repo=source_accounts_repo,
        resolution_runs_repo=runs_repo,
    )

    result = service.build_by_profile_id(profile_id=profile_id)

    assert status_value(result.status) == "built"
    assert result.updated is True

    assert result.display_name == "Muhammed Amil"
    assert result.primary_website_url == "https://amil.dev"
    assert result.primary_avatar_url == github.avatar_url
    assert result.location == "Bangalore, India"
    assert result.confidence_level == "high"

    assert "Python" in result.inferred_skills
    assert "FastAPI" in result.inferred_skills
    assert "Supabase" in result.inferred_skills

    platform_keys = {item.source_account_key for item in result.platform_profiles}
    assert account_key(github) in platform_keys
    assert account_key(devto) in platform_keys
    assert account_key(hackernews_review) not in platform_keys
    assert account_key(rejected_devto) not in platform_keys

    review_keys = {item.source_account_key for item in result.review_candidates}
    rejected_keys = {item.source_account_key for item in result.rejected_candidates}

    assert account_key(hackernews_review) in review_keys
    assert account_key(rejected_devto) in rejected_keys

    updated_payload = profiles_repo.profile["profile_payload"]
    assert updated_payload["profile_stage"] == "deterministic_built"
    assert updated_payload["phase"] == 8
    assert updated_payload["canonical_fields_pending"] is False
    assert updated_payload["resolution_summary"] == {"phase": "7E", "auto_match_count": 2}
    assert "field_sources" in updated_payload
    assert "platform_profiles" in updated_payload
    assert "review_candidates" in updated_payload
    assert "rejected_candidates" in updated_payload

    if runs_repo.patches:
        assert runs_repo.patches[-1]["patch"]["canonical_profile_built"] is True
        assert runs_repo.patches[-1]["patch"]["canonical_profile_stage"] == "deterministic_built"


def test_phase8_blocks_and_clears_fields_when_no_auto_match_accounts():
    profile_id = uuid4()
    run_id = uuid4()

    hn_review = make_source_account(
        source="hackernews",
        source_user_id="amil122",
        handle="amil122",
        display_name="amil122",
        profile_url="https://news.ycombinator.com/user?id=amil122",
    )

    profile = {
        "id": str(profile_id),
        "resolution_run_id": str(run_id),
        "display_name": "Muhammed Amil",
        "headline": "Old headline",
        "location": "Old location",
        "bio": "Old bio",
        "primary_avatar_url": "https://old.example/avatar.png",
        "primary_website_url": "https://old.example",
        "inferred_skills": ["OldSkill"],
        "confidence_level": "medium",
        "profile_payload": {
            "profile_stage": "resolution_shell",
            "resolution_summary": {"phase": "7E", "needs_review_count": 1},
        },
    }

    links = [
        make_link(
            profile_id=profile_id,
            account=hn_review,
            decision="needs_review",
            relationship_type="ambiguous",
            confidence_score=0.12,
            evidence_confidence_score=0.12,
        )
    ]

    runs_repo = FakeResolutionRunsRepo()
    profiles_repo = FakeProfilesRepo(profile=profile, links=links)
    source_accounts_repo = FakeSourceAccountsRepo(accounts=[hn_review])

    service = make_canonical_service(
        profiles_repo=profiles_repo,
        source_accounts_repo=source_accounts_repo,
        resolution_runs_repo=runs_repo,
    )

    result = service.build_by_profile_id(profile_id=profile_id)

    assert status_value(result.status) == "blocked_no_auto_match"
    assert result.updated is True
    assert result.display_name is None
    assert result.headline is None
    assert result.bio is None
    assert result.primary_website_url is None
    assert result.primary_avatar_url is None
    assert result.inferred_skills == []
    assert result.confidence_level == "uncertain"

    updated = profiles_repo.profile
    assert updated["display_name"] is None
    assert updated["headline"] is None
    assert updated["bio"] is None
    assert updated["primary_website_url"] is None
    assert updated["primary_avatar_url"] is None
    assert updated["inferred_skills"] == []
    assert updated["confidence_level"] == "uncertain"

    payload = updated["profile_payload"]
    assert payload["profile_stage"] == "canonical_build_blocked"
    assert payload["canonical_fields_pending"] is True
    assert payload["blocked_reason"] == "no_auto_match_accounts"

    if runs_repo.patches:
        assert runs_repo.patches[-1]["patch"]["canonical_profile_built"] is False
        assert runs_repo.patches[-1]["patch"]["canonical_build_blocked_reason"] == "no_auto_match_accounts"


def test_phase8_hn_only_anchor_does_not_become_high_confidence():
    profile_id = uuid4()
    run_id = uuid4()

    hn = make_source_account(
        source="hackernews",
        source_user_id="amil122",
        handle="amil122",
        display_name="amil122",
        profile_url="https://news.ycombinator.com/user?id=amil122",
        topics=["python"],
    )

    profile = {
        "id": str(profile_id),
        "resolution_run_id": str(run_id),
        "display_name": "Muhammed Amil",
        "headline": None,
        "location": None,
        "bio": None,
        "primary_avatar_url": None,
        "primary_website_url": None,
        "inferred_skills": [],
        "confidence_level": "medium",
        "profile_payload": {
            "profile_stage": "resolution_shell",
            "resolution_summary": {"phase": "7E", "auto_match_count": 1},
        },
    }

    links = [
        make_link(
            profile_id=profile_id,
            account=hn,
            decision="auto_match",
            relationship_type="primary",
            confidence_score=0.85,
            evidence_confidence_score=0.25,
            is_anchor=True,
        )
    ]

    service = make_canonical_service(
        profiles_repo=FakeProfilesRepo(profile=profile, links=links),
        source_accounts_repo=FakeSourceAccountsRepo(accounts=[hn]),
        resolution_runs_repo=FakeResolutionRunsRepo(),
    )

    result = service.build_by_profile_id(profile_id=profile_id)

    assert status_value(result.status) == "built"
    assert result.display_name in {"amil122", "Muhammed Amil"}
    assert result.confidence_level != "high"
    assert result.primary_website_url is None
    assert result.primary_avatar_url is None


def test_phase8_generic_subdomain_website_loses_to_real_personal_domain():
    profile_id = uuid4()
    run_id = uuid4()

    github = make_source_account(
        source="github",
        source_user_id="101",
        handle="amil122",
        display_name="Muhammed Amil",
        website_url="https://amil.substack.com",
        profile_url="https://github.com/amil122",
        topics=["python"],
    )

    devto = make_source_account(
        source="devto",
        source_user_id="202",
        handle="muhammedamil",
        display_name="Muhammed Amil",
        website_url="https://amil.dev",
        profile_url="https://dev.to/muhammedamil",
        topics=["fastapi"],
    )

    profile = {
        "id": str(profile_id),
        "resolution_run_id": str(run_id),
        "display_name": "Muhammed Amil",
        "headline": None,
        "location": None,
        "bio": None,
        "primary_avatar_url": None,
        "primary_website_url": None,
        "inferred_skills": [],
        "confidence_level": "medium",
        "profile_payload": {"profile_stage": "resolution_shell"},
    }

    links = [
        make_link(
            profile_id=profile_id,
            account=github,
            decision="auto_match",
            relationship_type="primary",
            confidence_score=0.95,
            evidence_confidence_score=0.95,
            is_anchor=True,
        ),
        make_link(
            profile_id=profile_id,
            account=devto,
            decision="auto_match",
            relationship_type="supporting",
            confidence_score=0.95,
            evidence_confidence_score=0.95,
        ),
    ]

    service = make_canonical_service(
        profiles_repo=FakeProfilesRepo(profile=profile, links=links),
        source_accounts_repo=FakeSourceAccountsRepo(accounts=[github, devto]),
        resolution_runs_repo=FakeResolutionRunsRepo(),
    )

    result = service.build_by_profile_id(profile_id=profile_id)

    assert result.primary_website_url == "https://amil.dev"


def test_phase8_build_is_idempotent_for_same_profile():
    profile_id = uuid4()
    run_id = uuid4()

    github = make_source_account(
        source="github",
        source_user_id="101",
        handle="amil122",
        display_name="Muhammed Amil",
        bio="Python backend developer building FastAPI systems.",
        website_url="https://amil.dev",
        profile_url="https://github.com/amil122",
        topics=["python", "fastapi", "python", "supabase"],
    )

    profile = {
        "id": str(profile_id),
        "resolution_run_id": str(run_id),
        "display_name": "Muhammed Amil",
        "headline": None,
        "location": None,
        "bio": None,
        "primary_avatar_url": None,
        "primary_website_url": None,
        "inferred_skills": [],
        "confidence_level": "medium",
        "profile_payload": {"profile_stage": "resolution_shell"},
    }

    links = [
        make_link(
            profile_id=profile_id,
            account=github,
            decision="auto_match",
            relationship_type="primary",
            confidence_score=0.95,
            evidence_confidence_score=0.95,
            is_anchor=True,
        )
    ]

    profiles_repo = FakeProfilesRepo(profile=profile, links=links)
    service = make_canonical_service(
        profiles_repo=profiles_repo,
        source_accounts_repo=FakeSourceAccountsRepo(accounts=[github]),
        resolution_runs_repo=FakeResolutionRunsRepo(),
    )

    first = service.build_by_profile_id(profile_id=profile_id)
    second = service.build_by_profile_id(profile_id=profile_id)

    assert first.canonical_profile_id == second.canonical_profile_id
    assert first.inferred_skills == second.inferred_skills
    assert len(first.platform_profiles) == len(second.platform_profiles)
    assert len(first.review_candidates) == len(second.review_candidates)
    assert len(first.rejected_candidates) == len(second.rejected_candidates)

    assert profiles_repo.profile["inferred_skills"].count("Python") == 1
    assert profiles_repo.profile["profile_payload"]["profile_stage"] == "deterministic_built"


@pytest.mark.skipif(
    not os.getenv("DEV_PROFILE_REAL_CANONICAL_PROFILE_ID"),
    reason="Set DEV_PROFILE_REAL_CANONICAL_PROFILE_ID to run live Phase 8 smoke test.",
)
def test_live_phase8_smoke_with_real_profile_id():
    from app.dependencies import get_canonical_profile_service

    profile_id = os.environ["DEV_PROFILE_REAL_CANONICAL_PROFILE_ID"]

    service = get_canonical_profile_service()
    result = service.build_by_profile_id(profile_id=profile_id)

    assert status_value(result.status) in {"built", "blocked_no_auto_match"}
    assert result.updated is True

    if status_value(result.status) == "built":
        assert result.platform_profiles
        assert result.profile_payload["profile_stage"] == "deterministic_built"
        assert result.profile_payload["canonical_fields_pending"] is False

    if status_value(result.status) == "blocked_no_auto_match":
        assert result.confidence_level == "uncertain"
        assert result.profile_payload["profile_stage"] == "canonical_build_blocked"
        assert result.profile_payload["canonical_fields_pending"] is True
