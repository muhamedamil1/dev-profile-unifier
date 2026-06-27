from __future__ import annotations

from app.schemas.metrics import (
    APICallMetric,
    LLMHealthMetrics,
    ProfileHealthMetrics,
    RateLimitState,
    SourceHealthMetric,
)
from app.storage.base import BaseRepository


class MetricsRepo(BaseRepository):
    table_name = "api_call_metrics"

    def record_api_call(self, metric: APICallMetric) -> dict:
        return self._insert_one(metric.to_db_payload())

    def get_profile_health_metrics(self) -> ProfileHealthMetrics:
        data = self._execute(
            self.client.table("health_profile_metrics").select("*").limit(1),
            operation="get_profile_health_metrics",
        )

        row = self._first_or_none(data) or {}

        return ProfileHealthMetrics(
            resolved_total=row.get("resolved_total") or 0,
            partial_total=row.get("partial_total") or 0,
            failed_total=row.get("failed_total") or 0,
            average_resolution_time_ms=row.get("average_resolution_time_ms") or 0,
        )

    def get_api_call_health_metrics(self) -> dict[str, SourceHealthMetric]:
        data = self._execute(
            self.client.table("health_api_call_metrics").select("*"),
            operation="get_api_call_health_metrics",
        )

        rows = data if isinstance(data, list) else []

        return {
            str(row["source"]): SourceHealthMetric(
                total=row.get("total") or 0,
                errors=row.get("errors") or 0,
                average_duration_ms=row.get("average_duration_ms") or 0,
            )
            for row in rows
            if row.get("source")
        }

    def get_latest_github_rate_limit(self) -> RateLimitState | None:
        data = self._execute(
            self.client.table("health_latest_github_rate_limit")
            .select("*")
            .limit(1),
            operation="get_latest_github_rate_limit",
        )

        row = self._first_or_none(data)
        if not row:
            return None

        return RateLimitState(
            remaining=row.get("remaining"),
            limit=row.get("limit"),
            reset_at=row.get("reset_at"),
            observed_at=row.get("observed_at"),
        )

    def get_llm_health_metrics(self) -> LLMHealthMetrics:
        data = self._execute(
            self.client.table("health_llm_metrics").select("*").limit(1),
            operation="get_llm_health_metrics",
        )

        row = self._first_or_none(data) or {}

        return LLMHealthMetrics(
            model=row.get("latest_model") or "gemini-2.5-flash",
            summaries_generated=row.get("summaries_generated") or 0,
            total_input_tokens=row.get("total_input_tokens") or 0,
            total_output_tokens=row.get("total_output_tokens") or 0,
            estimated_cost_usd=float(row.get("estimated_cost_usd") or 0),
        )
