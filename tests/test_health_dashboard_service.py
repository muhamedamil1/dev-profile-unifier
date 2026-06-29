from __future__ import annotations

from app.services.health_dashboard_service import HealthDashboardService


class FakeResponse:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, data):
        self.data = data

    def select(self, *_args, **_kwargs):
        return self

    def execute(self):
        return FakeResponse(self.data)


class FakeSupabase:
    def __init__(self, views):
        self.views = views

    def table(self, name):
        if name == "broken_view":
            raise RuntimeError("boom")
        return FakeQuery(self.views.get(name, []))


class FakeSettings:
    dashboard_token = "secret-token"


def test_health_dashboard_uses_observability_views():
    service = HealthDashboardService(
        supabase_client=FakeSupabase(
            {
                "health_profile_metrics": [
                    {
                        "profiles_resolved": 4,
                        "resolution_runs": 5,
                        "resolved_runs": 3,
                        "partial_runs": 1,
                        "failed_runs": 1,
                        "average_resolution_time_ms": 1200.5,
                    }
                ],
                "health_api_call_metrics": [
                    {
                        "source": "github",
                        "total_calls": 10,
                        "successful_calls": 9,
                        "failed_calls": 1,
                        "avg_duration_ms": 100.0,
                    }
                ],
                "health_latest_github_rate_limit": [
                    {"rate_limit_remaining": 42, "rate_limit_total": 60, "rate_limit_reset_at": "2026-06-29T10:00:00+00:00"}
                ],
                "health_llm_metrics": [
                    {
                        "total_calls": 2,
                        "successful_calls": 1,
                        "failed_calls": 1,
                        "input_tokens": 1000,
                        "output_tokens": 300,
                        "estimated_cost_usd": 0.0,
                        "retry_count": 2,
                        "rate_limit_wait_ms": 12000,
                    }
                ],
            }
        )
    )

    result = service.get_health()

    assert result.status == "ok"
    assert result.github_rate_limit.remaining == 42
    assert result.external_api_calls[0].source == "github"
    assert result.llm_usage.input_tokens == 1000
    assert result.llm_usage.retry_count == 2
    assert result.resolution_metrics.profiles_resolved == 4
    assert result.raw_views == {}


def test_health_dashboard_preserves_zero_values_and_can_include_raw_views():
    service = HealthDashboardService(
        supabase_client=FakeSupabase(
            {
                "health_profile_metrics": [
                    {"profiles_resolved": 0, "resolution_runs": 0, "resolved_runs": 0, "partial_runs": 0, "failed_runs": 0}
                ],
                "health_api_call_metrics": [
                    {"source": "github", "total_calls": 0, "successful_calls": 0, "failed_calls": 0, "avg_duration_ms": 0}
                ],
                "health_latest_github_rate_limit": [
                    {"rate_limit_remaining": 0, "rate_limit_total": 60, "rate_limit_reset_at": None}
                ],
                "health_llm_metrics": [
                    {"total_calls": 0, "input_tokens": 0, "output_tokens": 0, "retry_count": 0, "rate_limit_wait_ms": 0}
                ],
            }
        )
    )

    result = service.get_health(include_raw=True)

    assert result.github_rate_limit.remaining == 0
    assert result.external_api_calls[0].total_calls == 0
    assert result.external_api_calls[0].avg_duration_ms == 0
    assert result.llm_usage.total_calls == 0
    assert "health_latest_github_rate_limit" in result.raw_views


def test_health_dashboard_degrades_when_supabase_missing():
    result = HealthDashboardService(supabase_client=None).get_health()

    assert result.status == "degraded"
    assert result.warnings
    assert result.external_api_calls == []


def test_html_dashboard_renders_useful_sections_and_escapes_dynamic_values():
    service = HealthDashboardService(
        supabase_client=FakeSupabase(
            {
                "health_profile_metrics": [],
                "health_api_call_metrics": [
                    {"source": "<script>alert(1)</script>", "total_calls": 1, "successful_calls": 1, "failed_calls": 0}
                ],
                "health_latest_github_rate_limit": [],
                "health_llm_metrics": [],
            }
        )
    )

    html = service.render_html_dashboard()

    assert "Dev Profile Unifier Dashboard" in html
    assert "GitHub Rate Limit" in html
    assert "LLM Usage" in html
    assert "Resolution Metrics" in html
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_dashboard_token_validation_supports_query_and_bearer_token():
    service = HealthDashboardService(supabase_client=None, settings=FakeSettings())

    assert service.dashboard_token_configured() is True
    assert service.validate_dashboard_token(token="secret-token") is True
    assert service.validate_dashboard_token(authorization="Bearer secret-token") is True
    assert service.validate_dashboard_token(token="wrong") is False
    assert service.validate_dashboard_token() is False
