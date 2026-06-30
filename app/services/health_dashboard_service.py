from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from typing import Any

from app.schemas.observability import (
    GitHubRateLimitMetrics,
    HealthDashboardResponse,
    LLMMetrics,
    ResolutionMetrics,
    SourceAPIMetrics,
)

EXTERNAL_SOURCES: tuple[str, ...] = ("github", "stackoverflow", "devto", "hackernews")
LLM_SOURCE = "gemini"


def first_present(*values: Any) -> Any:
    """Return the first value that is not None, preserving valid zeros."""

    for value in values:
        if value is not None:
            return value
    return None


def int_value(*values: Any, default: int = 0) -> int:
    value = first_present(*values)
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def float_value(*values: Any, default: float = 0.0) -> float:
    value = first_present(*values)
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _metric_failed(row: dict[str, Any]) -> bool:
    """Return whether one api_call_metrics row represents a failed attempt."""

    if row.get("error_message"):
        return True

    status_code = row.get("status_code")
    if status_code is None:
        return True

    try:
        return int(status_code) >= 400
    except (TypeError, ValueError):
        return True


class HealthDashboardService:
    """DB-backed observability read model for GET /health and /dashboard.

    The dashboard supports both the original Phase 2 health views and the later
    api_call_metrics metadata added for Gemini/rate-limit observability.

    Important compatibility detail: early views expose columns such as `total`,
    `errors`, `limit`, and `resolved_total`, while later UI/service code used
    names such as `total_calls`, `failed_calls`, `rate_limit_total`, and
    `profiles_resolved`. This service intentionally accepts both shapes and uses
    direct table rows when available so the frontend shows the real DB values.
    """

    def __init__(
        self,
        *,
        supabase_client=None,
        metrics_repo=None,
        resolution_runs_repo=None,
        settings=None,
    ) -> None:
        self.supabase_client = supabase_client
        self.metrics_repo = metrics_repo
        self.resolution_runs_repo = resolution_runs_repo
        self.settings = settings

    def get_health(self, *, include_raw: bool = False) -> HealthDashboardResponse:
        warnings: list[str] = []
        raw_views: dict[str, Any] = {}

        profile_rows = self._safe_select_view("health_profile_metrics", warnings)
        api_view_rows = self._safe_select_view("health_api_call_metrics", warnings)
        github_rows = self._safe_select_view("health_latest_github_rate_limit", warnings)
        llm_rows = self._safe_select_view("health_llm_metrics", warnings)

        # Direct table rows are used to fill metrics that the older views cannot
        # express, especially Gemini retry/wait metadata and exact source call
        # success/failure counts.
        api_metric_rows = self._safe_select_table(
            "api_call_metrics",
            warnings,
            columns=(
                "source,status_code,duration_ms,error_message,"
                "rate_limit_remaining,rate_limit_total,rate_limit_reset_at,metadata,created_at"
            ),
        )
        resolution_run_rows = self._safe_select_table(
            "resolution_runs",
            warnings,
            columns="status,duration_ms,completed_at,created_at",
        )
        canonical_profile_rows = self._safe_select_table(
            "canonical_profiles",
            warnings,
            columns="id,created_at",
        )
        llm_summary_rows = self._safe_select_table(
            "llm_summaries",
            warnings,
            columns="input_tokens,output_tokens,estimated_cost_usd,created_at",
        )

        if include_raw:
            raw_views["health_profile_metrics"] = profile_rows
            raw_views["health_api_call_metrics"] = api_view_rows
            raw_views["health_latest_github_rate_limit"] = github_rows
            raw_views["health_llm_metrics"] = llm_rows
            raw_views["api_call_metrics"] = api_metric_rows
            raw_views["resolution_runs"] = resolution_run_rows
            raw_views["canonical_profiles"] = canonical_profile_rows
            raw_views["llm_summaries"] = llm_summary_rows

        return HealthDashboardResponse(
            status="degraded" if warnings else "ok",
            generated_at=datetime.now(timezone.utc),
            github_rate_limit=self._github_metrics(github_rows, api_metric_rows),
            external_api_calls=self._api_metrics(api_view_rows, api_metric_rows),
            llm_usage=self._llm_metrics(llm_rows, api_metric_rows, llm_summary_rows),
            resolution_metrics=self._resolution_metrics(profile_rows, resolution_run_rows, canonical_profile_rows),
            raw_views=raw_views,
            warnings=warnings,
        )

    def get_health_snapshot(self, *, include_raw: bool = False) -> HealthDashboardResponse:
        """Compatibility alias used by the UI route."""

        return self.get_health(include_raw=include_raw)

    def dashboard_token_configured(self) -> bool:
        return bool(self._setting("dashboard_token", "DASHBOARD_TOKEN"))

    def validate_dashboard_token(self, *, token: str | None = None, authorization: str | None = None) -> bool:
        expected = self._setting("dashboard_token", "DASHBOARD_TOKEN")
        if not expected:
            return True

        provided = token
        if not provided and authorization:
            scheme, _, credentials = authorization.partition(" ")
            if scheme.lower() == "bearer" and credentials:
                provided = credentials.strip()

        return provided == expected

    def render_html_dashboard(self) -> str:
        health = self.get_health(include_raw=False)
        external_rows = "".join(
            "<tr>"
            f"<td>{self._h(m.source)}</td>"
            f"<td>{m.total_calls}</td>"
            f"<td>{m.successful_calls}</td>"
            f"<td>{m.failed_calls}</td>"
            f"<td>{self._h(m.avg_duration_ms if m.avg_duration_ms is not None else '')}</td>"
            f"<td>{self._h(m.last_called_at if m.last_called_at is not None else '')}</td>"
            "</tr>"
            for m in health.external_api_calls
        )
        warnings = "".join(f"<li>{self._h(warning)}</li>" for warning in health.warnings)
        return f"""
<!doctype html>
<html>
<head>
  <meta charset=\"utf-8\" />
  <title>Dev Profile Unifier Dashboard</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 2rem; color: #111; }}
    table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; }}
    th, td {{ border: 1px solid #ddd; padding: 0.5rem; text-align: left; }}
    th {{ background: #f5f5f5; }}
    code {{ background: #f5f5f5; padding: 0.1rem 0.25rem; }}
  </style>
</head>
<body>
  <h1>Dev Profile Unifier Dashboard</h1>
  <p>Status: <strong>{self._h(health.status)}</strong> — generated at {self._h(health.generated_at)}</p>

  <h2>GitHub Rate Limit</h2>
  <p>Remaining: <strong>{self._h(health.github_rate_limit.remaining)}</strong> / {self._h(health.github_rate_limit.total)}</p>
  <p>Reset at: {self._h(health.github_rate_limit.reset_at)}</p>

  <h2>External API Calls</h2>
  <table>
    <tr><th>Source</th><th>Total</th><th>Success</th><th>Failed</th><th>Avg ms</th><th>Last called</th></tr>
    {external_rows}
  </table>

  <h2>LLM Usage</h2>
  <ul>
    <li>Total calls: {health.llm_usage.total_calls}</li>
    <li>Successful calls: {health.llm_usage.successful_calls}</li>
    <li>Failed calls: {health.llm_usage.failed_calls}</li>
    <li>Input tokens: {health.llm_usage.input_tokens}</li>
    <li>Output tokens: {health.llm_usage.output_tokens}</li>
    <li>Estimated cost USD: {health.llm_usage.estimated_cost_usd}</li>
    <li>Retries: {health.llm_usage.retry_count}</li>
    <li>Rate-limit wait ms: {health.llm_usage.rate_limit_wait_ms}</li>
  </ul>

  <h2>Resolution Metrics</h2>
  <ul>
    <li>Profiles resolved: {health.resolution_metrics.profiles_resolved}</li>
    <li>Resolution runs: {health.resolution_metrics.resolution_runs}</li>
    <li>Average resolution time ms: {self._h(health.resolution_metrics.average_resolution_time_ms)}</li>
  </ul>

  <h2>Warnings</h2>
  <ul>{warnings}</ul>
</body>
</html>
"""

    def _safe_select_view(self, view_name: str, warnings: list[str]) -> list[dict[str, Any]]:
        return self._safe_select_table(view_name, warnings, columns="*", label=view_name)

    def _safe_select_table(
        self,
        table_name: str,
        warnings: list[str],
        *,
        columns: str = "*",
        label: str | None = None,
    ) -> list[dict[str, Any]]:
        label = label or table_name
        if not self.supabase_client:
            warnings.append(f"Supabase client unavailable; could not read {label}.")
            return []
        try:
            response = self.supabase_client.table(table_name).select(columns).execute()
            data = getattr(response, "data", None)
            return list(data or []) if isinstance(data, list) else []
        except Exception as exc:  # noqa: BLE001 - health endpoint must degrade safely
            warnings.append(f"Could not read {label}: {type(exc).__name__}")
            return []

    @staticmethod
    def _github_metrics(rows: list[dict[str, Any]], api_metric_rows: list[dict[str, Any]] | None = None) -> GitHubRateLimitMetrics:
        api_metric_rows = api_metric_rows or []
        latest_metric = HealthDashboardService._latest_github_rate_limit_row(api_metric_rows)
        row = latest_metric or (rows[0] if rows else {})
        return GitHubRateLimitMetrics(
            remaining=first_present(row.get("rate_limit_remaining"), row.get("remaining")),
            total=first_present(row.get("rate_limit_total"), row.get("total"), row.get("limit")),
            reset_at=first_present(row.get("rate_limit_reset_at"), row.get("reset_at")),
            last_checked_at=first_present(row.get("created_at"), row.get("observed_at"), row.get("last_checked_at")),
        )

    @staticmethod
    def _api_metrics(rows: list[dict[str, Any]], api_metric_rows: list[dict[str, Any]] | None = None) -> list[SourceAPIMetrics]:
        api_metric_rows = api_metric_rows or []
        direct_metrics = HealthDashboardService._api_metrics_from_raw_rows(api_metric_rows)
        if direct_metrics:
            return direct_metrics

        metrics: list[SourceAPIMetrics] = []
        for row in rows:
            source = first_present(row.get("source"), row.get("metric_source"), "unknown")
            total_calls = int_value(row.get("total_calls"), row.get("call_count"), row.get("total"))
            failed_calls = int_value(row.get("failed_calls"), row.get("failure_count"), row.get("errors"), row.get("error_count"))
            successful_calls = int_value(
                row.get("successful_calls"),
                row.get("success_count"),
                default=max(total_calls - failed_calls, 0),
            )
            metrics.append(
                SourceAPIMetrics(
                    source=str(source),
                    total_calls=total_calls,
                    successful_calls=successful_calls,
                    failed_calls=failed_calls,
                    avg_duration_ms=first_present(row.get("avg_duration_ms"), row.get("average_duration_ms")),
                    last_called_at=first_present(row.get("last_called_at"), row.get("latest_call_at")),
                )
            )
        return metrics

    @staticmethod
    def _api_metrics_from_raw_rows(api_metric_rows: list[dict[str, Any]]) -> list[SourceAPIMetrics]:
        aggregates: dict[str, dict[str, Any]] = {
            source: {
                "total": 0,
                "failed": 0,
                "duration_sum": 0,
                "duration_count": 0,
                "last_called_at": None,
            }
            for source in EXTERNAL_SOURCES
        }

        saw_external_row = False
        for row in api_metric_rows:
            source = str(row.get("source") or "unknown")
            if source == LLM_SOURCE:
                continue
            if source not in aggregates:
                aggregates[source] = {
                    "total": 0,
                    "failed": 0,
                    "duration_sum": 0,
                    "duration_count": 0,
                    "last_called_at": None,
                }
            saw_external_row = True
            bucket = aggregates[source]
            bucket["total"] += 1
            if _metric_failed(row):
                bucket["failed"] += 1

            duration_ms = row.get("duration_ms")
            if duration_ms is not None:
                bucket["duration_sum"] += float_value(duration_ms)
                bucket["duration_count"] += 1

            created_at = row.get("created_at")
            if created_at is not None:
                current = bucket.get("last_called_at")
                if current is None or str(created_at) > str(current):
                    bucket["last_called_at"] = created_at

        if not saw_external_row:
            return []

        metrics: list[SourceAPIMetrics] = []
        for source in sorted(aggregates):
            bucket = aggregates[source]
            total_calls = int(bucket["total"])
            failed_calls = int(bucket["failed"])
            avg_duration_ms = None
            if bucket["duration_count"]:
                avg_duration_ms = round(bucket["duration_sum"] / bucket["duration_count"], 2)
            metrics.append(
                SourceAPIMetrics(
                    source=source,
                    total_calls=total_calls,
                    successful_calls=max(total_calls - failed_calls, 0),
                    failed_calls=failed_calls,
                    avg_duration_ms=avg_duration_ms,
                    last_called_at=bucket["last_called_at"],
                )
            )
        return metrics

    @staticmethod
    def _llm_metrics(
        llm_rows: list[dict[str, Any]],
        api_metric_rows: list[dict[str, Any]] | None = None,
        llm_summary_rows: list[dict[str, Any]] | None = None,
    ) -> LLMMetrics:
        api_metric_rows = api_metric_rows or []
        llm_summary_rows = llm_summary_rows or []
        gemini_rows = [row for row in api_metric_rows if str(row.get("source")) == LLM_SOURCE]

        summary_totals = HealthDashboardService._llm_summary_totals(llm_rows, llm_summary_rows)
        api_totals = HealthDashboardService._llm_api_totals(gemini_rows)

        total_calls = api_totals["total_calls"] or summary_totals["total_calls"]
        successful_calls = api_totals["successful_calls"] or summary_totals["total_calls"]
        failed_calls = api_totals["failed_calls"]
        input_tokens = summary_totals["input_tokens"] or api_totals["input_tokens"]
        output_tokens = summary_totals["output_tokens"] or api_totals["output_tokens"]
        estimated_cost_usd = summary_totals["estimated_cost_usd"] or api_totals["estimated_cost_usd"]

        return LLMMetrics(
            total_calls=int(total_calls),
            successful_calls=int(successful_calls),
            failed_calls=int(failed_calls),
            input_tokens=int(input_tokens),
            output_tokens=int(output_tokens),
            estimated_cost_usd=float(estimated_cost_usd),
            retry_count=int(api_totals["retry_count"]),
            rate_limit_wait_ms=int(api_totals["rate_limit_wait_ms"]),
        )

    @staticmethod
    def _llm_summary_totals(llm_rows: list[dict[str, Any]], llm_summary_rows: list[dict[str, Any]]) -> dict[str, float | int]:
        if llm_summary_rows:
            return {
                "total_calls": len(llm_summary_rows),
                "input_tokens": sum(int_value(row.get("input_tokens")) for row in llm_summary_rows),
                "output_tokens": sum(int_value(row.get("output_tokens")) for row in llm_summary_rows),
                "estimated_cost_usd": sum(float_value(row.get("estimated_cost_usd")) for row in llm_summary_rows),
            }

        if llm_rows:
            row = llm_rows[0]
            return {
                "total_calls": int_value(row.get("total_calls"), row.get("call_count"), row.get("summaries_generated")),
                "input_tokens": int_value(row.get("input_tokens"), row.get("total_input_tokens")),
                "output_tokens": int_value(row.get("output_tokens"), row.get("total_output_tokens")),
                "estimated_cost_usd": float_value(row.get("estimated_cost_usd"), row.get("total_estimated_cost_usd")),
            }

        return {"total_calls": 0, "input_tokens": 0, "output_tokens": 0, "estimated_cost_usd": 0.0}

    @staticmethod
    def _llm_api_totals(gemini_rows: list[dict[str, Any]]) -> dict[str, float | int]:
        total_calls = len(gemini_rows)
        failed_calls = sum(1 for row in gemini_rows if _metric_failed(row))
        input_tokens = 0
        output_tokens = 0
        estimated_cost_usd = 0.0
        retry_count = 0
        rate_limit_wait_ms = 0

        for row in gemini_rows:
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            retry_count += int_value(metadata.get("retry_count"), metadata.get("total_retry_count"))
            rate_limit_wait_ms += int_value(metadata.get("rate_limit_wait_ms"), metadata.get("total_rate_limit_wait_ms"))
            input_tokens += int_value(metadata.get("input_tokens"), metadata.get("total_input_tokens"))
            output_tokens += int_value(metadata.get("output_tokens"), metadata.get("total_output_tokens"))
            estimated_cost_usd += float_value(metadata.get("estimated_cost_usd"), metadata.get("total_estimated_cost_usd"))

        return {
            "total_calls": total_calls,
            "successful_calls": max(total_calls - failed_calls, 0),
            "failed_calls": failed_calls,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "estimated_cost_usd": estimated_cost_usd,
            "retry_count": retry_count,
            "rate_limit_wait_ms": rate_limit_wait_ms,
        }

    @staticmethod
    def _resolution_metrics(
        rows: list[dict[str, Any]],
        resolution_run_rows: list[dict[str, Any]] | None = None,
        canonical_profile_rows: list[dict[str, Any]] | None = None,
    ) -> ResolutionMetrics:
        resolution_run_rows = resolution_run_rows or []
        canonical_profile_rows = canonical_profile_rows or []

        if resolution_run_rows or canonical_profile_rows:
            total_runs = len(resolution_run_rows)
            resolved_runs = sum(1 for row in resolution_run_rows if row.get("status") == "resolved")
            partial_runs = sum(1 for row in resolution_run_rows if row.get("status") == "partial")
            failed_runs = sum(1 for row in resolution_run_rows if row.get("status") == "failed")
            duration_values = [
                int_value(row.get("duration_ms"))
                for row in resolution_run_rows
                if row.get("status") in {"resolved", "partial"} and row.get("duration_ms") is not None
            ]
            avg_duration = round(sum(duration_values) / len(duration_values), 2) if duration_values else 0
            return ResolutionMetrics(
                profiles_resolved=len(canonical_profile_rows),
                resolution_runs=total_runs,
                resolved_runs=resolved_runs,
                partial_runs=partial_runs,
                failed_runs=failed_runs,
                average_resolution_time_ms=avg_duration,
            )

        row = rows[0] if rows else {}
        resolved_runs = int_value(row.get("resolved_runs"), row.get("resolved_total"))
        partial_runs = int_value(row.get("partial_runs"), row.get("partial_total"))
        failed_runs = int_value(row.get("failed_runs"), row.get("failed_total"))
        resolution_runs = int_value(row.get("resolution_runs"), row.get("total_resolution_runs"), default=resolved_runs + partial_runs + failed_runs)
        return ResolutionMetrics(
            profiles_resolved=int_value(row.get("profiles_resolved"), row.get("canonical_profiles_count"), row.get("resolved_total")),
            resolution_runs=resolution_runs,
            resolved_runs=resolved_runs,
            partial_runs=partial_runs,
            failed_runs=failed_runs,
            average_resolution_time_ms=first_present(row.get("average_resolution_time_ms"), row.get("avg_resolution_time_ms")),
        )

    @staticmethod
    def _latest_github_rate_limit_row(api_metric_rows: list[dict[str, Any]]) -> dict[str, Any] | None:
        candidates = [
            row
            for row in api_metric_rows
            if str(row.get("source")) == "github"
            and row.get("rate_limit_remaining") is not None
            and row.get("rate_limit_total") is not None
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda row: str(row.get("created_at") or ""))

    def _setting(self, *names: str) -> Any:
        if not self.settings:
            return None
        for name in names:
            value = getattr(self.settings, name, None)
            if value:
                return value
        return None

    @staticmethod
    def _h(value: Any) -> str:
        if value is None:
            return ""
        return escape(str(value), quote=True)
