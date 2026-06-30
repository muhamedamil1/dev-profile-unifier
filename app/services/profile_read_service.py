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
            sources=self._build_source_rows(payload.get("platform_profiles") or []),
            review_candidates=self._build_candidate_rows(payload.get("review_candidates") or []),
            rejected_candidates=self._build_candidate_rows(payload.get("rejected_candidates") or []),
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

    def _build_source_rows(self, rows: Any) -> list[ProfileSourceAPI]:
        source_rows: list[ProfileSourceAPI] = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            source_rows.append(ProfileSourceAPI(**self._enrich_explainability(dict(item))))
        return source_rows

    def _build_candidate_rows(self, rows: Any) -> list[ReviewCandidateAPI]:
        candidate_rows: list[ReviewCandidateAPI] = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            candidate_rows.append(ReviewCandidateAPI(**self._enrich_explainability(dict(item))))
        return candidate_rows

    def _enrich_explainability(self, item: dict[str, Any]) -> dict[str, Any]:
        decision_payload = self._extract_decision_payload(item)
        item["decision_payload"] = decision_payload

        metadata = decision_payload.get("metadata") if isinstance(decision_payload.get("metadata"), dict) else {}
        field_map = {
            "evidence_confidence_score": ("evidence_confidence_score", "evidence_score_before_anchor_policy", "account_score"),
            "decision_confidence_score": ("decision_confidence_score", "confidence_score"),
            "accepted_as_anchor": ("accepted_as_anchor", "is_anchor"),
            "is_anchor": ("is_anchor", "accepted_as_anchor"),
            "hn_conservative": ("hn_conservative",),
            "decision_basis": ("decision_basis",),
            "risk_level": ("risk_level",),
        }
        for target, keys in field_map.items():
            if item.get(target) is not None:
                continue
            for key in keys:
                value = decision_payload.get(key)
                if value is None and isinstance(metadata, dict):
                    value = metadata.get(key)
                if value is not None:
                    item[target] = value
                    break

        if item.get("decision_confidence_score") is None and item.get("confidence_score") is not None:
            item["decision_confidence_score"] = item.get("confidence_score")
        if item.get("evidence_confidence_score") is None:
            evidence_score = metadata.get("evidence_score_before_anchor_policy") if isinstance(metadata, dict) else None
            item["evidence_confidence_score"] = evidence_score if evidence_score is not None else item.get("confidence_score")

        if not self._safe_str(item.get("reason")):
            reason = self._reason_from_item_and_payload(item=item, decision_payload=decision_payload)
            if reason:
                item["reason"] = reason
        if item.get("rationale") is None:
            item["rationale"] = self._rationale_text(decision_payload.get("rationale"))
        return item

    @staticmethod
    def _extract_decision_payload(item: dict[str, Any]) -> dict[str, Any]:
        for candidate in (item.get("decision_payload"), item.get("link_decision_payload"), item.get("link_payload")):
            if isinstance(candidate, dict):
                return dict(candidate)
        evidence_summary = item.get("evidence_summary")
        if isinstance(evidence_summary, dict):
            for key in ("decision_payload", "link_decision_payload", "classifier_payload"):
                nested = evidence_summary.get(key)
                if isinstance(nested, dict):
                    return dict(nested)
        return {}

    def _reason_from_item_and_payload(self, *, item: dict[str, Any], decision_payload: dict[str, Any]) -> str | None:
        source = self._safe_str(item.get("source") or item.get("platform")) or "source"
        verification_status = self._safe_str(item.get("verification_status"))
        decision_basis = self._safe_str(item.get("decision_basis") or decision_payload.get("decision_basis"))
        accepted_as_anchor = bool(item.get("accepted_as_anchor") or decision_payload.get("accepted_as_anchor") or item.get("is_anchor") or decision_payload.get("is_anchor"))
        hn_conservative = bool(item.get("hn_conservative") or decision_payload.get("hn_conservative") or source == "hackernews")

        if verification_status == "claimed_by_input" or decision_basis == "anchor_input" or accepted_as_anchor:
            if source == "hackernews" or hn_conservative:
                return (
                    "User provided this Hacker News handle; accepted as a claimed input anchor, "
                    "not external ownership verification. Hacker News profiles are sparse, so treat this conservatively unless stronger evidence is present."
                )
            explicit = self._first_text(item.get("reason"), item.get("rationale"), item.get("explanation"))
            if explicit:
                return explicit
            return f"User provided this {source} identifier; accepted as claimed input, not external ownership verification."

        explicit = self._first_text(item.get("reason"), item.get("rationale"), item.get("explanation"))
        if explicit:
            return explicit

        rationale = decision_payload.get("rationale")
        rationale_text = self._rationale_text(rationale)
        if rationale_text:
            return rationale_text

        metadata = decision_payload.get("metadata")
        if isinstance(metadata, dict):
            explanation = metadata.get("account_score_explanation")
            explanation_text = self._rationale_text(explanation)
            if explanation_text:
                return explanation_text

        if verification_status == "claimed_by_input":
            return f"User provided this {source} identifier. It is accepted as claimed input, not external verification."
        return None

    @staticmethod
    def _rationale_text(value: Any) -> str | None:
        if isinstance(value, list):
            texts = [ProfileReadService._safe_str(item) for item in value]
            texts = [item for item in texts if item]
            return " ".join(texts[:2]) if texts else None
        return ProfileReadService._safe_str(value)

    @staticmethod
    def _first_text(*values: Any) -> str | None:
        for value in values:
            text = ProfileReadService._rationale_text(value)
            if text:
                return text
        return None

    @staticmethod
    def _safe_str(value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return None
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
        review_candidate_count = len(payload.get("review_candidates") or [])
        blocked_reason = payload.get("canonical_build_blocked_reason") or payload.get("blocked_reason")
        if blocked_reason:
            outcome = payload.get("outcome") or payload.get("status")
            if blocked_reason == "no_candidates_found" or outcome == "no_candidates_found":
                warnings.append(
                    APIWarning(
                        code="no_candidates_found",
                        message="No public candidate accounts were found for this request.",
                        details={"reason": "no_candidates_found", "review_candidate_count": 0},
                    )
                )
            else:
                warnings.append(
                    APIWarning(
                        code="profile_needs_review",
                        message="No confident canonical profile was created. Review candidates before trusting this identity.",
                        details={
                            "reason": str(blocked_reason),
                            "review_candidate_count": review_candidate_count,
                        },
                    )
                )
            return warnings

        if payload.get("canonical_fields_pending") is True:
            warnings.append(
                APIWarning(
                    code="canonical_fields_pending",
                    message="Canonical fields are pending until the preserved candidate accounts are reviewed.",
                )
            )
        if payload.get("review_candidates"):
            warnings.append(
                APIWarning(
                    code="ambiguous_candidates_present",
                    message="Possible matching accounts were preserved for review and excluded from factual canonical fields.",
                    details={"count": review_candidate_count},
                )
            )
        return warnings

