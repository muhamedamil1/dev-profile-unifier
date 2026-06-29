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


class HealthDashboardService:
    """DB-backed observability read model for GET /health and /dashboard.

    Preferred source is the health_* views created in Phase 2. If a view is missing,
    the service degrades gracefully and returns warnings instead of leaking errors.
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
        api_rows = self._safe_select_view("health_api_call_metrics", warnings)
        github_rows = self._safe_select_view("health_latest_github_rate_limit", warnings)
        llm_rows = self._safe_select_view("health_llm_metrics", warnings)

        if include_raw:
            raw_views["health_profile_metrics"] = profile_rows
            raw_views["health_api_call_metrics"] = api_rows
            raw_views["health_latest_github_rate_limit"] = github_rows
            raw_views["health_llm_metrics"] = llm_rows

        return HealthDashboardResponse(
            status="degraded" if warnings else "ok",
            generated_at=datetime.now(timezone.utc),
            github_rate_limit=self._github_metrics(github_rows),
            external_api_calls=self._api_metrics(api_rows),
            llm_usage=self._llm_metrics(llm_rows, api_rows),
            resolution_metrics=self._resolution_metrics(profile_rows),
            raw_views=raw_views,
            warnings=warnings,
        )

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
        if not self.supabase_client:
            warnings.append(f"Supabase client unavailable; could not read {view_name}.")
            return []
        try:
            response = self.supabase_client.table(view_name).select("*").execute()
            data = getattr(response, "data", None)
            return list(data or []) if isinstance(data, list) else []
        except Exception as exc:  # noqa: BLE001 - health endpoint must degrade safely
            warnings.append(f"Could not read {view_name}: {type(exc).__name__}")
            return []

    @staticmethod
    def _github_metrics(rows: list[dict[str, Any]]) -> GitHubRateLimitMetrics:
        row = rows[0] if rows else {}
        return GitHubRateLimitMetrics(
            remaining=first_present(row.get("rate_limit_remaining"), row.get("remaining")),
            total=first_present(row.get("rate_limit_total"), row.get("total")),
            reset_at=first_present(row.get("rate_limit_reset_at"), row.get("reset_at")),
            last_checked_at=first_present(row.get("created_at"), row.get("last_checked_at")),
        )

    @staticmethod
    def _api_metrics(rows: list[dict[str, Any]]) -> list[SourceAPIMetrics]:
        metrics: list[SourceAPIMetrics] = []
        for row in rows:
            source = first_present(row.get("source"), row.get("metric_source"), "unknown")
            metrics.append(
                SourceAPIMetrics(
                    source=str(source),
                    total_calls=int_value(row.get("total_calls"), row.get("call_count")),
                    successful_calls=int_value(row.get("successful_calls"), row.get("success_count")),
                    failed_calls=int_value(row.get("failed_calls"), row.get("failure_count")),
                    avg_duration_ms=first_present(row.get("avg_duration_ms"), row.get("average_duration_ms")),
                    last_called_at=first_present(row.get("last_called_at"), row.get("latest_call_at")),
                )
            )
        return metrics

    @staticmethod
    def _llm_metrics(llm_rows: list[dict[str, Any]], api_rows: list[dict[str, Any]]) -> LLMMetrics:
        if llm_rows:
            row = llm_rows[0]
            return LLMMetrics(
                total_calls=int_value(row.get("total_calls"), row.get("call_count")),
                successful_calls=int_value(row.get("successful_calls"), row.get("success_count")),
                failed_calls=int_value(row.get("failed_calls"), row.get("failure_count")),
                input_tokens=int_value(row.get("input_tokens"), row.get("total_input_tokens")),
                output_tokens=int_value(row.get("output_tokens"), row.get("total_output_tokens")),
                estimated_cost_usd=float_value(row.get("estimated_cost_usd"), row.get("total_estimated_cost_usd")),
                retry_count=int_value(row.get("retry_count"), row.get("total_retry_count")),
                rate_limit_wait_ms=int_value(row.get("rate_limit_wait_ms"), row.get("total_rate_limit_wait_ms")),
            )

        retry_count = 0
        wait_ms = 0
        total = 0
        success = 0
        failed = 0
        input_tokens = 0
        output_tokens = 0
        estimated_cost = 0.0
        for row in api_rows:
            if str(row.get("source")) != "gemini":
                continue
            total += int_value(row.get("total_calls"), row.get("call_count"))
            success += int_value(row.get("successful_calls"), row.get("success_count"))
            failed += int_value(row.get("failed_calls"), row.get("failure_count"))
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            retry_count += int_value(metadata.get("retry_count"))
            wait_ms += int_value(metadata.get("rate_limit_wait_ms"))
            input_tokens += int_value(metadata.get("input_tokens"))
            output_tokens += int_value(metadata.get("output_tokens"))
            estimated_cost += float_value(metadata.get("estimated_cost_usd"))
        return LLMMetrics(
            total_calls=total,
            successful_calls=success,
            failed_calls=failed,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=estimated_cost,
            retry_count=retry_count,
            rate_limit_wait_ms=wait_ms,
        )

    @staticmethod
    def _resolution_metrics(rows: list[dict[str, Any]]) -> ResolutionMetrics:
        row = rows[0] if rows else {}
        return ResolutionMetrics(
            profiles_resolved=int_value(row.get("profiles_resolved"), row.get("canonical_profiles_count")),
            resolution_runs=int_value(row.get("resolution_runs"), row.get("total_resolution_runs")),
            resolved_runs=int_value(row.get("resolved_runs")),
            partial_runs=int_value(row.get("partial_runs")),
            failed_runs=int_value(row.get("failed_runs")),
            average_resolution_time_ms=first_present(row.get("average_resolution_time_ms"), row.get("avg_resolution_time_ms")),
        )

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
