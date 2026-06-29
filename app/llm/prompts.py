from __future__ import annotations

import json
import re
from typing import Any
from uuid import UUID

from app.schemas.summary import SummaryPromptPayload, StructuredProfileSummary

SUMMARY_PROMPT_VERSION = "profile_summary_v1_2026_06"

FORBIDDEN_CLAIM_PHRASES = {
    "verified",
    "verified owner",
    "verified account",
    "verified identity",
    "confirmed owner",
    "confirmed account",
    "confirmed ownership",
    "confirmed identity",
    "proven identity",
    "proves identity",
    "proved identity",
    "guaranteed same person",
    "definitely the same person",
    "certainly the same person",
    "owns the account",
    "owns these accounts",
    "account owner",
    "real owner",
    "officially verified",
}

SAFE_SOURCE_NOTE = (
    "This summary is based only on accepted public source accounts and deterministic canonical profile fields. "
    "Public profiles do not prove account ownership without OAuth or user-controlled verification."
)

SAFE_OWNERSHIP_LIMITATION = (
    "Public profiles do not prove account ownership without OAuth or user-controlled verification."
)


def build_summary_prompt_payload(profile: dict[str, Any]) -> SummaryPromptPayload:
    payload = profile.get("profile_payload")
    if not isinstance(payload, dict):
        payload = {}

    activity_summary = payload.get("activity_summary") if isinstance(payload.get("activity_summary"), dict) else {}

    platform_profiles = payload.get("platform_profiles")
    if not isinstance(platform_profiles, list):
        platform_profiles = []

    deterministic_facts = payload.get("deterministic_facts")
    if not isinstance(deterministic_facts, list):
        deterministic_facts = []

    field_sources = payload.get("field_sources")
    if not isinstance(field_sources, dict):
        field_sources = {}

    return SummaryPromptPayload(
        profile_id=UUID(str(profile["id"])),
        display_name=profile.get("display_name"),
        headline=profile.get("headline"),
        bio=profile.get("bio"),
        location=profile.get("location"),
        primary_website_url=profile.get("primary_website_url"),
        inferred_skills=profile.get("inferred_skills") or [],
        platform_profiles=platform_profiles,
        deterministic_facts=deterministic_facts,
        field_sources=field_sources,
        review_candidate_count=int(activity_summary.get("review_source_count") or len(payload.get("review_candidates") or [])),
        rejected_candidate_count=int(activity_summary.get("rejected_source_count") or len(payload.get("rejected_candidates") or [])),
        confidence_level=profile.get("confidence_level"),
    )


def build_profile_summary_prompt(prompt_payload: SummaryPromptPayload) -> str:
    allowed_payload = prompt_payload.model_dump(mode="json")

    return (
        "You are writing an evidence-aware developer profile summary for a public developer identity resolver.\n"
        "Use ONLY the JSON facts provided below. Do not infer employment, credentials, seniority, or account ownership.\n"
        "Never say verified, proven, guaranteed, confirmed owner, owns the account, or any equivalent ownership claim.\n"
        "Use safe language such as 'appears to', 'public profile data suggests', and 'based on accepted source accounts'.\n"
        "Do not use accounts requiring review or rejected accounts as factual sources; mention limitations only generically if useful.\n"
        "Do not name, list, or describe accounts outside the accepted source set.\n"
        "Return valid JSON only, with exactly these keys and no extra keys:\n"
        "headline: string\n"
        "short_summary: string\n"
        "strengths: array of concise strings\n"
        "source_note: string\n"
        "limitations: array of concise strings\n\n"
        "Canonical profile data:\n"
        f"{json.dumps(allowed_payload, ensure_ascii=False, sort_keys=True)}\n"
    )


def deterministic_fallback_summary(prompt_payload: SummaryPromptPayload) -> StructuredProfileSummary:
    display_name = prompt_payload.display_name or "This developer"
    skills = [skill for skill in prompt_payload.inferred_skills if skill][:6]

    if skills:
        headline = f"Developer focused on {', '.join(skills[:4])}"
        focus = ", ".join(skills[:4])
        short_summary = (
            f"{display_name} appears to be a developer focused on {focus}, "
            "based on accepted public profile data and deterministic canonical profile fields."
        )
    elif prompt_payload.headline:
        headline = prompt_payload.headline
        short_summary = (
            f"{display_name} appears to have public developer profile activity reflected in the accepted source accounts."
        )
    else:
        headline = "Developer profile based on accepted public source data"
        short_summary = (
            f"{display_name} has a sparse deterministic profile based on accepted public source accounts."
        )

    strengths = skills[:5]
    if not strengths and prompt_payload.platform_profiles:
        strengths = ["Public developer profile presence"]

    limitations = [SAFE_OWNERSHIP_LIMITATION]

    if prompt_payload.review_candidate_count:
        limitations.append("Some candidate accounts require review and were not used as factual summary sources.")

    if prompt_payload.rejected_candidate_count:
        limitations.append("Rejected candidate accounts were excluded from the summary facts.")

    return StructuredProfileSummary(
        headline=headline,
        short_summary=short_summary,
        strengths=strengths,
        source_note=SAFE_SOURCE_NOTE,
        limitations=limitations,
    )


def parse_structured_summary_json(text: str) -> StructuredProfileSummary:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        parsed = json.loads(text[start : end + 1])

    if not isinstance(parsed, dict):
        raise ValueError("Gemini summary response must be a JSON object.")

    return StructuredProfileSummary.model_validate(parsed)


def structured_summary_text(summary: StructuredProfileSummary) -> str:
    return " ".join(
        [
            summary.headline,
            summary.short_summary,
            summary.source_note,
            " ".join(summary.strengths),
            " ".join(summary.limitations),
        ]
    )


def text_contains_forbidden_claims(text: str) -> bool:
    lowered = text.lower()
    return any(
        re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", lowered) is not None
        for phrase in FORBIDDEN_CLAIM_PHRASES
    )


def sanitize_forbidden_claims(summary: StructuredProfileSummary) -> tuple[StructuredProfileSummary, bool]:
    changed = False

    def clean(value: str) -> str:
        nonlocal changed
        updated = value
        replacements = {
            "officially verified": "evidence-matched",
            "verified identity": "evidence-supported profile match",
            "verified account": "accepted public profile match",
            "verified owner": "accepted public profile match",
            "confirmed identity": "evidence-supported profile match",
            "confirmed account": "accepted public profile match",
            "confirmed owner": "accepted public profile match",
            "confirmed ownership": "accepted public profile match",
            "proven identity": "evidence-supported profile match",
            "proves identity": "suggests a profile match",
            "proved identity": "suggested a profile match",
            "guaranteed same person": "evidence-supported match",
            "definitely the same person": "appears to match",
            "certainly the same person": "appears to match",
            "owns these accounts": "is associated with the accepted public profile data",
            "owns the account": "is associated with the accepted public profile data",
            "account owner": "public profile match",
            "real owner": "public profile match",
            "verified": "evidence-matched",
        }
        for bad, safe in replacements.items():
            if bad in updated.lower():
                updated = replace_case_insensitive(updated, bad, safe)
                changed = True
        return " ".join(updated.split())

    cleaned_limitations = [clean(item) for item in summary.limitations]
    limitation_text = " ".join(cleaned_limitations).lower()
    if "ownership" not in limitation_text and "oauth" not in limitation_text:
        cleaned_limitations.append(SAFE_OWNERSHIP_LIMITATION)

    cleaned = StructuredProfileSummary(
        headline=clean(summary.headline),
        short_summary=clean(summary.short_summary),
        strengths=[clean(item) for item in summary.strengths],
        source_note=SAFE_SOURCE_NOTE,
        limitations=cleaned_limitations,
    )

    if summary.source_note != SAFE_SOURCE_NOTE:
        changed = True

    return cleaned, changed


def replace_case_insensitive(text: str, old: str, new: str) -> str:
    lowered = text.lower()
    old_lower = old.lower()
    result = []
    index = 0
    while True:
        match = lowered.find(old_lower, index)
        if match == -1:
            result.append(text[index:])
            break
        result.append(text[index:match])
        result.append(new)
        index = match + len(old)
    return "".join(result)
