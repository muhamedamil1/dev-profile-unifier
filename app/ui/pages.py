from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from app.ui.components import (
    badge,
    badge_cell,
    card,
    dict_list,
    empty_state,
    json_details,
    link_cell,
    list_chips,
    metric_card,
    simple_table,
    warnings_panel,
)
from app.ui.html import field, h, safe_link, safe_url, to_plain
from app.ui.styles import APP_CSS


def render_layout(*, title: str, active: str, content: str) -> str:
    def nav_link(label: str, href: str, key: str) -> str:
        active_class = " active" if active == key else ""
        return f'<a class="nav-link{active_class}" href="{h(href)}">{h(label)}</a>'

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{h(title)} Â· Dev Profile Unifier</title>
  <style>{APP_CSS}</style>
</head>
<body>
  <div class="app-shell">
    <header class="topbar">
      <nav class="nav" aria-label="Primary navigation">
        <a class="brand" href="/app" style="text-decoration:none;color:inherit;">
          <span class="brand-mark">D</span>
          <span>Dev Profile Unifier</span>
        </a>
        <div class="nav-links">
          {nav_link("Resolve", "/app", "resolve")}
          {nav_link("Dashboard", "/dashboard", "dashboard")}
          {nav_link("Health", "/health", "health")}
          {nav_link("API Docs", "/docs", "docs")}
        </div>
      </nav>
    </header>
    <main class="container">
      {content}
      <footer class="footer">Deterministic-first identity resolution with optional Gemini review and grounded profile summaries.</footer>
    </main>
  </div>
</body>
</html>"""


def render_resolve_page(*, recent_health: Any | None = None) -> str:
    health = to_plain(recent_health) or {}
    status = field(health, "status", default="ready")
    generated_at = field(health, "generated_at", default=None)

    hero = f"""
    <section class="hero">
      <div class="hero-copy">
        <div class="kicker">Evidence-based developer identity resolution</div>
        <h1>Unify public developer profiles without blind merging.</h1>
        <p>Resolve GitHub, Stack Overflow, dev.to, and Hacker News identities into a canonical profile using deterministic evidence, conflict checks, optional Gemini review for ambiguous matches, and grounded AI summaries.</p>
        <div class="button-row">
          <a class="button" href="#resolve-form">Resolve a profile</a>
          <a class="button secondary" href="/dashboard">View observability</a>
        </div>
      </div>
      <aside class="card" aria-label="System snapshot">
        <div class="card-header"><div class="card-title"><h2>System snapshot</h2><div class="subtitle">Useful for demo and operations checks.</div></div>{badge(status, str(status))}</div>
        <div class="grid grid-2">
          {metric_card("Health", status)}
          {metric_card("Generated", generated_at or "Live", note="Dashboard reads /health")}
        </div>
        <div class="button-row"><a class="button ghost" href="/health">Open JSON health</a><a class="button ghost" href="/docs">Open API docs</a></div>
      </aside>
    </section>
    """

    form = """
    <form id="resolve-form" class="card">
      <div class="card-header">
        <div class="card-title">
          <h2>Resolve developer profile</h2>
          <div class="subtitle">Provide at least one known handle. More fields improve evidence quality.</div>
        </div>
      </div>
      <div class="form-grid">
        <div class="field"><label for="name">Name</label><input id="name" name="name" placeholder="John Doe" autocomplete="name" /></div>
        <div class="field"><label for="github">GitHub handle</label><input id="github" name="github" placeholder="jonedoe11" autocomplete="off" /></div>
        <div class="field"><label for="stackoverflow">Stack Overflow user ID</label><input id="stackoverflow" name="stackoverflow" placeholder="1234567" autocomplete="off" /></div>
        <div class="field"><label for="devto">dev.to handle</label><input id="devto" name="devto" placeholder="johndoe11" autocomplete="off" /></div>
        <div class="field"><label for="hackernews">Hacker News handle</label><input id="hackernews" name="hackernews" placeholder="johnh" autocomplete="off" /></div>
      </div>
      <div class="button-row">
        <button class="button" type="submit" id="resolve-button">Resolve Profile</button>
        <span class="muted">The UI calls the production POST /profiles/resolve endpoint.</span>
      </div>
      <div id="resolve-status" class="status-box" role="status" aria-live="polite"></div>
    </form>
    <script>
      const form = document.getElementById('resolve-form');
      const statusBox = document.getElementById('resolve-status');
      const button = document.getElementById('resolve-button');
      function setStatus(message, kind) {
        statusBox.textContent = message;
        statusBox.className = 'status-box show ' + (kind || '');
      }
      async function parseResponsePayload(response) {
        const text = await response.text();
        if (!text) return {};
        try {
          return JSON.parse(text);
        } catch (_) {
          return {message: text};
        }
      }
      function errorMessageFromPayload(data) {
        if (!data) return 'Resolve request failed.';
        if (typeof data.detail === 'string') return data.detail;
        if (Array.isArray(data.detail)) {
          const messages = data.detail.map((item) => {
            if (!item) return '';
            if (typeof item === 'string') return item;
            if (item.msg) return item.msg;
            if (item.message) return item.message;
            try { return JSON.stringify(item); } catch (_) { return String(item); }
          }).filter(Boolean);
          if (messages.length) return messages.join(' ');
        }
        if (typeof data.public_message === 'string') return data.public_message;
        if (typeof data.message === 'string') return data.message;
        if (typeof data.error === 'string') return data.error;
        return 'Resolve request failed.';
      }
      form.addEventListener('submit', async (event) => {
        event.preventDefault();
        const formData = new FormData(form);
        const payload = {};
        const fieldMap = {
          name: 'name',
          github: 'github',
          stackoverflow: 'stackoverflow_user_id',
          devto: 'devto',
          hackernews: 'hackernews',
        };
        for (const [key, value] of formData.entries()) {
          const text = String(value || '').trim();
          const apiKey = fieldMap[key] || key;
          if (text) payload[apiKey] = text;
        }
        if (!Object.keys(payload).length) {
          setStatus('Add at least one name or platform handle before resolving.', 'error');
          return;
        }
        button.disabled = true;
        button.textContent = 'Resolving...';
        setStatus('Resolving profile. This may call external APIs and Gemini depending on configuration.', '');
        try {
          const response = await fetch('/profiles/resolve', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
          });
          const data = await parseResponsePayload(response);
          if (!response.ok) {
            throw new Error(errorMessageFromPayload(data));
          }
          const profileId = data.profile_id || data.id;
          if (!profileId) {
            setStatus('Resolve completed but no profile_id was returned. Open /docs to inspect the API response.', 'error');
            return;
          }
          setStatus('Profile resolved. Opening result page...', 'success');
          window.location.href = '/app/profiles/' + encodeURIComponent(profileId);
        } catch (error) {
          setStatus(error.message || 'Unexpected resolve error.', 'error');
        } finally {
          button.disabled = false;
          button.textContent = 'Resolve Profile';
        }
      });
    </script>
    """

    info_cards = f"""
    <section class="grid grid-3">
      {card("Deterministic first", "<p>Clear matches and clear rejects are handled by evidence, conflicts, and scoring before any LLM is involved.</p>")}
      {card("Gemini only when useful", "<p>The frontend never calls Gemini directly. It uses the backend, where Gemini is rate-limited, retried, and monitored.</p>")}
      {card("Production visibility", "<p>Use the dashboard to inspect API calls, GitHub rate limit, LLM tokens, retry waits, and resolution timing.</p>")}
    </section>
    """
    return render_layout(title="Resolve", active="resolve", content=hero + form + info_cards)


def render_profile_page(profile: Any) -> str:
    data = to_plain(profile) or {}
    profile_id = field(data, "profile_id", "id", default="unknown")
    display_name = field(data, "display_name", default="Unnamed profile")
    headline = field(data, "headline", default="No headline available")
    confidence = field(data, "confidence_level", default="unknown")
    location = field(data, "location", default=None)
    raw_website = field(data, "primary_website_url", "website_url", default=None)
    website = safe_url(raw_website)
    avatar = field(data, "primary_avatar_url", "avatar_url", default=None)
    avatar_url = safe_url(avatar)
    resolution_run_id = field(data, "resolution_run_id", default=None)
    sources = dict_list(field(data, "sources", "accepted_sources", "platform_profiles", default=[]))
    review_candidates = dict_list(field(data, "review_candidates", default=[]))
    rejected_candidates = dict_list(field(data, "rejected_candidates", default=[]))
    warnings = field(data, "warnings", default=[])
    ai_summary = field(data, "ai_summary", "summary", default={}) or {}
    skills = field(data, "inferred_skills", "skills", default=[])
    profile_stage = field(data, "profile_stage", default=None)
    canonical_fields_pending = field(data, "canonical_fields_pending", default=False)
    resolution_summary = field(data, "resolution_summary", default={}) or {}
    outcome = field(resolution_summary, "outcome", default=field(data, "outcome", default=None))
    is_uncertain_profile = bool(canonical_fields_pending or profile_stage == "canonical_build_blocked" or outcome in {"ambiguous_candidates", "no_confident_match", "no_candidates_found"})

    initials = "".join(part[:1] for part in str(display_name).split()[:2]).upper() or "?"
    avatar_html = f'<img src="{h(avatar_url)}" alt="" />' if avatar_url else h(initials)
    website_html = safe_link(website, website) if website else ""
    raw_api_href = "/profiles/" + quote(str(profile_id), safe="")

    header = f"""
    <section class="card">
      <div class="profile-header">
        <div class="avatar">{avatar_html}</div>
        <div>
          <div class="title-row"><h1 class="profile-name">{h(display_name)}</h1>{badge(confidence, str(confidence))}</div>
          <p>{h(headline)}</p>
          <div class="profile-meta">
            <span>Profile ID: <strong>{h(profile_id)}</strong></span>
            {f'<span>Resolution run: <strong>{h(resolution_run_id)}</strong></span>' if resolution_run_id else ''}
            {f'<span>Location: <strong>{h(location)}</strong></span>' if location else ''}
            {f'<span>Website: {website_html}</span>' if website_html else ''}
          </div>
          {list_chips(skills)}
        </div>
        <a class="button ghost" href="{h(raw_api_href)}">Raw API</a>
      </div>
    </section>
    """

    summary_body = render_summary(ai_summary)
    sources_body = render_sources_table(sources, empty="No accepted sources were returned for this profile.")
    review_body = render_sources_table(review_candidates, empty="No ambiguous candidates require review.", review=True)
    rejected_body = render_sources_table(rejected_candidates, empty="No rejected candidates were returned.", rejected=True)

    uncertain_body = render_uncertain_profile_notice(outcome=outcome, review_count=len(review_candidates), rejected_count=len(rejected_candidates)) if is_uncertain_profile else ""

    content = (
        header
        + warnings_panel(warnings)
        + uncertain_body
        + card("AI summary", summary_body, subtitle="Grounded summary generated after deterministic profile building.")
        + card("Accepted sources", sources_body, subtitle="Sources accepted into this profile. Claimed-input sources are user-provided anchors and are not external ownership verification.")
        + card("Needs review", review_body, subtitle="Ambiguous candidates are not merged automatically.")
        + card("Rejected candidates", rejected_body, subtitle="Excluded from canonical fields and AI summaries.")
        + card("Raw profile JSON", json_details("Open raw profile response", data), subtitle="Useful for evaluator/debug inspection.")
    )
    return render_layout(title=str(display_name), active="resolve", content=content)



def render_uncertain_profile_notice(*, outcome: Any, review_count: int, rejected_count: int) -> str:
    if outcome == "no_candidates_found":
        body = (
            "<p>No public candidate accounts were found for this request. "
            "No canonical identity was created from invented or untrusted data.</p>"
        )
    else:
        body = (
            "<p>Possible public accounts were found, but the resolver did not have enough evidence "
            "to merge them safely. Review the candidates below before trusting this identity.</p>"
        )
    body += f'<div class="grid grid-2">{metric_card("Needs review", review_count)}{metric_card("Rejected", rejected_count)}</div>'
    return card("No confident canonical profile yet", body, subtitle="Uncertainty is a valid resolution outcome.")

def render_summary(summary: Any) -> str:
    data = to_plain(summary) or {}
    if not isinstance(data, dict) or not data:
        return empty_state("No AI summary has been generated yet.")
    headline = field(data, "headline", default="Summary")
    short_summary = field(data, "short_summary", "summary", default="")
    strengths = field(data, "strengths", default=[])
    source_note = field(data, "source_note", default="")
    limitations = field(data, "limitations", default=[])
    used_fallback = field(data, "used_fallback", default=False)
    fallback_badge = badge("fallback", "fallback") if used_fallback else badge("generated", "generated")
    strengths_html = list_chips(strengths)
    limitations_html = "".join(f"<li>{h(item)}</li>" for item in (limitations or []))
    return f"""
    <div class="title-row"><h3>{h(headline)}</h3>{fallback_badge}</div>
    <p>{h(short_summary)}</p>
    {strengths_html}
    {f'<p><strong>Source note:</strong> {h(source_note)}</p>' if source_note else ''}
    {f'<ul>{limitations_html}</ul>' if limitations_html else ''}
    """


def render_sources_table(rows: list[dict[str, Any]], *, empty: str, review: bool = False, rejected: bool = False) -> str:
    if not rows:
        return empty_state(empty)
    table_rows: list[list[Any]] = []
    for row in rows:
        source = field(row, "source", "platform", default="unknown")
        handle = field(row, "handle", "source_account_key", default="---")
        decision = source_decision_label(row, default="needs_review" if review else "reject" if rejected else "auto_match")
        confidence = source_confidence_label(row)
        profile_url = field(row, "profile_url", "url", default=None)
        reason = source_reason(row)
        table_rows.append([
            badge_cell(source, str(source)),
            handle,
            badge_cell(decision, str(decision)),
            confidence,
            link_cell(profile_url, "Open") if profile_url else "---",
            reason,
        ])
    return simple_table(["Source", "Handle", "Decision", "Confidence", "Profile", "Reason"], table_rows)


def source_decision_label(row: dict[str, Any], *, default: str) -> str:
    decision = str(field(row, "decision", default=default) or default)
    verification_status = str(field(row, "verification_status", default="") or "")
    decision_payload = source_decision_payload(row)
    decision_basis = str(field(row, "decision_basis", default=decision_payload.get("decision_basis", "")) or "")
    accepted_as_anchor = bool(field(row, "accepted_as_anchor", "is_anchor", default=decision_payload.get("accepted_as_anchor", decision_payload.get("is_anchor", False))))
    if verification_status == "claimed_by_input" or decision_basis == "anchor_input" or accepted_as_anchor:
        return f"{decision} / claimed input"
    return decision


def source_confidence_label(row: dict[str, Any]) -> str:
    decision_payload = source_decision_payload(row)
    confidence = field(row, "decision_confidence_score", "confidence_score", "score", default=decision_payload.get("decision_confidence_score"))
    evidence_confidence = field(row, "evidence_confidence_score", default=decision_payload.get("evidence_confidence_score"))
    if confidence is None:
        return "---"
    label = str(confidence)
    if evidence_confidence is not None and str(evidence_confidence) != str(confidence):
        label += f" \u00b7 evidence {evidence_confidence}"
    return label


def source_reason(row: dict[str, Any]) -> str:
    decision_payload = source_decision_payload(row)
    source = str(field(row, "source", "platform", default="source") or "source")
    verification_status = str(field(row, "verification_status", default="") or "")
    decision_basis = str(field(row, "decision_basis", default=decision_payload.get("decision_basis", "")) or "")
    accepted_as_anchor = bool(field(row, "accepted_as_anchor", "is_anchor", default=decision_payload.get("accepted_as_anchor", decision_payload.get("is_anchor", False))))
    hn_conservative = bool(field(row, "hn_conservative", default=decision_payload.get("hn_conservative", False)))

    if verification_status == "claimed_by_input" or decision_basis == "anchor_input" or accepted_as_anchor:
        if source == "hackernews" or hn_conservative:
            return (
                "User provided this Hacker News handle; accepted as a claimed input anchor, "
                "not external ownership verification. Hacker News profiles are sparse, so treat this conservatively unless stronger evidence is present."
            )
        explicit = field(row, "reason", "rationale", "explanation", default=None)
        if explicit:
            return str(explicit)
        return f"User provided this {source} identifier; accepted as claimed input, not external ownership verification."

    explicit = field(row, "reason", "rationale", "explanation", default=None)
    if explicit:
        return str(explicit)

    rationale_text = _text_from_reason_value(decision_payload.get("rationale"))
    if rationale_text:
        return rationale_text

    metadata = decision_payload.get("metadata")
    if isinstance(metadata, dict):
        explanation = _text_from_reason_value(metadata.get("account_score_explanation"))
        if explanation:
            return explanation

    if verification_status == "claimed_by_input":
        return f"User provided this {source} identifier; accepted as claimed input, not external ownership verification."
    return "---"


def source_decision_payload(row: dict[str, Any]) -> dict[str, Any]:
    for candidate in (
        field(row, "decision_payload", default=None),
        field(row, "link_decision_payload", default=None),
    ):
        plain = to_plain(candidate)
        if isinstance(plain, dict):
            return plain

    evidence_summary = to_plain(field(row, "evidence_summary", default={}))
    if isinstance(evidence_summary, dict):
        for key in ("decision_payload", "link_decision_payload", "classifier_payload"):
            nested = to_plain(evidence_summary.get(key))
            if isinstance(nested, dict):
                return nested
    return {}


def _text_from_reason_value(value: Any) -> str | None:
    if isinstance(value, list):
        texts = [str(item).strip() for item in value if str(item).strip()]
        return " ".join(texts[:2]) if texts else None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def render_dashboard_page(snapshot: Any, *, include_raw: bool = False, token_required: bool = False) -> str:
    data = to_plain(snapshot) or {}
    status = field(data, "status", default="unknown")
    generated_at = field(data, "generated_at", default=datetime.now(timezone.utc).isoformat())
    github = field(data, "github_rate_limit", default={}) or {}
    llm = field(data, "llm_usage", default={}) or {}
    profile_metrics = field(data, "profile_metrics", "resolution_metrics", default={}) or {}
    api_calls = dict_list(field(data, "api_calls_by_source", "external_api_calls", default=[]))
    warnings = field(data, "warnings", default=[])
    raw_views = field(data, "raw_views", default={})

    remaining = field(github, "remaining", "rate_limit_remaining", default="â€”")
    total = field(github, "total", "rate_limit_total", default="â€”")
    reset_at = field(github, "reset_at", "rate_limit_reset_at", default="â€”")
    resolved = field(profile_metrics, "profiles_resolved", "resolved_profiles", "total_profiles", default=0)
    avg_time = field(profile_metrics, "average_resolution_time_ms", "avg_resolution_time_ms", default=0)
    input_tokens = field(llm, "input_tokens", "total_input_tokens", default=0)
    output_tokens = field(llm, "output_tokens", "total_output_tokens", default=0)
    retry_count = field(llm, "retry_count", "total_retry_count", default=0)
    wait_ms = field(llm, "rate_limit_wait_ms", "total_rate_limit_wait_ms", default=0)
    cost = field(llm, "estimated_cost_usd", "total_estimated_cost_usd", default=0)

    api_rows = []
    for row in api_calls:
        source = field(row, "source", default="unknown")
        total_calls = field(row, "total_calls", "call_count", default=0)
        success = field(row, "successful_calls", "success_count", default=0)
        failed = field(row, "failed_calls", "error_count", default=0)
        avg_ms = field(row, "average_duration_ms", "avg_duration_ms", default=0)
        api_rows.append([badge_cell(source, str(source)), total_calls, success, failed, avg_ms])

    metrics = f"""
    <section class="grid grid-4">
      {metric_card("Profiles resolved", resolved, note="Canonical profiles completed")}
      {metric_card("Avg resolution", f"{avg_time} ms", note="Resolution run timing")}
      {metric_card("Gemini tokens", int(input_tokens or 0) + int(output_tokens or 0), note=f"input {input_tokens} / output {output_tokens}")}
      {metric_card("GitHub quota", f"{remaining} / {total}", note=f"reset: {reset_at}")}
    </section>
    <section class="grid grid-3">
      {metric_card("LLM retries", retry_count, note="Gemini retry attempts")}
      {metric_card("LLM wait", f"{wait_ms} ms", note="local limiter + retry waits")}
      {metric_card("Estimated cost", f"${cost}", note="free-tier project stores 0.0 by default")}
    </section>
    """

    token_protection_note = '<p class="muted">Dashboard token protection is enabled.</p>' if token_required else ""
    body = (
        f'<section class="card"><div class="card-header"><div class="card-title"><h2>System health</h2><div class="subtitle">Generated at {h(generated_at)}</div></div>{badge(status, str(status))}</div>'
        f'<p>This dashboard reads the same production observability data as <a class="link" href="/health">/health</a>. It is designed for operational checks during demos and deployment.</p>'
        + token_protection_note
        + '</section>'
        + warnings_panel(warnings)
        + metrics
        + card("External API calls", simple_table(["Source", "Total", "Successful", "Failed", "Avg ms"], api_rows), subtitle="Calls recorded in api_call_metrics.")
        + card("Raw observability JSON", json_details("Open health response", data), subtitle="JSON response used to render this dashboard.")
    )
    if include_raw and raw_views:
        body += card("Raw DB views", json_details("Open raw view rows", raw_views), subtitle="Only returned when include_raw=true.")
    return render_layout(title="Dashboard", active="dashboard", content=body)


def render_not_found_page(message: str = "The requested resource was not found.") -> str:
    return render_layout(
        title="Not found",
        active="resolve",
        content=card("Not found", f"<p>{h(message)}</p><a class=\"button\" href=\"/app\">Back to Resolve</a>"),
    )


def render_error_page(title: str, message: str) -> str:
    return render_layout(
        title=title,
        active="resolve",
        content=card(title, f"<p>{h(message)}</p><a class=\"button\" href=\"/app\">Back to Resolve</a>"),
    )

