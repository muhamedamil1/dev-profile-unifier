from __future__ import annotations

import json
import os
from uuid import uuid4

import pytest

from app.llm.gemini_client import GeminiClientError, GeminiTextResult
from app.llm.prompts import SAFE_SOURCE_NOTE, SUMMARY_PROMPT_VERSION, text_contains_forbidden_claims
from app.schemas.summary import SummaryGenerationStatus, SummarySafetyFlag
from app.services.summary_service import SummaryService


def status_value(value):
    return value.value if hasattr(value, "value") else str(value)


class FakeProfilesRepo:
    def __init__(self, profile: dict):
        self.profile = dict(profile)
        self.payload_patches: list[dict] = []

    def get_by_id(self, profile_id):
        if str(self.profile["id"]) == str(profile_id):
            return dict(self.profile)
        return None

    def update_profile_payload_patch(self, *, profile_id, patch: dict):
        assert str(profile_id) == str(self.profile["id"])
        current = self.profile.get("profile_payload")
        if not isinstance(current, dict):
            current = {}
        self.profile["profile_payload"] = {**current, **patch}
        self.payload_patches.append(dict(patch))
        return dict(self.profile)


class FakeSummariesRepo:
    def __init__(self):
        self.deleted: list[dict] = []
        self.rows: list[dict] = []

    def delete_by_profile_and_prompt_version(self, *, profile_id, prompt_version, model=None):
        self.deleted.append(
            {
                "profile_id": str(profile_id),
                "prompt_version": prompt_version,
                "model": model,
            }
        )
        before = len(self.rows)
        self.rows = [
            row for row in self.rows
            if not (
                row["profile_id"] == str(profile_id)
                and row["prompt_version"] == prompt_version
                and (model is None or row["model"] == model)
            )
        ]
        return before - len(self.rows)

    def create_summary(
        self,
        *,
        profile_id,
        model,
        prompt_version,
        prompt_text,
        summary,
        input_tokens,
        output_tokens,
        estimated_cost_usd,
    ):
        row = {
            "id": str(uuid4()),
            "profile_id": str(profile_id),
            "model": model,
            "prompt_version": prompt_version,
            "prompt_text": prompt_text,
            "summary": summary,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "estimated_cost_usd": estimated_cost_usd,
        }
        self.rows.append(row)
        return dict(row)


class FakeMetricsRepo:
    def __init__(self):
        self.rows: list[dict] = []

    def record_metric(self, **kwargs):
        self.rows.append(dict(kwargs))
        return {"id": str(uuid4()), **kwargs}


class FakeResolutionRunsRepo:
    def __init__(self):
        self.patches: list[dict] = []

    def update_result_summary_patch(self, *, resolution_run_id, patch: dict):
        self.patches.append(
            {
                "resolution_run_id": str(resolution_run_id),
                "patch": dict(patch),
            }
        )
        return {"id": str(resolution_run_id), "result_summary": dict(patch)}


class FakeGeminiClient:
    def __init__(self, *, text: str | None = None, error: Exception | None = None, available: bool = True):
        self.text = text
        self.error = error
        self._available = available
        self.model_name = "gemini-test-model"
        self.calls: list[str] = []

    @property
    def available(self):
        return self._available

    def generate_text(self, *, prompt: str):
        self.calls.append(prompt)
        if self.error:
            raise self.error
        return GeminiTextResult(
            text=self.text or "{}",
            model=self.model_name,
            duration_ms=12,
            input_tokens=120,
            output_tokens=80,
        )


def built_profile() -> dict:
    profile_id = uuid4()
    run_id = uuid4()
    return {
        "id": str(profile_id),
        "resolution_run_id": str(run_id),
        "display_name": "Muhammed Amil",
        "headline": "Developer focused on Python and FastAPI",
        "location": "Bangalore, India",
        "bio": "AI backend developer building Python, FastAPI, and Supabase systems.",
        "primary_avatar_url": "https://avatars.githubusercontent.com/u/101",
        "primary_website_url": "https://amil.dev",
        "inferred_skills": ["Python", "FastAPI", "Supabase", "AI"],
        "confidence_level": "high",
        "profile_payload": {
            "profile_stage": "deterministic_built",
            "phase": "8",
            "canonical_fields_pending": False,
            "resolution_summary": {"phase": "7E", "auto_match_count": 2},
            "field_sources": {
                "display_name": {"strategy": "repeated_real_name", "source_account_keys": ["github:101", "devto:202"]},
            },
            "platform_profiles": [
                {
                    "source": "github",
                    "source_account_key": "github:101",
                    "handle": "amil122",
                    "profile_url": "https://github.com/amil122",
                    "decision": "auto_match",
                },
                {
                    "source": "devto",
                    "source_account_key": "devto:202",
                    "handle": "muhammedamil",
                    "profile_url": "https://dev.to/muhammedamil",
                    "decision": "auto_match",
                },
            ],
            "review_candidates": [
                {"source": "hackernews", "source_account_key": "hackernews:amil122", "decision": "needs_review"}
            ],
            "rejected_candidates": [
                {"source": "devto", "source_account_key": "devto:999", "decision": "reject"}
            ],
            "activity_summary": {
                "accepted_source_count": 2,
                "review_source_count": 1,
                "rejected_source_count": 1,
            },
            "deterministic_facts": [
                {"fact_type": "skill", "fact_value": "Python"},
                {"fact_type": "platform_profile", "fact_value": "https://github.com/amil122"},
            ],
        },
    }


def make_service(*, profile: dict, gemini_client: FakeGeminiClient):
    profiles_repo = FakeProfilesRepo(profile)
    summaries_repo = FakeSummariesRepo()
    metrics_repo = FakeMetricsRepo()
    runs_repo = FakeResolutionRunsRepo()
    service = SummaryService(
        profiles_repo=profiles_repo,
        summaries_repo=summaries_repo,
        metrics_repo=metrics_repo,
        resolution_runs_repo=runs_repo,
        gemini_client=gemini_client,
    )
    return service, profiles_repo, summaries_repo, metrics_repo, runs_repo


def test_summary_service_generates_and_persists_safe_gemini_summary():
    profile = built_profile()
    gemini_text = json.dumps(
        {
            "headline": "Developer focused on Python, FastAPI, Supabase, and AI",
            "short_summary": "Muhammed Amil appears to be a backend-oriented developer based on accepted public profile data.",
            "strengths": ["Python backend development", "FastAPI systems", "Supabase-backed applications"],
            "source_note": "Summary is based on accepted public source accounts and deterministic canonical profile fields.",
            "limitations": ["Public profiles do not prove account ownership without OAuth or user-controlled verification."],
        }
    )
    service, profiles_repo, summaries_repo, metrics_repo, runs_repo = make_service(
        profile=profile,
        gemini_client=FakeGeminiClient(text=gemini_text),
    )

    result = service.generate_for_profile_id(profile_id=profile["id"])

    assert status_value(result.status) == "generated"
    assert result.persisted is True
    assert result.used_fallback is False
    assert result.summary_id is not None
    assert result.summary.headline.startswith("Developer focused")
    assert "accepted public" in result.summary.short_summary
    assert result.summary.source_note == SAFE_SOURCE_NOTE

    assert len(summaries_repo.rows) == 1
    stored = summaries_repo.rows[0]
    assert stored["profile_id"] == profile["id"]
    assert stored["prompt_version"] == SUMMARY_PROMPT_VERSION
    assert json.loads(stored["summary"])["headline"] == result.summary.headline

    payload = profiles_repo.profile["profile_payload"]
    assert payload["ai_summary_generated"] is True
    assert payload["ai_summary_stage"] == "gemini_summary_generated"
    assert payload["phase_9_summary"]["summary_id"] == str(result.summary_id)

    assert runs_repo.patches[-1]["patch"]["ai_summary_generated"] is True
    assert runs_repo.patches[-1]["patch"]["ai_summary_stage"] == "gemini_summary_generated"

    assert metrics_repo.rows[-1]["source"] == "gemini"
    assert metrics_repo.rows[-1]["status_code"] == 200


def test_summary_service_sanitizes_forbidden_verified_claims():
    profile = built_profile()
    gemini_text = json.dumps(
        {
            "headline": "Verified AI developer",
            "short_summary": "Muhammed Amil is the verified owner of these accounts and definitely the same person.",
            "strengths": ["Verified Python expertise"],
            "source_note": "Verified by public data.",
            "limitations": [],
        }
    )
    service, _profiles_repo, summaries_repo, _metrics_repo, _runs_repo = make_service(
        profile=profile,
        gemini_client=FakeGeminiClient(text=gemini_text),
    )

    result = service.generate_for_profile_id(profile_id=profile["id"])
    combined = " ".join(
        [
            result.summary.headline,
            result.summary.short_summary,
            result.summary.source_note,
            " ".join(result.summary.strengths),
            " ".join(result.summary.limitations),
        ]
    ).lower()

    assert not text_contains_forbidden_claims(combined)
    assert SummarySafetyFlag.FORBIDDEN_CLAIM_REMOVED in result.safety_flags
    assert len(summaries_repo.rows) == 1


def test_summary_service_falls_back_on_extra_unapproved_json_keys():
    profile = built_profile()
    gemini_text = json.dumps(
        {
            "headline": "Developer focused on Python",
            "short_summary": "Based on accepted source accounts.",
            "strengths": ["Python"],
            "source_note": "Based on accepted source accounts.",
            "limitations": [],
            "verified_accounts": ["github:101"],
        }
    )
    service, _profiles_repo, _summaries_repo, _metrics_repo, _runs_repo = make_service(
        profile=profile,
        gemini_client=FakeGeminiClient(text=gemini_text),
    )

    result = service.generate_for_profile_id(profile_id=profile["id"])

    assert status_value(result.status) == "fallback"
    assert SummarySafetyFlag.INVALID_JSON_FALLBACK in result.safety_flags
    assert result.summary.source_note == SAFE_SOURCE_NOTE


def test_summary_service_falls_back_when_gemini_unavailable():
    profile = built_profile()
    service, profiles_repo, summaries_repo, metrics_repo, runs_repo = make_service(
        profile=profile,
        gemini_client=FakeGeminiClient(available=False, error=GeminiClientError("missing key")),
    )

    result = service.generate_for_profile_id(profile_id=profile["id"])

    assert status_value(result.status) == "fallback"
    assert result.used_fallback is True
    assert SummarySafetyFlag.GEMINI_UNAVAILABLE_FALLBACK in result.safety_flags
    assert "Python" in result.summary.short_summary
    assert len(summaries_repo.rows) == 1
    assert profiles_repo.profile["profile_payload"]["ai_summary_stage"] == "deterministic_fallback_summary"
    assert runs_repo.patches[-1]["patch"]["ai_summary_used_fallback"] is True
    assert metrics_repo.rows[-1]["status_code"] == 503
    assert metrics_repo.rows[-1]["metadata"]["fallback_reason"] == "GeminiClientError"


def test_summary_service_falls_back_on_invalid_json():
    profile = built_profile()
    service, _profiles_repo, _summaries_repo, metrics_repo, _runs_repo = make_service(
        profile=profile,
        gemini_client=FakeGeminiClient(text="not json at all"),
    )

    result = service.generate_for_profile_id(profile_id=profile["id"])

    assert status_value(result.status) == "fallback"
    assert SummarySafetyFlag.INVALID_JSON_FALLBACK in result.safety_flags
    assert result.summary.short_summary
    assert metrics_repo.rows[-1]["status_code"] == 502


def test_summary_service_strict_mode_raises_and_records_only_metric_when_persisting():
    profile = built_profile()
    service, profiles_repo, summaries_repo, metrics_repo, runs_repo = make_service(
        profile=profile,
        gemini_client=FakeGeminiClient(text="not json at all"),
    )

    with pytest.raises(Exception):
        service.generate_for_profile_id(
            profile_id=profile["id"],
            allow_fallback=False,
            persist=True,
        )

    assert summaries_repo.rows == []
    assert profiles_repo.payload_patches == []
    assert runs_repo.patches == []
    assert len(metrics_repo.rows) == 1
    assert metrics_repo.rows[0]["status_code"] == 500


def test_summary_service_persist_false_is_true_dry_run():
    profile = built_profile()
    gemini_text = json.dumps(
        {
            "headline": "Developer focused on Python",
            "short_summary": "Based on accepted public profile data.",
            "strengths": ["Python"],
            "source_note": "Based on accepted source accounts.",
            "limitations": [],
        }
    )
    service, profiles_repo, summaries_repo, metrics_repo, runs_repo = make_service(
        profile=profile,
        gemini_client=FakeGeminiClient(text=gemini_text),
    )

    result = service.generate_for_profile_id(profile_id=profile["id"], persist=False)

    assert status_value(result.status) == "generated"
    assert result.persisted is False
    assert result.summary_id is None
    assert summaries_repo.rows == []
    assert profiles_repo.payload_patches == []
    assert runs_repo.patches == []
    assert metrics_repo.rows == []


def test_summary_service_blocks_unbuilt_canonical_profile():
    profile = built_profile()
    profile["profile_payload"] = {
        "profile_stage": "resolution_shell",
        "canonical_fields_pending": True,
    }
    service, _profiles_repo, summaries_repo, metrics_repo, _runs_repo = make_service(
        profile=profile,
        gemini_client=FakeGeminiClient(text="{}"),
    )

    with pytest.raises(Exception):
        service.generate_for_profile_id(profile_id=profile["id"])

    assert summaries_repo.rows == []
    assert metrics_repo.rows == []


def test_summary_prompt_excludes_review_and_rejected_candidates_as_factual_sources():
    profile = built_profile()
    service, _profiles_repo, _summaries_repo, _metrics_repo, _runs_repo = make_service(
        profile=profile,
        gemini_client=FakeGeminiClient(text=json.dumps({
            "headline": "Developer focused on Python",
            "short_summary": "Based on accepted source accounts.",
            "strengths": ["Python"],
            "source_note": "Based on accepted source accounts.",
            "limitations": ["Review candidates were not used as factual sources."],
        })),
    )

    result = service.generate_for_profile_id(profile_id=profile["id"])
    prompt = result.prompt_text

    assert "review_candidate_count" in prompt
    assert "rejected_candidate_count" in prompt
    assert "review_candidates" not in prompt
    assert "rejected_candidates" not in prompt
    assert "hackernews:amil122" not in prompt
    assert "devto:999" not in prompt


@pytest.mark.skipif(
    not os.getenv("DEV_PROFILE_REAL_SUMMARY_PROFILE_ID"),
    reason="Set DEV_PROFILE_REAL_SUMMARY_PROFILE_ID to run live Phase 9 smoke test.",
)
def test_live_summary_service_smoke():
    from app.dependencies import get_summary_service

    profile_id = os.environ["DEV_PROFILE_REAL_SUMMARY_PROFILE_ID"]
    service = get_summary_service()
    result = service.generate_for_profile_id(profile_id=profile_id)

    assert status_value(result.status) in {"generated", "fallback"}
    assert result.summary.short_summary
    assert result.persisted is True
