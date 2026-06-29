from __future__ import annotations

from uuid import uuid4

import pytest

from app.services.profile_read_service import AppError, ProfileReadService


class FakeProfilesRepo:
    def __init__(self, profile):
        self.profile = profile

    def get_by_id(self, profile_id):
        if not self.profile:
            return None
        if str(self.profile["id"]) == str(profile_id):
            return dict(self.profile)
        return None


class FakeSummariesRepo:
    def get_latest_by_profile_id(self, *, profile_id):
        return None


def make_profile():
    profile_id = uuid4()
    run_id = uuid4()
    return {
        "id": str(profile_id),
        "resolution_run_id": str(run_id),
        "display_name": "Muhammed Amil",
        "headline": "Developer focused on Python and FastAPI",
        "location": "Bangalore, India",
        "bio": "Backend developer",
        "primary_avatar_url": "https://example.com/avatar.png",
        "primary_website_url": "https://amil.dev",
        "inferred_skills": ["Python", "FastAPI"],
        "confidence_level": "high",
        "profile_payload": {
            "profile_stage": "deterministic_built",
            "canonical_fields_pending": False,
            "platform_profiles": [
                {"source": "github", "handle": "amil122", "display_name": "Muhammed Amil", "decision": "auto_match"}
            ],
            "review_candidates": [
                {"source_account_key": "hackernews:amil122", "source": "hackernews", "handle": "amil122"}
            ],
            "rejected_candidates": [],
            "phase_9_summary": {
                "summary_id": str(uuid4()),
                "model": "gemini-2.5-flash-lite",
                "prompt_version": "profile_summary_v1",
                "headline": "Developer focused on Python and FastAPI",
                "short_summary": "Based on accepted public profile data, Muhammed appears focused on Python and FastAPI.",
                "strengths": ["Python", "FastAPI"],
                "source_note": "Based on accepted public source accounts.",
                "limitations": ["Public profiles do not prove account ownership without OAuth."],
                "used_fallback": False,
            },
            "field_sources": {"display_name": {"source": "github"}},
            "deterministic_facts": [{"field": "skill", "value": "Python"}],
            "resolution_summary": {"auto_match_count": 1},
        },
    }


def test_profile_read_service_returns_canonical_profile_payload():
    profile = make_profile()
    service = ProfileReadService(
        profiles_repo=FakeProfilesRepo(profile),
        summaries_repo=FakeSummariesRepo(),
    )

    result = service.get_profile(profile["id"])

    assert result.display_name == "Muhammed Amil"
    assert result.profile_stage == "deterministic_built"
    assert result.sources[0].source == "github"
    assert result.review_candidates[0].source == "hackernews"
    assert result.ai_summary is not None
    assert result.ai_summary.model == "gemini-2.5-flash-lite"
    assert result.evidence_summary["field_sources"]["display_name"]["source"] == "github"
    assert result.resolution_summary["auto_match_count"] == 1
    assert any(warning.code == "ambiguous_candidates_present" for warning in result.warnings)


def test_profile_read_service_raises_for_missing_profile():
    service = ProfileReadService(profiles_repo=FakeProfilesRepo(None))

    with pytest.raises(AppError):
        service.get_profile(uuid4())
