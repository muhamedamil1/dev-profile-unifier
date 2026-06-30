from __future__ import annotations

from app.ui.components import warnings_panel
from app.ui.pages import render_dashboard_page, render_profile_page, render_resolve_page


def test_resolve_page_contains_production_resolve_endpoint():
    html = render_resolve_page(recent_health={"status": "healthy", "generated_at": "2026-06-30T00:00:00Z"})

    assert "Dev Profile Unifier" in html
    assert "POST /profiles/resolve" in html
    assert "fetch('/profiles/resolve'" in html
    assert "Resolve Profile" in html


def test_profile_page_escapes_untrusted_profile_values():
    malicious = '<script>alert("owned")</script>'
    html = render_profile_page(
        {
            "profile_id": "profile-1",
            "resolution_run_id": "run-1",
            "display_name": malicious,
            "headline": "<img src=x onerror=alert(1)>",
            "confidence_level": "high",
            "primary_website_url": "javascript:alert(1)",
            "sources": [
                {
                    "source": "github",
                    "handle": malicious,
                    "decision": "auto_match",
                    "confidence_score": 0.95,
                    "profile_url": "https://github.com/amil122",
                    "reason": malicious,
                }
            ],
            "review_candidates": [],
            "rejected_candidates": [],
            "ai_summary": {
                "headline": malicious,
                "short_summary": malicious,
                "strengths": [malicious],
                "source_note": malicious,
                "limitations": [malicious],
            },
            "warnings": [malicious],
        }
    )

    assert malicious not in html
    assert "&lt;script&gt;alert" in html
    assert 'href="javascript:alert' not in html
    assert ">Website:" not in html
    assert "https://github.com/amil122" in html


def test_dashboard_preserves_zero_rate_limit_remaining():
    html = render_dashboard_page(
        {
            "status": "healthy",
            "generated_at": "2026-06-30T00:00:00Z",
            "github_rate_limit": {
                "remaining": 0,
                "total": 60,
                "reset_at": "2026-06-30T01:00:00Z",
            },
            "llm_usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "retry_count": 0,
                "rate_limit_wait_ms": 0,
                "estimated_cost_usd": 0.0,
            },
            "profile_metrics": {"profiles_resolved": 0, "average_resolution_time_ms": 0},
            "api_calls_by_source": [],
            "warnings": [],
        }
    )

    assert "0 / 60" in html
    assert "GitHub quota" in html



def test_dashboard_accepts_health_response_field_names():
    html = render_dashboard_page(
        {
            "status": "ok",
            "github_rate_limit": {"remaining": 12, "total": 60},
            "llm_usage": {"input_tokens": 10, "output_tokens": 5},
            "resolution_metrics": {"profiles_resolved": 3, "average_resolution_time_ms": 25},
            "external_api_calls": [
                {"source": "github", "total_calls": 4, "successful_calls": 3, "failed_calls": 1, "avg_duration_ms": 80}
            ],
        }
    )

    assert "3" in html
    assert "25 ms" in html
    assert "15" in html
    assert "12 / 60" in html
    assert "github" in html
    assert "80" in html

def test_dashboard_escapes_warnings_and_raw_json():
    malicious = '<script>alert("x")</script>'
    html = render_dashboard_page(
        {
            "status": "degraded",
            "warnings": [malicious],
            "github_rate_limit": {},
            "llm_usage": {},
            "profile_metrics": {},
            "api_calls_by_source": [{"source": malicious, "total_calls": 1}],
            "raw_views": {"bad": malicious},
        },
        include_raw=True,
    )

    assert malicious not in html
    assert "&lt;script&gt;alert" in html


def test_resolve_page_handles_validation_and_non_json_errors():
    html = render_resolve_page(recent_health={"status": "healthy"})

    assert "parseResponsePayload" in html
    assert "errorMessageFromPayload" in html
    assert "Array.isArray(data.detail)" in html
    assert "response.text()" in html
    assert "JSON.parse(text)" in html


def test_profile_page_url_encodes_raw_api_link_and_uses_sanitized_avatar_url():
    html = render_profile_page(
        {
            "profile_id": "profile id/with slash?x=1",
            "display_name": "Jane Doe",
            "headline": "Engineer",
            "confidence_level": "medium",
            "primary_avatar_url": "javascript:alert(1)",
            "sources": [],
            "review_candidates": [],
            "rejected_candidates": [],
            "ai_summary": {},
            "warnings": [],
        }
    )

    assert "/profiles/profile%20id%2Fwith%20slash%3Fx%3D1" in html
    assert 'src="javascript:alert' not in html
    assert ">JD<" in html


def test_profile_page_renders_sanitized_avatar_url_only():
    html = render_profile_page(
        {
            "profile_id": "profile-1",
            "display_name": "Jane Doe",
            "headline": "Engineer",
            "confidence_level": "high",
            "primary_avatar_url": "https://example.com/avatar.png",
            "sources": [],
            "review_candidates": [],
            "rejected_candidates": [],
            "ai_summary": {},
            "warnings": [],
        }
    )

    assert 'src="https://example.com/avatar.png"' in html


def test_profile_warning_renderer_combines_review_warnings_without_raw_dict_syntax():
    html = warnings_panel(
        [
            {
                "code": "canonical_fields_pending",
                "message": "Canonical fields are still pending and may be incomplete.",
                "details": {},
            },
            {
                "code": "ambiguous_candidates_present",
                "message": "Some source accounts were left for review.",
                "details": {"count": 2},
            },
        ]
    )

    assert "Profile needs review" in html
    assert "2 possible matching accounts" in html
    assert "2 candidate accounts need review" in html
    assert "{'code':" not in html
    assert "canonical_fields_pending" not in html


def test_profile_warning_renderer_uses_review_candidate_count_detail():
    html = warnings_panel(
        [
            {
                "code": "profile_needs_review",
                "message": "This profile needs review before canonical fields can be finalized.",
                "details": {"reason": "no_auto_match_accounts", "review_candidate_count": 1},
            }
        ]
    )

    assert "Profile needs review" in html
    assert "1 candidate account needs review" in html
    assert "{'code':" not in html


def test_profile_warning_renderer_keeps_unknown_warning_as_clean_message():
    html = warnings_panel(
        [
            {
                "code": "unexpected_warning",
                "message": "Review <script>alert(1)</script> manually.",
                "details": {"debug": "hidden"},
            }
        ]
    )

    assert "Review &lt;script&gt;alert" in html
    assert "{'code':" not in html
    assert "unexpected_warning" not in html
    assert "hidden" not in html


def test_profile_page_renders_uncertain_shell_without_accepted_sources():
    html = render_profile_page(
        {
            "profile_id": "profile-uncertain",
            "display_name": None,
            "headline": None,
            "confidence_level": "uncertain",
            "profile_stage": "canonical_build_blocked",
            "canonical_fields_pending": True,
            "resolution_summary": {"outcome": "ambiguous_candidates"},
            "sources": [],
            "review_candidates": [
                {
                    "source": "github",
                    "handle": "benhalpern",
                    "decision": "needs_review",
                    "confidence_score": 0.62,
                    "reason": "name-only candidate requires review",
                }
            ],
            "rejected_candidates": [],
            "ai_summary": {},
            "warnings": [
                {
                    "code": "profile_needs_review",
                    "message": "No confident canonical profile was created.",
                    "details": {"review_candidate_count": 1},
                }
            ],
        }
    )

    assert "No confident canonical profile yet" in html
    assert "Review the candidates below" in html
    assert "No accepted sources were returned for this profile." in html
    assert "benhalpern" in html
    assert "uncertain" in html
