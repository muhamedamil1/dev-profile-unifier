from __future__ import annotations

from uuid import uuid4

from app.services.profile_read_service import ProfileReadService


class FakeProfilesRepo:
    def __init__(self, profile):
        self.profile = profile

    def get_by_id(self, profile_id):
        if str(self.profile["id"]) == str(profile_id):
            return dict(self.profile)
        return None


class FakeSummariesRepo:
    def get_latest_by_profile_id(self, *, profile_id):
        return None


def _profile_with_sources(sources):
    profile_id = uuid4()
    return profile_id, {
        "id": str(profile_id),
        "resolution_run_id": str(uuid4()),
        "display_name": "Simon Willison",
        "headline": None,
        "location": None,
        "bio": None,
        "primary_avatar_url": None,
        "primary_website_url": None,
        "inferred_skills": [],
        "confidence_level": "medium",
        "profile_payload": {
            "profile_stage": "deterministic_built",
            "canonical_fields_pending": False,
            "platform_profiles": sources,
            "review_candidates": [],
            "rejected_candidates": [],
        },
    }


def test_read_service_exposes_claimed_input_anchor_reason_and_payload():
    decision_payload = {
        "metadata": {
            "anchor_floor": 0.85,
            "evidence_score_before_anchor_policy": 0.25,
            "account_score_explanation": [
                "Account hackernews:simonw scored 0.25 against the request.",
                "Applied independent evidence groups: input_identifier.",
            ],
        },
        "is_anchor": True,
        "rationale": [
            "Account matched a platform identifier directly provided in the request.",
            "Accepted as a user-provided anchor for this resolution run.",
        ],
        "risk_level": "low",
        "account_score": 0.25,
        "decision_basis": "anchor_input",
        "hn_conservative": True,
        "accepted_as_anchor": True,
        "decision_confidence_score": 0.85,
        "evidence_confidence_score": 0.25,
    }
    profile_id, profile = _profile_with_sources([
        {
            "source": "hackernews",
            "handle": "simonw",
            "decision": "auto_match",
            "verification_status": "claimed_by_input",
            "confidence_score": 0.85,
            "decision_payload": decision_payload,
        }
    ])

    service = ProfileReadService(profiles_repo=FakeProfilesRepo(profile), summaries_repo=FakeSummariesRepo())
    result = service.get_profile(profile_id)

    source = result.sources[0]
    assert source.decision_payload["decision_basis"] == "anchor_input"
    assert source.accepted_as_anchor is True
    assert source.decision_confidence_score == 0.85
    assert source.evidence_confidence_score == 0.25
    assert source.hn_conservative is True
    assert source.risk_level == "low"
    assert "not external ownership verification" in source.reason
    assert "Hacker News profiles are sparse" in source.reason


def test_read_service_handles_missing_or_malformed_decision_payloads():
    profile_id, profile = _profile_with_sources([
        {
            "source": "github",
            "handle": "simonw",
            "decision": "auto_match",
            "verification_status": "claimed_by_input",
            "confidence_score": 0.85,
            "decision_payload": None,
        },
        {
            "source": "devto",
            "handle": "simonw",
            "decision": "auto_match",
            "confidence_score": 0.86,
            "decision_payload": "not-a-dict",
        },
    ])

    service = ProfileReadService(profiles_repo=FakeProfilesRepo(profile), summaries_repo=FakeSummariesRepo())
    result = service.get_profile(profile_id)

    assert result.sources[0].decision_payload == {}
    assert result.sources[0].decision_confidence_score == 0.85
    assert result.sources[0].evidence_confidence_score == 0.85
    assert "claimed input" in result.sources[0].reason
    assert result.sources[1].decision_payload == {}
    assert result.sources[1].decision_confidence_score == 0.86
