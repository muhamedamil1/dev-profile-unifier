from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

from app.schemas.canonical_profile import CanonicalBuildStatus
from app.services.canonical_profile_service import CanonicalProfileService


class FakeProfilesRepo:
    def __init__(self, profile: dict, links: list[dict]) -> None:
        self.profile = deepcopy(profile)
        self.links = deepcopy(links)
        self.updates: list[dict] = []

    def get_by_id(self, profile_id):
        if str(profile_id) == str(self.profile["id"]):
            return deepcopy(self.profile)
        return None

    def get_by_resolution_run_id(self, resolution_run_id):
        if str(resolution_run_id) == str(self.profile.get("resolution_run_id")):
            return deepcopy(self.profile)
        return None

    def list_source_links_for_profile(self, profile_id):
        assert str(profile_id) == str(self.profile["id"])
        return deepcopy(self.links)

    def update_canonical_profile_fields(self, **payload):
        profile_id = payload.pop("profile_id")
        assert str(profile_id) == str(self.profile["id"])
        self.profile.update(deepcopy(payload))
        self.updates.append(deepcopy(payload))
        return deepcopy(self.profile)


class FakeSourceAccountsRepo:
    def __init__(self, accounts: list[dict]) -> None:
        self.accounts = {str(account["id"]): deepcopy(account) for account in accounts}

    def list_by_ids(self, account_ids):
        return [deepcopy(self.accounts[str(account_id)]) for account_id in account_ids if str(account_id) in self.accounts]


class FakeResolutionRunsRepo:
    def __init__(self, summary: dict | None = None) -> None:
        self.summary = deepcopy(summary or {})
        self.patches: list[dict] = []

    def merge_result_summary(self, *, resolution_run_id, patch):
        self.patches.append({"resolution_run_id": str(resolution_run_id), "patch": deepcopy(patch)})
        self.summary = {**self.summary, **deepcopy(patch)}
        return {"id": str(resolution_run_id), "result_summary": deepcopy(self.summary)}


def _profile(*, display_name="Shell Name", payload=None):
    return {
        "id": str(uuid4()),
        "resolution_run_id": str(uuid4()),
        "display_name": display_name,
        "headline": None,
        "location": None,
        "bio": None,
        "primary_avatar_url": None,
        "primary_website_url": None,
        "inferred_skills": [],
        "confidence_level": "low",
        "profile_payload": payload
        or {
            "profile_stage": "resolution_shell",
            "phase": "7E",
            "resolution_summary": {"canonical_profile_pending": True},
            "max_evidence_confidence_score": 0.91,
            "max_decision_confidence_score": 0.93,
        },
    }


def _account(
    *,
    source,
    handle,
    display_name=None,
    bio=None,
    website_url=None,
    avatar_url=None,
    topics=None,
    activity_payload=None,
):
    account_id = str(uuid4())
    return {
        "id": account_id,
        "source": source,
        "source_user_id": handle,
        "handle": handle,
        "source_account_key": f"{source}:{handle}".lower(),
        "display_name": display_name if display_name is not None else handle,
        "bio": bio,
        "location": None,
        "website_url": website_url,
        "profile_url": f"https://example.com/{source}/{handle}",
        "avatar_url": avatar_url,
        "company": None,
        "topics": topics or [],
        "outbound_links": [],
        "activity_payload": activity_payload or {},
    }


def _link(account, *, decision="auto_match", confidence=0.90, evidence=0.80, relationship="secondary"):
    return {
        "id": str(uuid4()),
        "profile_id": None,
        "source_account_id": account["id"],
        "confidence_score": confidence,
        "decision": decision,
        "relationship_type": relationship,
        "decision_payload": {
            "evidence_confidence_score": evidence,
            "decision_confidence_score": confidence,
            "is_anchor": relationship == "primary",
            "rationale": ["test rationale"],
        },
    }


def _service(profile, accounts, links, *, run_summary=None):
    for link in links:
        link["profile_id"] = profile["id"]
    runs_repo = FakeResolutionRunsRepo(run_summary)
    service = CanonicalProfileService(
        profiles_repo=FakeProfilesRepo(profile, links),
        source_accounts_repo=FakeSourceAccountsRepo(accounts),
        resolution_runs_repo=runs_repo,
    )
    return service, service.profiles_repo, runs_repo


def test_accepted_accounts_build_canonical_fields():
    profile = _profile()
    github = _account(
        source="github",
        handle="amil",
        display_name="Muhammed Amil",
        bio="Backend developer building Python, FastAPI, and Supabase systems.",
        website_url="https://amil.dev",
        avatar_url="https://avatars.githubusercontent.com/u/1",
        topics=["Python", "FastAPI"],
        activity_payload={"top_languages": {"Python": 10, "TypeScript": 4}},
    )
    devto = _account(
        source="devto",
        handle="amildev",
        display_name="Muhammed Amil",
        bio="I write about Python APIs and Supabase-backed developer tools.",
        website_url="amil.dev",
        topics=["fastapi", "supabase"],
        activity_payload={"article_tags": "python, fastapi; backend"},
    )
    service, _profiles_repo, runs_repo = _service(
        profile,
        [github, devto],
        [_link(github, relationship="primary"), _link(devto)],
        run_summary={"existing": True},
    )

    result = service.build_by_profile_id(profile_id=profile["id"])

    assert result.status == CanonicalBuildStatus.BUILT
    assert result.display_name == "Muhammed Amil"
    assert result.primary_website_url == "https://amil.dev"
    assert len(result.inferred_skills) == len(set(result.inferred_skills))
    assert "Python" in result.inferred_skills
    assert result.profile_payload["profile_stage"] == "deterministic_built"
    assert result.profile_payload["phase"] == 8
    assert result.profile_payload["canonical_fields_pending"] is False
    assert result.profile_payload["resolution_summary"] == {"canonical_profile_pending": True}
    assert runs_repo.summary["existing"] is True
    assert runs_repo.summary["canonical_profile_built"] is True


def test_needs_review_excluded_from_canonical_fields():
    profile = _profile()
    github = _account(source="github", handle="amil", display_name="Muhammed Amil", topics=["python"])
    hn = _account(source="hackernews", handle="amil122", display_name="amil122", topics=["rust"])
    service, _profiles_repo, _runs_repo = _service(
        profile,
        [github, hn],
        [_link(github, relationship="primary"), _link(hn, decision="needs_review", confidence=0.95, evidence=0.95, relationship="possible_alias")],
    )

    result = service.build_by_profile_id(profile_id=profile["id"])

    assert result.display_name == "Muhammed Amil"
    assert {item.source for item in result.platform_profiles} == {"github"}
    assert [item.source for item in result.review_candidates] == ["hackernews"]
    assert all(item.source != "hackernews" for item in result.platform_profiles)


def test_rejected_excluded_even_if_better_looking():
    profile = _profile()
    github = _account(source="github", handle="amil", display_name="Muhammed Amil", topics=["python"])
    devto = _account(
        source="devto",
        handle="polished",
        display_name="Muhammed Amil Khan",
        bio="Principal engineer with a polished biography and impressive public writing.",
        website_url="https://perfect.dev",
        avatar_url="https://cdn.example.com/perfect.png",
        topics=["fastapi", "supabase"],
    )
    service, _profiles_repo, _runs_repo = _service(
        profile,
        [github, devto],
        [_link(github, relationship="primary"), _link(devto, decision="reject", confidence=0.99, evidence=0.99, relationship="rejected")],
    )

    result = service.build_by_profile_id(profile_id=profile["id"])

    assert result.display_name == "Muhammed Amil"
    assert result.bio is None
    assert result.primary_website_url is None
    assert result.primary_avatar_url is None
    assert result.inferred_skills == ["Python"]
    assert [item.source for item in result.rejected_candidates] == ["devto"]
    assert {item.source for item in result.platform_profiles} == {"github"}


def test_no_auto_match_blocked_clears_shell_fields():
    profile = _profile(display_name="Request Shell Name")
    hn = _account(source="hackernews", handle="amil122", display_name="amil122")
    service, _profiles_repo, runs_repo = _service(
        profile,
        [hn],
        [_link(hn, decision="needs_review", confidence=0.70, evidence=0.40, relationship="possible_alias")],
    )

    result = service.build_by_profile_id(profile_id=profile["id"])

    assert result.status == CanonicalBuildStatus.BLOCKED_NO_AUTO_MATCH
    assert result.display_name is None
    assert result.headline is None
    assert result.confidence_level == "low"
    assert result.inferred_skills == []
    assert result.profile_payload["profile_stage"] == "canonical_build_blocked"
    assert result.profile_payload["canonical_fields_pending"] is True
    assert result.profile_payload["blocked_reason"] == "no_auto_match_accounts"
    assert runs_repo.summary["canonical_profile_built"] is False


def test_hn_only_direct_anchor_does_not_become_high_confidence():
    profile = _profile(display_name=None)
    hn = _account(source="hackernews", handle="amil122", display_name="amil122")
    service, _profiles_repo, _runs_repo = _service(
        profile,
        [hn],
        [_link(hn, confidence=0.90, evidence=0.20, relationship="primary")],
    )

    result = service.build_by_profile_id(profile_id=profile["id"])

    assert result.status == CanonicalBuildStatus.BUILT
    assert result.display_name == "amil122"
    assert result.confidence_level != "high"
    assert result.bio is None
    assert result.primary_website_url is None
    assert result.primary_avatar_url is None


def test_generic_subdomain_website_loses_to_real_website():
    profile = _profile()
    github = _account(source="github", handle="amil", display_name="Muhammed Amil", website_url="https://amil.substack.com")
    devto = _account(source="devto", handle="amil", display_name="Muhammed Amil", website_url="https://amil.dev")
    service, _profiles_repo, _runs_repo = _service(
        profile,
        [github, devto],
        [_link(github, relationship="primary"), _link(devto)],
    )

    result = service.build_by_profile_id(profile_id=profile["id"])

    assert result.primary_website_url == "https://amil.dev"


def test_build_is_idempotent_for_payload_arrays_and_skills():
    profile = _profile()
    github = _account(source="github", handle="amil", display_name="Muhammed Amil", topics=["python", "fastapi"])
    hn = _account(source="hackernews", handle="amil122", display_name="amil122")
    devto = _account(source="devto", handle="other", display_name="Other Person", website_url="https://other.dev")
    service, _profiles_repo, _runs_repo = _service(
        profile,
        [github, hn, devto],
        [
            _link(github, relationship="primary"),
            _link(hn, decision="needs_review", confidence=0.70, evidence=0.40, relationship="possible_alias"),
            _link(devto, decision="reject", confidence=0.20, evidence=0.10, relationship="rejected"),
        ],
    )

    first = service.build_by_profile_id(profile_id=profile["id"])
    second = service.build_by_profile_id(profile_id=profile["id"])

    assert second.canonical_profile_id == first.canonical_profile_id
    assert second.inferred_skills == first.inferred_skills
    assert len(second.platform_profiles) == len(first.platform_profiles) == 1
    assert len(second.review_candidates) == len(first.review_candidates) == 1
    assert len(second.rejected_candidates) == len(first.rejected_candidates) == 1
    assert len(second.profile_payload["platform_profiles"]) == 1
    assert len(second.profile_payload["review_candidates"]) == 1
    assert len(second.profile_payload["rejected_candidates"]) == 1
