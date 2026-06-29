from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from app.schemas.profile_api import (
    AISummaryAPI,
    APIWarning,
    ProfileDetailResponse,
    ProfileSourceAPI,
    ReviewCandidateAPI,
)
try:
    from app.utils.errors import AppError
except ImportError:  # pragma: no cover - only used when running overlay tests outside the full repo
    class AppError(RuntimeError):
        def __init__(self, *, message: str, public_message: str | None = None) -> None:
            super().__init__(message)
            self.message = message
            self.public_message = public_message or message



class ProfileReadService:
    """Read-model service for GET /profiles/{id}.

    This service intentionally reads from the deterministic canonical profile payload
    produced by Phase 8 and the optional AI summary metadata produced by Phase 9.
    It does not mutate identity decisions or call external APIs.
    """

    def __init__(
        self,
        *,
        profiles_repo,
        summaries_repo=None,
        resolution_runs_repo=None,
    ) -> None:
        self.profiles_repo = profiles_repo
        self.summaries_repo = summaries_repo
        self.resolution_runs_repo = resolution_runs_repo

    def get_profile(self, profile_id: UUID | str) -> ProfileDetailResponse:
        profile = self.profiles_repo.get_by_id(profile_id)
        if not profile:
            raise AppError(
                message=f"Canonical profile not found: {profile_id}",
                public_message="Canonical profile was not found.",
            )

        payload = profile.get("profile_payload")
        if not isinstance(payload, dict):
            payload = {}

        resolution_summary = self._load_resolution_summary(profile)
        ai_summary = self._load_ai_summary(profile, payload)

        return ProfileDetailResponse(
            profile_id=UUID(str(profile["id"])),
            resolution_run_id=UUID(str(profile["resolution_run_id"])) if profile.get("resolution_run_id") else None,
            display_name=profile.get("display_name"),
            headline=profile.get("headline"),
            location=profile.get("location"),
            bio=profile.get("bio"),
            primary_avatar_url=profile.get("primary_avatar_url"),
            primary_website_url=profile.get("primary_website_url"),
            inferred_skills=list(profile.get("inferred_skills") or []),
            confidence_level=profile.get("confidence_level"),
            profile_stage=payload.get("profile_stage"),
            canonical_fields_pending=payload.get("canonical_fields_pending"),
            sources=[ProfileSourceAPI(**item) for item in payload.get("platform_profiles") or [] if isinstance(item, dict)],
            review_candidates=[ReviewCandidateAPI(**item) for item in payload.get("review_candidates") or [] if isinstance(item, dict)],
            rejected_candidates=[ReviewCandidateAPI(**item) for item in payload.get("rejected_candidates") or [] if isinstance(item, dict)],
            ai_summary=ai_summary,
            evidence_summary={
                "field_sources": payload.get("field_sources") or {},
                "deterministic_facts": payload.get("deterministic_facts") or [],
                "max_evidence_confidence_score": payload.get("max_evidence_confidence_score"),
                "max_decision_confidence_score": payload.get("max_decision_confidence_score"),
            },
            resolution_summary=resolution_summary,
            warnings=self._warnings_from_payload(payload),
            created_at=profile.get("created_at"),
            updated_at=profile.get("updated_at"),
        )

    def _load_resolution_summary(self, profile: dict[str, Any]) -> dict[str, Any]:
        payload = profile.get("profile_payload") if isinstance(profile.get("profile_payload"), dict) else {}
        summary = payload.get("resolution_summary") if isinstance(payload, dict) else None
        if isinstance(summary, dict):
            return dict(summary)

        if not self.resolution_runs_repo or not hasattr(self.resolution_runs_repo, "get_by_id"):
            return {}

        resolution_run_id = profile.get("resolution_run_id")
        if not resolution_run_id:
            return {}

        run = self.resolution_runs_repo.get_by_id(resolution_run_id)
        result_summary = run.get("result_summary") if isinstance(run, dict) else None
        return dict(result_summary) if isinstance(result_summary, dict) else {}

    def _load_ai_summary(self, profile: dict[str, Any], payload: dict[str, Any]) -> AISummaryAPI | None:
        phase_9_summary = payload.get("phase_9_summary")
        if isinstance(phase_9_summary, dict):
            return AISummaryAPI(**phase_9_summary)

        latest_row = self._load_latest_summary_row(profile.get("id"))
        if not latest_row:
            return None

        parsed_summary = self._parse_summary_text(latest_row.get("summary"))
        return AISummaryAPI(
            summary_id=UUID(str(latest_row["id"])) if latest_row.get("id") else None,
            model=latest_row.get("model"),
            prompt_version=latest_row.get("prompt_version"),
            headline=parsed_summary.get("headline"),
            short_summary=parsed_summary.get("short_summary"),
            strengths=list(parsed_summary.get("strengths") or []),
            source_note=parsed_summary.get("source_note"),
            limitations=list(parsed_summary.get("limitations") or []),
            used_fallback=bool(parsed_summary.get("used_fallback", False)),
        )

    def _load_latest_summary_row(self, profile_id: Any) -> dict[str, Any] | None:
        if not self.summaries_repo or not profile_id:
            return None

        for method_name in (
            "get_latest_by_profile_id",
            "get_latest_for_profile",
            "get_latest_summary_for_profile",
        ):
            method = getattr(self.summaries_repo, method_name, None)
            if callable(method):
                row = method(profile_id=profile_id)
                return dict(row) if isinstance(row, dict) else None

        return None

    @staticmethod
    def _parse_summary_text(summary_text: Any) -> dict[str, Any]:
        if isinstance(summary_text, dict):
            return dict(summary_text)
        if not isinstance(summary_text, str) or not summary_text.strip():
            return {}
        try:
            parsed = json.loads(summary_text)
        except json.JSONDecodeError:
            return {"short_summary": summary_text}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _warnings_from_payload(payload: dict[str, Any]) -> list[APIWarning]:
        warnings: list[APIWarning] = []
        if payload.get("canonical_fields_pending") is True:
            warnings.append(
                APIWarning(
                    code="canonical_fields_pending",
                    message="Canonical fields are still pending and may be incomplete.",
                )
            )
        if payload.get("review_candidates"):
            warnings.append(
                APIWarning(
                    code="ambiguous_candidates_present",
                    message="Some source accounts were left for review and were not used as factual canonical sources.",
                    details={"count": len(payload.get("review_candidates") or [])},
                )
            )
        return warnings
