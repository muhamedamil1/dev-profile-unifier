from __future__ import annotations

import time
from typing import Any
from uuid import UUID

from app.llm.gemini_client import (
    GeminiClient,
    GeminiClientError,
    estimate_gemini_cost_usd,
    estimate_tokens,
)
from app.llm.prompts import (
    SAFE_SOURCE_NOTE,
    SUMMARY_PROMPT_VERSION,
    build_profile_summary_prompt,
    build_summary_prompt_payload,
    deterministic_fallback_summary,
    parse_structured_summary_json,
    sanitize_forbidden_claims,
    structured_summary_text,
    text_contains_forbidden_claims,
)
from app.schemas.summary import (
    StructuredProfileSummary,
    SummaryGenerationResult,
    SummaryGenerationStatus,
    SummarySafetyFlag,
)
from app.utils.errors import AppError


class SummaryService:
    """
    Phase 9: Gemini-backed summary generation for deterministic canonical profiles.

    This service does not change identity decisions, does not re-score accounts,
    and never uses needs_review/rejected candidates as factual summary sources.
    """

    def __init__(
        self,
        *,
        profiles_repo,
        summaries_repo,
        metrics_repo,
        gemini_client: GeminiClient,
        resolution_runs_repo=None,
    ) -> None:
        self.profiles_repo = profiles_repo
        self.summaries_repo = summaries_repo
        self.metrics_repo = metrics_repo
        self.gemini_client = gemini_client
        self.resolution_runs_repo = resolution_runs_repo

    def generate_for_profile_id(
        self,
        *,
        profile_id: UUID | str,
        persist: bool = True,
        replace_existing: bool = True,
        allow_fallback: bool = True,
    ) -> SummaryGenerationResult:
        profile = self.profiles_repo.get_by_id(profile_id)
        if not profile:
            raise AppError(
                message=f"Canonical profile not found: {profile_id}",
                public_message="Canonical profile was not found.",
            )

        self._ensure_profile_ready(profile)

        prompt_payload = build_summary_prompt_payload(profile)
        prompt_text = build_profile_summary_prompt(prompt_payload)
        safety_flags: list[SummarySafetyFlag] = []
        raw_text: str | None = None
        model_name = self.gemini_client.model_name
        input_tokens = estimate_tokens(prompt_text)
        output_tokens = 0
        duration_ms = 0
        status_code = 200
        error_message: str | None = None
        used_fallback = False
        fallback_reason: str | None = None

        started = time.perf_counter()

        try:
            if not self.gemini_client.available:
                raise GeminiClientError("Gemini API key is not configured.")

            generated = self.gemini_client.generate_text(prompt=prompt_text)
            raw_text = generated.text
            duration_ms = generated.duration_ms
            model_name = generated.model
            input_tokens = generated.input_tokens or input_tokens
            output_tokens = generated.output_tokens or estimate_tokens(generated.text)

            if not generated.text.strip():
                safety_flags.append(SummarySafetyFlag.EMPTY_RESPONSE_FALLBACK)
                raise ValueError("Gemini returned an empty summary response.")

            structured = parse_structured_summary_json(generated.text)

        except Exception as exc:
            if not allow_fallback:
                if persist:
                    self._record_metric(
                        profile=profile,
                        endpoint="gemini.generate_content",
                        status_code=500,
                        duration_ms=int((time.perf_counter() - started) * 1000),
                        error_message="Gemini summary generation failed.",
                        metadata={
                            "model": model_name,
                            "prompt_version": SUMMARY_PROMPT_VERSION,
                            "error_type": type(exc).__name__,
                            "profile_id": str(profile["id"]),
                        },
                    )
                raise

            used_fallback = True
            fallback_reason = type(exc).__name__
            status_code = 503 if isinstance(exc, GeminiClientError) else 502
            error_message = "Gemini summary generation fell back to deterministic summary."

            if isinstance(exc, GeminiClientError):
                safety_flags.append(SummarySafetyFlag.GEMINI_UNAVAILABLE_FALLBACK)
            elif SummarySafetyFlag.EMPTY_RESPONSE_FALLBACK not in safety_flags:
                safety_flags.append(SummarySafetyFlag.INVALID_JSON_FALLBACK)

            structured = deterministic_fallback_summary(prompt_payload)
            duration_ms = int((time.perf_counter() - started) * 1000)
            output_tokens = estimate_tokens(structured.model_dump_json())

        structured, sanitized = sanitize_forbidden_claims(structured)
        if sanitized and SummarySafetyFlag.FORBIDDEN_CLAIM_REMOVED not in safety_flags:
            safety_flags.append(SummarySafetyFlag.FORBIDDEN_CLAIM_REMOVED)

        if text_contains_forbidden_claims(structured_summary_text(structured)):
            if SummarySafetyFlag.FORBIDDEN_CLAIM_REMOVED not in safety_flags:
                safety_flags.append(SummarySafetyFlag.FORBIDDEN_CLAIM_REMOVED)
            structured = deterministic_fallback_summary(prompt_payload)
            used_fallback = True
            fallback_reason = fallback_reason or "ForbiddenClaimAfterSanitization"
            status_code = 502
            output_tokens = estimate_tokens(structured.model_dump_json())

        structured = self._ensure_required_safety_note(structured)
        estimated_cost = estimate_gemini_cost_usd(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

        summary_row: dict[str, Any] | None = None

        if persist:
            if replace_existing and hasattr(self.summaries_repo, "delete_by_profile_and_prompt_version"):
                self.summaries_repo.delete_by_profile_and_prompt_version(
                    profile_id=profile["id"],
                    prompt_version=SUMMARY_PROMPT_VERSION,
                    model=model_name,
                )

            summary_row = self.summaries_repo.create_summary(
                profile_id=profile["id"],
                model=model_name,
                prompt_version=SUMMARY_PROMPT_VERSION,
                prompt_text=prompt_text,
                summary=structured.model_dump_json(),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated_cost_usd=estimated_cost,
            )

            self._patch_profile_payload(
                profile=profile,
                summary_row=summary_row,
                structured=structured,
                used_fallback=used_fallback,
                safety_flags=safety_flags,
                model_name=model_name,
                prompt_version=SUMMARY_PROMPT_VERSION,
            )

            self._patch_resolution_summary(
                profile=profile,
                summary_row=summary_row,
                used_fallback=used_fallback,
                safety_flags=safety_flags,
            )

            self._record_metric(
                profile=profile,
                endpoint="gemini.generate_content",
                status_code=status_code,
                duration_ms=duration_ms,
                error_message=error_message if used_fallback else None,
                metadata={
                    "model": model_name,
                    "prompt_version": SUMMARY_PROMPT_VERSION,
                    "used_fallback": used_fallback,
                    "fallback_reason": fallback_reason,
                    "safety_flags": [flag.value for flag in safety_flags],
                    "profile_id": str(profile["id"]),
                    "summary_id": str(summary_row["id"]) if summary_row and summary_row.get("id") else None,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "estimated_cost_usd": estimated_cost,
                },
            )

        return SummaryGenerationResult(
            profile_id=UUID(str(profile["id"])),
            status=SummaryGenerationStatus.FALLBACK if used_fallback else SummaryGenerationStatus.GENERATED,
            summary_id=UUID(str(summary_row["id"])) if summary_row and summary_row.get("id") else None,
            model=model_name,
            prompt_version=SUMMARY_PROMPT_VERSION,
            prompt_text=prompt_text,
            summary=structured,
            raw_model_text=raw_text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=estimated_cost,
            safety_flags=safety_flags,
            persisted=bool(persist),
            used_fallback=used_fallback,
            metadata={
                "profile_stage": (profile.get("profile_payload") or {}).get("profile_stage") if isinstance(profile.get("profile_payload"), dict) else None,
                "fallback_reason": fallback_reason,
            },
        )

    def _ensure_profile_ready(self, profile: dict[str, Any]) -> None:
        payload = profile.get("profile_payload")
        if not isinstance(payload, dict):
            payload = {}

        if payload.get("profile_stage") != "deterministic_built":
            raise AppError(
                message=f"Canonical profile is not ready for summary: {profile.get('id')}",
                public_message="Canonical profile must be built before generating an AI summary.",
            )

        if payload.get("canonical_fields_pending") is True:
            raise AppError(
                message=f"Canonical profile still has pending deterministic fields: {profile.get('id')}",
                public_message="Canonical profile fields are not ready for summary generation.",
            )

    def _ensure_required_safety_note(self, summary: StructuredProfileSummary) -> StructuredProfileSummary:
        summary.source_note = SAFE_SOURCE_NOTE

        limitation_text = " ".join(summary.limitations).lower()
        if "ownership" not in limitation_text and "oauth" not in limitation_text:
            summary.limitations.append(
                "Public profiles do not prove account ownership without OAuth or user-controlled verification."
            )
        return summary

    def _patch_profile_payload(
        self,
        *,
        profile: dict[str, Any],
        summary_row: dict[str, Any],
        structured: StructuredProfileSummary,
        used_fallback: bool,
        safety_flags: list[SummarySafetyFlag],
        model_name: str,
        prompt_version: str,
    ) -> None:
        patch = {
            "phase_9_summary": {
                "summary_id": str(summary_row.get("id")) if summary_row.get("id") else None,
                "model": model_name,
                "prompt_version": prompt_version,
                "used_fallback": used_fallback,
                "safety_flags": [flag.value for flag in safety_flags],
                "headline": structured.headline,
                "short_summary": structured.short_summary,
                "strengths": structured.strengths,
                "source_note": structured.source_note,
                "limitations": structured.limitations,
            },
            "ai_summary_generated": True,
            "ai_summary_stage": "gemini_summary_generated" if not used_fallback else "deterministic_fallback_summary",
        }

        if hasattr(self.profiles_repo, "update_profile_payload_patch"):
            self.profiles_repo.update_profile_payload_patch(
                profile_id=profile["id"],
                patch=patch,
            )

    def _patch_resolution_summary(
        self,
        *,
        profile: dict[str, Any],
        summary_row: dict[str, Any],
        used_fallback: bool,
        safety_flags: list[SummarySafetyFlag],
    ) -> None:
        if not self.resolution_runs_repo:
            return

        if not hasattr(self.resolution_runs_repo, "update_result_summary_patch"):
            return

        resolution_run_id = profile.get("resolution_run_id")
        if not resolution_run_id:
            return

        self.resolution_runs_repo.update_result_summary_patch(
            resolution_run_id=resolution_run_id,
            patch={
                "ai_summary_generated": True,
                "ai_summary_id": str(summary_row.get("id")) if summary_row.get("id") else None,
                "ai_summary_stage": "gemini_summary_generated" if not used_fallback else "deterministic_fallback_summary",
                "ai_summary_used_fallback": used_fallback,
                "ai_summary_safety_flags": [flag.value for flag in safety_flags],
                "ai_summary_prompt_version": SUMMARY_PROMPT_VERSION,
            },
        )

    def _record_metric(
        self,
        *,
        profile: dict[str, Any],
        endpoint: str,
        status_code: int,
        duration_ms: int,
        error_message: str | None,
        metadata: dict[str, Any],
    ) -> None:
        if not self.metrics_repo:
            return

        if hasattr(self.metrics_repo, "record_metric"):
            self.metrics_repo.record_metric(
                resolution_run_id=profile.get("resolution_run_id"),
                source="gemini",
                endpoint=endpoint,
                http_method="POST",
                status_code=status_code,
                duration_ms=duration_ms,
                error_message=error_message,
                metadata=metadata,
            )
