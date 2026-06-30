from __future__ import annotations

from app.ui.pages import render_profile_page, render_sources_table


def test_sources_table_shows_claimed_input_reason_and_evidence_confidence():
    html = render_sources_table(
        [
            {
                "source": "hackernews",
                "handle": "simonw",
                "decision": "auto_match",
                "verification_status": "claimed_by_input",
                "confidence_score": 0.85,
                "decision_payload": {
                    "decision_basis": "anchor_input",
                    "accepted_as_anchor": True,
                    "hn_conservative": True,
                    "evidence_confidence_score": 0.25,
                    "decision_confidence_score": 0.85,
                },
            }
        ],
        empty="empty",
    )

    assert "auto match / claimed input" in html
    assert "0.85 \u00b7 evidence 0.25" in html
    assert "not external ownership verification" in html
    assert "Hacker News profiles are sparse" in html


def test_sources_table_keeps_unsafe_urls_blocked():
    html = render_sources_table(
        [
            {
                "source": "github",
                "handle": "simonw",
                "decision": "auto_match",
                "confidence_score": 0.95,
                "profile_url": "javascript:alert(1)",
                "reason": "safe reason",
            }
        ],
        empty="empty",
    )

    assert 'href="javascript:alert' not in html
    assert "safe reason" in html


def test_profile_page_subtitle_clarifies_claimed_input():
    html = render_profile_page(
        {
            "profile_id": "11111111-1111-1111-1111-111111111111",
            "display_name": "Simon Willison",
            "confidence_level": "medium",
            "sources": [
                {
                    "source": "github",
                    "handle": "simonw",
                    "decision": "auto_match",
                    "verification_status": "claimed_by_input",
                    "confidence_score": 0.85,
                    "reason": "User provided this github identifier; accepted as claimed input, not external ownership verification.",
                }
            ],
            "review_candidates": [],
            "rejected_candidates": [],
            "warnings": [],
            "ai_summary": {},
            "inferred_skills": [],
        }
    )

    assert "Claimed-input sources are user-provided anchors" in html
    assert "accepted as claimed input" in html
