from __future__ import annotations


from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.schemas.metrics import LLMUsageMetric
from app.storage.base import BaseRepository


class SummariesRepo(BaseRepository):
    table_name = "llm_summaries"

    def insert_summary(
        self,
        *,
        profile_id: str | UUID,
        summary: LLMUsageMetric,
    ) -> dict:
        return self._insert_one(
            summary.to_db_payload(profile_id=UUID(str(profile_id)))
        )

    def get_latest_for_profile(self, profile_id: str | UUID) -> dict | None:
        data = self._execute(
            self.client.table(self.table_name)
            .select("*")
            .eq("profile_id", str(profile_id))
            .order("created_at", desc=True)
            .limit(1),
            operation="get_latest_for_profile",
        )

        return self._first_or_none(data)

    def list_by_profile(self, profile_id: str | UUID) -> list[dict]:
        data = self._execute(
            self.client.table(self.table_name)
            .select("*")
            .eq("profile_id", str(profile_id))
            .order("created_at", desc=True),
            operation="list_by_profile",
        )

        return data if isinstance(data, list) else []

    def delete_by_profile_and_prompt_version(
        self,
        *,
        profile_id: UUID | str,
        prompt_version: str,
        model: str | None = None,
    ) -> int:
        query = (
            self.client.table(self.table_name)
            .delete()
            .eq("profile_id", str(profile_id))
            .eq("prompt_version", prompt_version)
        )

        if model:
            query = query.eq("model", model)

        data = self._execute(
            query,
            operation="delete_llm_summaries_by_profile_prompt_version",
        )
        return len(data or [])

    def create_summary(
        self,
        *,
        profile_id: UUID | str,
        model: str,
        prompt_version: str,
        prompt_text: str,
        summary: str,
        input_tokens: int,
        output_tokens: int,
        estimated_cost_usd: float,
    ) -> dict:
        payload = {
            "profile_id": str(profile_id),
            "model": model,
            "prompt_version": prompt_version,
            "prompt_text": prompt_text,
            "summary": summary,
            "input_tokens": int(input_tokens or 0),
            "output_tokens": int(output_tokens or 0),
            "estimated_cost_usd": float(estimated_cost_usd or 0.0),
        }
        return self._insert_one(payload)

    def get_latest_for_profile(self, profile_id: UUID | str) -> dict | None:
        data = self._execute(
            self.client.table(self.table_name)
            .select("*")
            .eq("profile_id", str(profile_id))
            .order("created_at", desc=True)
            .limit(1),
            operation="get_latest_llm_summary_for_profile",
        )
        return self._first_or_none(data)


# app/storage/metrics_repo.py
class MetricsRepoMixin:
    table_name = "api_call_metrics"

    def record_metric(
        self,
        *,
        resolution_run_id: UUID | str | None,
        source: str,
        endpoint: str,
        http_method: str,
        status_code: int | None,
        duration_ms: int,
        error_message: str | None = None,
        rate_limit_remaining: int | None = None,
        rate_limit_total: int | None = None,
        rate_limit_reset_at: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict:
        payload = {
            "resolution_run_id": str(resolution_run_id) if resolution_run_id else None,
            "source": source,
            "endpoint": endpoint,
            "http_method": http_method,
            "status_code": status_code,
            "duration_ms": int(duration_ms or 0),
            "error_message": error_message,
            "rate_limit_remaining": rate_limit_remaining,
            "rate_limit_total": rate_limit_total,
            "rate_limit_reset_at": rate_limit_reset_at,
            "metadata": metadata or {},
        }
        return self._insert_one(payload)


# app/storage/profiles_repo.py
class ProfilesRepoPayloadPatchMixin:
    def update_profile_payload_patch(
        self,
        *,
        profile_id: UUID | str,
        patch: dict[str, Any],
    ) -> dict:
        existing = self.get_by_id(profile_id)
        if not existing:
            raise ValueError(f"Canonical profile not found: {profile_id}")

        current_payload = existing.get("profile_payload")
        if not isinstance(current_payload, dict):
            current_payload = {}

        payload = {
            "profile_payload": {
                **current_payload,
                **patch,
            },
            "updated_at": datetime.now(UTC).isoformat(),
        }
        return self._update_by_id(str(profile_id), payload)


# app/storage/resolution_runs_repo.py
class ResolutionRunsRepoSummaryPatchMixin:
    def update_result_summary_patch(
        self,
        *,
        resolution_run_id: UUID | str,
        patch: dict[str, Any],
    ) -> dict:
        existing = self.get_by_id(resolution_run_id)
        if not existing:
            raise ValueError(f"Resolution run not found: {resolution_run_id}")

        current = existing.get("result_summary")
        if not isinstance(current, dict):
            current = {}

        payload = {
            "result_summary": {
                **current,
                **patch,
            }
        }
        return self._update_by_id(str(resolution_run_id), payload)
