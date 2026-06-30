from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from app.schemas.canonical_profile import (
    CanonicalActivitySummary,
    CanonicalBuildStatus,
    CanonicalFieldSelection,
    CanonicalPlatformProfile,
    CanonicalProfileBuildResult,
    CanonicalReviewCandidate,
)
from app.storage.profiles_repo import ProfilesRepo
from app.storage.resolution_runs_repo import ResolutionRunsRepo
from app.storage.source_accounts_repo import SourceAccountsRepo
from app.utils.errors import ProfileNotFoundError


SOURCE_PRIORITY = {
    "github": 100,
    "stackoverflow": 85,
    "devto": 75,
    "hackernews": 35,
}

DISPLAY_NAME_SOURCE_PRIORITY = {
    "github": 100,
    "devto": 85,
    "stackoverflow": 80,
    "hackernews": 20,
}

BIO_SOURCE_PRIORITY = {
    "github": 100,
    "devto": 90,
    "stackoverflow": 75,
    "hackernews": 25,
}

AVATAR_SOURCE_PRIORITY = {
    "github": 100,
    "devto": 80,
    "stackoverflow": 75,
    "hackernews": 10,
}

GENERIC_WEBSITE_DOMAINS = {
    "linktr.ee",
    "bio.link",
    "about.me",
    "carrd.co",
    "medium.com",
    "substack.com",
    "hashnode.dev",
    "notion.site",
    "vercel.app",
    "netlify.app",
    "pages.dev",
    "github.io",
}

PLATFORM_DOMAINS = {
    "github.com",
    "dev.to",
    "stackoverflow.com",
    "stackexchange.com",
    "news.ycombinator.com",
    "ycombinator.com",
}

VAGUE_LOCATIONS = {
    "earth",
    "world",
    "worldwide",
    "internet",
    "remote",
    "online",
    "global",
    "somewhere",
    "planet earth",
}

GENERIC_BIOS = {
    "developer",
    "software developer",
    "software engineer",
    "programmer",
    "coder",
    "student",
    "engineer",
}

WEAK_HEADLINES = {
    "hello",
    "hi",
    "welcome",
    "welcome to my profile",
    "developer",
    "software developer",
    "software engineer",
    "programmer",
    "coder",
}

GENERIC_SKILLS = {
    "software",
    "developer",
    "development",
    "programming",
    "code",
    "coding",
    "technology",
    "tech",
    "web",
    "app",
    "apps",
    "project",
    "projects",
    "personal",
    "portfolio",
}

SKILL_DISPLAY_OVERRIDES = {
    "ai": "AI",
    "api": "API",
    "apis": "APIs",
    "fastapi": "FastAPI",
    "javascript": "JavaScript",
    "typescript": "TypeScript",
    "postgresql": "PostgreSQL",
    "postgres": "PostgreSQL",
    "supabase": "Supabase",
    "python": "Python",
    "react": "React",
    "nextjs": "Next.js",
    "next.js": "Next.js",
    "nodejs": "Node.js",
    "node.js": "Node.js",
    "llm": "LLM",
    "llms": "LLMs",
    "nlp": "NLP",
    "ml": "ML",
    "fast-api": "FastAPI",
    "hacker-news": "Hacker News",
}


@dataclass(frozen=True)
class ProfileAccountBundle:
    link: dict[str, Any]
    account: dict[str, Any]

    @property
    def account_id(self) -> UUID:
        return UUID(str(self.account["id"]))

    @property
    def source(self) -> str:
        return str(self.account.get("source") or "").strip().lower()

    @property
    def source_account_key(self) -> str:
        value = self.account.get("source_account_key")
        if value:
            return str(value).strip().lower()

        source = self.source
        source_user_id = self.account.get("source_user_id")
        handle = self.account.get("handle")

        stable_id = source_user_id or handle or self.account["id"]
        return f"{source}:{stable_id}".lower()

    @property
    def decision(self) -> str:
        return str(self.link.get("decision") or "").strip().lower()

    @property
    def relationship_type(self) -> str | None:
        value = self.link.get("relationship_type")
        return str(value).strip().lower() if value else None

    @property
    def confidence_score(self) -> float:
        value = self.link.get("confidence_score")
        try:
            return float(value or 0.0)
        except (TypeError, ValueError):
            return 0.0

    @property
    def decision_payload(self) -> dict[str, Any]:
        payload = self.link.get("decision_payload")
        return payload if isinstance(payload, dict) else {}

    @property
    def is_anchor(self) -> bool:
        return bool(self.decision_payload.get("is_anchor"))

    @property
    def evidence_confidence_score(self) -> float:
        value = self.decision_payload.get("evidence_confidence_score")
        try:
            return float(value or 0.0)
        except (TypeError, ValueError):
            return 0.0

    @property
    def field_confidence_score(self) -> float:
        if self.evidence_confidence_score > 0:
            return self.evidence_confidence_score
        return self.confidence_score

    @property
    def handle(self) -> str | None:
        return clean_string(self.account.get("handle"))

    @property
    def profile_url(self) -> str | None:
        return clean_string(self.account.get("profile_url"))


class CanonicalProfileService:
    """
    Builds deterministic canonical profile fields from accepted source accounts.

    Phase 8 responsibilities:
    - load profile shell
    - load source links and source accounts
    - use auto_match accounts for canonical fields
    - preserve review/rejected accounts in payload
    - update canonical_profiles

    This service does not call Gemini and does not change match decisions.
    """

    def __init__(
        self,
        *,
        profiles_repo: ProfilesRepo,
        source_accounts_repo: SourceAccountsRepo,
        resolution_runs_repo: ResolutionRunsRepo | None = None,
    ) -> None:
        self.profiles_repo = profiles_repo
        self.source_accounts_repo = source_accounts_repo
        self.resolution_runs_repo = resolution_runs_repo

    def build_by_resolution_run_id(
        self,
        *,
        resolution_run_id: UUID | str,
    ) -> CanonicalProfileBuildResult:
        profile = self.profiles_repo.get_by_resolution_run_id(resolution_run_id)

        if not profile:
            raise ProfileNotFoundError(str(resolution_run_id))

        return self.build_by_profile_id(profile_id=profile["id"])

    def build_by_profile_id(
        self,
        *,
        profile_id: UUID | str,
    ) -> CanonicalProfileBuildResult:
        profile = self.profiles_repo.get_by_id(profile_id)

        if not profile:
            raise ProfileNotFoundError(str(profile_id))

        links = self.profiles_repo.list_source_links_for_profile(profile["id"])
        bundles = self._load_account_bundles(links)

        accepted = [
            bundle
            for bundle in bundles
            if bundle.decision == "auto_match"
        ]

        review = [
            bundle
            for bundle in bundles
            if bundle.decision == "needs_review"
        ]

        rejected = [
            bundle
            for bundle in bundles
            if bundle.decision == "reject"
        ]

        if not accepted:
            return self._mark_blocked_no_auto_match(
                profile=profile,
                bundles=bundles,
                review=review,
                rejected=rejected,
            )

        display_name = self._select_display_name(
            accepted=accepted,
            fallback_name=clean_string(profile.get("display_name")),
        )

        bio = self._select_bio(accepted)
        headline = self._select_headline(
            accepted=accepted,
            bio_selection=bio,
        )
        location = self._select_location(accepted)
        website = self._select_website_url(accepted)
        avatar = self._select_avatar_url(accepted)
        skills = self._merge_inferred_skills(accepted)

        field_sources = {
            "display_name": display_name,
            "headline": headline,
            "bio": bio,
            "location": location,
            "primary_website_url": website,
            "primary_avatar_url": avatar,
            "inferred_skills": skills,
        }

        confidence_level = self._profile_confidence_level(
            accepted=accepted,
            field_sources=field_sources,
        )

        platform_profiles = self._build_platform_profiles(accepted)
        review_candidates = self._build_review_candidates(review)
        rejected_candidates = self._build_review_candidates(rejected)

        activity_summary = self._build_activity_summary(
            accepted=accepted,
            review=review,
            rejected=rejected,
        )

        profile_payload = self._build_profile_payload(
            previous_payload=profile.get("profile_payload"),
            field_sources=field_sources,
            platform_profiles=platform_profiles,
            review_candidates=review_candidates,
            rejected_candidates=rejected_candidates,
            activity_summary=activity_summary,
            accepted=accepted,
        )

        updated = self.profiles_repo.update_canonical_profile_fields(
            profile_id=profile["id"],
            display_name=display_name.value,
            headline=headline.value,
            location=location.value,
            bio=bio.value,
            primary_avatar_url=avatar.value,
            primary_website_url=website.value,
            inferred_skills=skills.value or [],
            confidence_level=confidence_level,
            profile_payload=profile_payload,
        )
        self._patch_resolution_run_summary(
            profile=updated,
            patch={
                "canonical_profile_built": True,
                "canonical_profile_stage": "deterministic_built",
                "canonical_builder_version": "canonical_builder_v1",
            },
        )

        return CanonicalProfileBuildResult(
            canonical_profile_id=UUID(str(updated["id"])),
            status=CanonicalBuildStatus.BUILT,
            updated=True,
            display_name=updated.get("display_name"),
            headline=updated.get("headline"),
            location=updated.get("location"),
            bio=updated.get("bio"),
            primary_avatar_url=updated.get("primary_avatar_url"),
            primary_website_url=updated.get("primary_website_url"),
            inferred_skills=updated.get("inferred_skills") or [],
            confidence_level=updated.get("confidence_level") or confidence_level,
            field_sources=field_sources,
            platform_profiles=platform_profiles,
            review_candidates=review_candidates,
            rejected_candidates=rejected_candidates,
            activity_summary=activity_summary,
            profile_payload=profile_payload,
        )

    def _load_account_bundles(
        self,
        links: list[dict[str, Any]],
    ) -> list[ProfileAccountBundle]:
        source_account_ids = [
            link.get("source_account_id")
            for link in links
            if link.get("source_account_id")
        ]

        accounts = self.source_accounts_repo.list_by_ids(source_account_ids)
        account_by_id = {
            str(account["id"]): account
            for account in accounts
        }

        bundles: list[ProfileAccountBundle] = []

        for link in links:
            account_id = link.get("source_account_id")

            if not account_id:
                continue

            account = account_by_id.get(str(account_id))

            if not account:
                continue

            bundles.append(
                ProfileAccountBundle(
                    link=link,
                    account=account,
                )
            )

        return sorted(
            bundles,
            key=lambda item: (
                item.decision != "auto_match",
                -item.field_confidence_score,
                -SOURCE_PRIORITY.get(item.source, 0),
                item.source_account_key,
            ),
        )

    def _select_display_name(
        self,
        *,
        accepted: list[ProfileAccountBundle],
        fallback_name: str | None,
    ) -> CanonicalFieldSelection:
        candidates: list[tuple[str, ProfileAccountBundle]] = []

        for bundle in accepted:
            value = clean_string(bundle.account.get("display_name"))

            if not value:
                continue

            if is_handle_like_name(value, bundle.handle):
                continue

            candidates.append((value, bundle))

        if candidates:
            by_normalized: dict[str, list[tuple[str, ProfileAccountBundle]]] = defaultdict(list)

            for value, bundle in candidates:
                by_normalized[normalize_name_key(value)].append((value, bundle))

            ranked = sorted(
                by_normalized.values(),
                key=lambda group: (
                    -len(group),
                    -max(item[1].field_confidence_score for item in group),
                    -max(relationship_priority(item[1].relationship_type) for item in group),
                    -max(DISPLAY_NAME_SOURCE_PRIORITY.get(item[1].source, 0) for item in group),
                    -max(len(item[0]) for item in group),
                    normalize_name_key(group[0][0]),
                ),
            )

            winning_group = ranked[0]
            value = sorted(
                winning_group,
                key=lambda item: (
                    -len(item[0]),
                    -item[1].field_confidence_score,
                    -relationship_priority(item[1].relationship_type),
                    -DISPLAY_NAME_SOURCE_PRIORITY.get(item[1].source, 0),
                    item[0].lower(),
                ),
            )[0][0]

            return CanonicalFieldSelection(
                field_name="display_name",
                value=value,
                strategy="repeated_real_name" if len(winning_group) > 1 else "best_real_name_by_source_priority",
                confidence_score=min(0.97, max(item[1].field_confidence_score for item in winning_group)),
                source_account_keys=sorted({item[1].source_account_key for item in winning_group}),
                source_account_ids=sorted({item[1].account_id for item in winning_group}, key=str),
            )

        if fallback_name:
            return CanonicalFieldSelection(
                field_name="display_name",
                value=fallback_name,
                strategy="fallback_existing_profile_display_name",
                confidence_score=0.50,
                source_account_keys=[],
                source_account_ids=[],
            )

        best_handle = self._best_handle(accepted)

        return CanonicalFieldSelection(
            field_name="display_name",
            value=best_handle[0] if best_handle else None,
            strategy="fallback_best_handle",
            confidence_score=min(0.50, best_handle[1].field_confidence_score) if best_handle else 0.0,
            source_account_keys=[best_handle[1].source_account_key] if best_handle else [],
            source_account_ids=[best_handle[1].account_id] if best_handle else [],
        )

    def _select_bio(
        self,
        accepted: list[ProfileAccountBundle],
    ) -> CanonicalFieldSelection:
        candidates: list[tuple[str, ProfileAccountBundle, int]] = []

        for bundle in accepted:
            for field in ("bio", "summary", "about"):
                value = clean_string(bundle.account.get(field))
                if not value:
                    continue

                if not is_meaningful_bio(value):
                    continue

                quality = bio_quality_score(value)
                candidates.append((value, bundle, quality))

            activity_payload = safe_dict(bundle.account.get("activity_payload"))
            for field in ("bio", "summary", "about"):
                value = clean_string(activity_payload.get(field))
                if value and is_meaningful_bio(value):
                    quality = bio_quality_score(value)
                    candidates.append((value, bundle, quality))

        if not candidates:
            return CanonicalFieldSelection(
                field_name="bio",
                value=None,
                strategy="no_meaningful_bio_available",
                confidence_score=0.0,
            )

        value, bundle, _quality = sorted(
            candidates,
            key=lambda item: (
                item[2],
                relationship_priority(item[1].relationship_type),
                BIO_SOURCE_PRIORITY.get(item[1].source, 0),
                item[1].field_confidence_score,
                len(item[0]),
            ),
            reverse=True,
        )[0]

        return CanonicalFieldSelection(
            field_name="bio",
            value=value,
            strategy="highest_quality_source_bio",
            confidence_score=min(0.97, bundle.field_confidence_score),
            source_account_keys=[bundle.source_account_key],
            source_account_ids=[bundle.account_id],
        )

    def _select_headline(
        self,
        *,
        accepted: list[ProfileAccountBundle],
        bio_selection: CanonicalFieldSelection,
    ) -> CanonicalFieldSelection:
        if bio_selection.value:
            headline = summarize_headline_from_bio(str(bio_selection.value))

            if headline:
                return CanonicalFieldSelection(
                    field_name="headline",
                    value=headline,
                    strategy="shortened_from_selected_bio",
                    confidence_score=bio_selection.confidence_score,
                    source_account_keys=bio_selection.source_account_keys,
                    source_account_ids=bio_selection.source_account_ids,
                )

        skills = self._merge_inferred_skills(accepted)

        if skills.value:
            top = skills.value[:3]
            headline = "Developer focused on " + ", ".join(top)

            return CanonicalFieldSelection(
                field_name="headline",
                value=headline,
                strategy="deterministic_from_top_skills",
                confidence_score=skills.confidence_score,
                source_account_keys=skills.source_account_keys,
                source_account_ids=skills.source_account_ids,
            )

        return CanonicalFieldSelection(
            field_name="headline",
            value=None,
            strategy="no_headline_available",
            confidence_score=0.0,
        )

    def _select_location(
        self,
        accepted: list[ProfileAccountBundle],
    ) -> CanonicalFieldSelection:
        candidates: list[tuple[str, ProfileAccountBundle]] = []

        for bundle in accepted:
            value = clean_string(bundle.account.get("location"))

            if not value:
                continue

            if normalize_location_key(value) in VAGUE_LOCATIONS:
                continue

            candidates.append((value, bundle))

        if not candidates:
            return CanonicalFieldSelection(
                field_name="location",
                value=None,
                strategy="no_location_available",
                confidence_score=0.0,
            )

        grouped: dict[str, list[tuple[str, ProfileAccountBundle]]] = defaultdict(list)

        for value, bundle in candidates:
            grouped[normalize_location_key(value)].append((value, bundle))

        winning_group = sorted(
            grouped.values(),
            key=lambda group: (
                len(group),
                max(item[1].field_confidence_score for item in group),
                max(relationship_priority(item[1].relationship_type) for item in group),
                max(SOURCE_PRIORITY.get(item[1].source, 0) for item in group),
            ),
            reverse=True,
        )[0]

        value, best_bundle = sorted(
            winning_group,
            key=lambda item: (
                SOURCE_PRIORITY.get(item[1].source, 0),
                relationship_priority(item[1].relationship_type),
                item[1].field_confidence_score,
            ),
            reverse=True,
        )[0]

        return CanonicalFieldSelection(
            field_name="location",
            value=value,
            strategy="repeated_location" if len(winning_group) > 1 else "best_location_by_source_priority",
            confidence_score=min(0.97, best_bundle.field_confidence_score),
            source_account_keys=sorted({item[1].source_account_key for item in winning_group}),
            source_account_ids=sorted({item[1].account_id for item in winning_group}, key=str),
        )

    def _select_website_url(
        self,
        accepted: list[ProfileAccountBundle],
    ) -> CanonicalFieldSelection:
        candidates: list[tuple[str, str, ProfileAccountBundle]] = []

        for bundle in accepted:
            for field in ("website_url", "primary_website_url", "blog_url"):
                value = clean_string(bundle.account.get(field))

                if not value:
                    continue

                normalized = normalize_url(value)
                domain = domain_from_url(normalized)

                if not normalized or not domain:
                    continue

                if domain_in_set(domain, PLATFORM_DOMAINS):
                    continue

                candidates.append((normalized, domain, bundle))

        if not candidates:
            return CanonicalFieldSelection(
                field_name="primary_website_url",
                value=None,
                strategy="no_website_available",
                confidence_score=0.0,
            )

        non_generic = [
            item for item in candidates
            if not domain_in_set(item[1], GENERIC_WEBSITE_DOMAINS)
        ]

        candidate_pool = non_generic or candidates

        grouped: dict[str, list[tuple[str, str, ProfileAccountBundle]]] = defaultdict(list)

        for value, domain, bundle in candidate_pool:
            grouped[domain].append((value, domain, bundle))

        winning_group = sorted(
            grouped.values(),
            key=lambda group: (
                len(group),
                max(item[2].field_confidence_score for item in group),
                max(relationship_priority(item[2].relationship_type) for item in group),
                max(SOURCE_PRIORITY.get(item[2].source, 0) for item in group),
                item_domain_quality(group[0][1]),
            ),
            reverse=True,
        )[0]

        value, _domain, best_bundle = sorted(
            winning_group,
            key=lambda item: (
                SOURCE_PRIORITY.get(item[2].source, 0),
                relationship_priority(item[2].relationship_type),
                item[2].field_confidence_score,
                len(item[0]),
            ),
            reverse=True,
        )[0]

        return CanonicalFieldSelection(
            field_name="primary_website_url",
            value=value,
            strategy="shared_website_domain" if len(winning_group) > 1 else "best_website_by_source_priority",
            confidence_score=min(0.97, best_bundle.field_confidence_score),
            source_account_keys=sorted({item[2].source_account_key for item in winning_group}),
            source_account_ids=sorted({item[2].account_id for item in winning_group}, key=str),
        )

    def _select_avatar_url(
        self,
        accepted: list[ProfileAccountBundle],
    ) -> CanonicalFieldSelection:
        candidates: list[tuple[str, ProfileAccountBundle]] = []

        for bundle in accepted:
            for field in ("avatar_url", "profile_image_url", "profile_image", "primary_avatar_url"):
                value = clean_string(bundle.account.get(field))

                if value and is_probable_url(value):
                    candidates.append((value, bundle))

            activity_payload = safe_dict(bundle.account.get("activity_payload"))
            for field in ("avatar_url", "profile_image_url", "profile_image"):
                value = clean_string(activity_payload.get(field))

                if value and is_probable_url(value):
                    candidates.append((value, bundle))

        if not candidates:
            return CanonicalFieldSelection(
                field_name="primary_avatar_url",
                value=None,
                strategy="no_avatar_available",
                confidence_score=0.0,
            )

        value, bundle = sorted(
            candidates,
            key=lambda item: (
                AVATAR_SOURCE_PRIORITY.get(item[1].source, 0),
                relationship_priority(item[1].relationship_type),
                item[1].field_confidence_score,
            ),
            reverse=True,
        )[0]

        return CanonicalFieldSelection(
            field_name="primary_avatar_url",
            value=value,
            strategy="best_avatar_by_source_priority",
            confidence_score=min(0.97, bundle.field_confidence_score),
            source_account_keys=[bundle.source_account_key],
            source_account_ids=[bundle.account_id],
        )

    def _merge_inferred_skills(
        self,
        accepted: list[ProfileAccountBundle],
    ) -> CanonicalFieldSelection:
        weighted: dict[str, float] = defaultdict(float)
        source_keys: dict[str, set[str]] = defaultdict(set)
        source_ids: dict[str, set[UUID]] = defaultdict(set)

        for bundle in accepted:
            raw_terms = extract_skill_terms(bundle.account)

            for term in raw_terms:
                normalized = normalize_skill_key(term)

                if not normalized:
                    continue

                if normalized in GENERIC_SKILLS:
                    continue

                weighted[normalized] += 1.0 + (SOURCE_PRIORITY.get(bundle.source, 0) / 100.0)
                source_keys[normalized].add(bundle.source_account_key)
                source_ids[normalized].add(bundle.account_id)

        if not weighted:
            return CanonicalFieldSelection(
                field_name="inferred_skills",
                value=[],
                strategy="no_skills_available",
                confidence_score=0.0,
            )

        ranked_terms = sorted(
            weighted.items(),
            key=lambda item: (
                -item[1],
                -len(source_keys[item[0]]),
                item[0],
            ),
        )

        skills = [
            display_skill_label(term)
            for term, _score in ranked_terms[:30]
        ]

        selected_keys = set()
        selected_ids = set()

        for term, _score in ranked_terms[:30]:
            selected_keys.update(source_keys[term])
            selected_ids.update(source_ids[term])

        confidence = min(
            0.97,
            max(bundle.field_confidence_score for bundle in accepted) if accepted else 0.0,
        )

        return CanonicalFieldSelection(
            field_name="inferred_skills",
            value=skills,
            strategy="merged_ranked_topics_and_languages",
            confidence_score=confidence,
            source_account_keys=sorted(selected_keys),
            source_account_ids=sorted(selected_ids, key=str),
        )

    def _build_platform_profiles(
        self,
        accepted: list[ProfileAccountBundle],
    ) -> list[CanonicalPlatformProfile]:
        profiles: list[CanonicalPlatformProfile] = []

        for bundle in accepted:
            profiles.append(
                CanonicalPlatformProfile(
                    source=bundle.source,
                    source_account_key=bundle.source_account_key,
                    source_account_id=bundle.account_id,
                    handle=bundle.handle,
                    profile_url=bundle.profile_url,
                    decision=bundle.decision,
                    relationship_type=bundle.relationship_type,
                    verification_status=clean_string(bundle.link.get("verification_status")),
                    confidence_score=bundle.confidence_score,
                    decision_payload=dict(bundle.decision_payload),
                    reason=self._source_reason(bundle),
                    evidence_confidence_score=bundle.decision_payload.get("evidence_confidence_score"),
                    decision_confidence_score=bundle.decision_payload.get("decision_confidence_score"),
                    accepted_as_anchor=bool(bundle.decision_payload.get("accepted_as_anchor")) or bundle.is_anchor,
                    hn_conservative=bool(bundle.decision_payload.get("hn_conservative")),
                    decision_basis=clean_string(bundle.decision_payload.get("decision_basis")),
                    risk_level=clean_string(bundle.decision_payload.get("risk_level")),
                    is_anchor=bundle.is_anchor,
                )
            )

        return sorted(
            profiles,
            key=lambda item: (
                -SOURCE_PRIORITY.get(item.source, 0),
                item.source_account_key,
            ),
        )

    def _source_reason(self, bundle: ProfileAccountBundle) -> str | None:
        if (
            bundle.source == "hackernews"
            and (
                clean_string(bundle.link.get("verification_status")) == "claimed_by_input"
                or bundle.decision_payload.get("decision_basis") == "anchor_input"
                or bundle.decision_payload.get("accepted_as_anchor")
                or bundle.decision_payload.get("is_anchor")
            )
        ):
            return (
                "User provided this Hacker News handle; accepted as a claimed input anchor, "
                "not external ownership verification. Hacker News profiles are sparse, so treat this conservatively unless stronger evidence is present."
            )
        rationale = bundle.decision_payload.get("rationale")
        if isinstance(rationale, list):
            for item in rationale:
                text = clean_string(item)
                if text:
                    return text
        text = clean_string(bundle.decision_payload.get("rationale"))
        if text:
            return text
        metadata = bundle.decision_payload.get("metadata")
        if isinstance(metadata, dict):
            explanation = metadata.get("account_score_explanation")
            if isinstance(explanation, list):
                for item in explanation:
                    text = clean_string(item)
                    if text:
                        return text
            return clean_string(explanation)
        return None
    def _build_review_candidates(
        self,
        bundles: list[ProfileAccountBundle],
    ) -> list[CanonicalReviewCandidate]:
        candidates: list[CanonicalReviewCandidate] = []

        for bundle in bundles:
            rationale = bundle.decision_payload.get("rationale")
            reason = None

            if isinstance(rationale, list) and rationale:
                reason = str(rationale[0])

            candidates.append(
                CanonicalReviewCandidate(
                    source=bundle.source,
                    source_account_key=bundle.source_account_key,
                    source_account_id=bundle.account_id,
                    handle=bundle.handle,
                    profile_url=bundle.profile_url,
                    decision=bundle.decision,
                    confidence_score=bundle.confidence_score,
                    reason=reason,
                )
            )

        return sorted(
            candidates,
            key=lambda item: (
                -item.confidence_score,
                item.source_account_key,
            ),
        )

    def _build_activity_summary(
        self,
        *,
        accepted: list[ProfileAccountBundle],
        review: list[ProfileAccountBundle],
        rejected: list[ProfileAccountBundle],
    ) -> CanonicalActivitySummary:
        return CanonicalActivitySummary(
            accepted_source_count=len(accepted),
            review_source_count=len(review),
            rejected_source_count=len(rejected),
            accepted_sources=sorted({item.source for item in accepted}),
            review_sources=sorted({item.source for item in review}),
            rejected_sources=sorted({item.source for item in rejected}),
        )

    def _build_profile_payload(
        self,
        *,
        previous_payload: Any,
        field_sources: dict[str, CanonicalFieldSelection],
        platform_profiles: list[CanonicalPlatformProfile],
        review_candidates: list[CanonicalReviewCandidate],
        rejected_candidates: list[CanonicalReviewCandidate],
        activity_summary: CanonicalActivitySummary,
        accepted: list[ProfileAccountBundle],
    ) -> dict[str, Any]:
        base_payload = previous_payload if isinstance(previous_payload, dict) else {}

        field_sources_payload = {
            field_name: selection.model_dump(mode="json")
            for field_name, selection in field_sources.items()
        }

        deterministic_facts = self._build_deterministic_facts(
            field_sources=field_sources,
            platform_profiles=platform_profiles,
        )

        return {
            **base_payload,
            "profile_stage": "deterministic_built",
            "phase": 8,
            "canonical_fields_pending": False,
            "canonical_builder_version": "canonical_builder_v1",
            "canonical_field_policy": {
                "canonical_fields_from": "auto_match_accounts_only",
                "needs_review_usage": "metadata_only",
                "reject_usage": "excluded_from_canonical_fields",
                "llm_usage": "none",
            },
            "accepted_account_keys": sorted(
                bundle.source_account_key
                for bundle in accepted
            ),
            "field_sources": field_sources_payload,
            "platform_profiles": [
                item.model_dump(mode="json")
                for item in platform_profiles
            ],
            "review_candidates": [
                item.model_dump(mode="json")
                for item in review_candidates
            ],
            "rejected_candidates": [
                item.model_dump(mode="json")
                for item in rejected_candidates
            ],
            "activity_summary": activity_summary.model_dump(mode="json"),
            "deterministic_facts": deterministic_facts,
        }

    def _build_deterministic_facts(
        self,
        *,
        field_sources: dict[str, CanonicalFieldSelection],
        platform_profiles: list[CanonicalPlatformProfile],
    ) -> list[dict[str, Any]]:
        facts: list[dict[str, Any]] = []

        skills = field_sources["inferred_skills"].value or []
        for skill in skills[:30]:
            facts.append(
                {
                    "fact_type": "skill",
                    "fact_value": skill,
                    "confidence_score": field_sources["inferred_skills"].confidence_score,
                    "source_account_keys": field_sources["inferred_skills"].source_account_keys,
                    "derivation": "merged_ranked_topics_and_languages",
                }
            )

        website = field_sources["primary_website_url"].value
        if website:
            facts.append(
                {
                    "fact_type": "website",
                    "fact_value": website,
                    "confidence_score": field_sources["primary_website_url"].confidence_score,
                    "source_account_keys": field_sources["primary_website_url"].source_account_keys,
                    "derivation": field_sources["primary_website_url"].strategy,
                }
            )

        for profile in platform_profiles:
            if profile.profile_url:
                facts.append(
                    {
                        "fact_type": "platform_profile",
                        "fact_value": profile.profile_url,
                        "confidence_score": profile.confidence_score,
                        "source_account_keys": [profile.source_account_key],
                        "platform": profile.source,
                        "derivation": "accepted_auto_match_platform_profile",
                    }
                )

        return facts

    def _profile_confidence_level(
        self,
        *,
        accepted: list[ProfileAccountBundle],
        field_sources: dict[str, CanonicalFieldSelection],
    ) -> str:
        if not accepted:
            return "low"

        max_link_score = max(bundle.confidence_score for bundle in accepted)
        max_evidence_score = max(bundle.evidence_confidence_score for bundle in accepted)

        strong_field_count = sum(
            1
            for selection in field_sources.values()
            if selection.value not in (None, [], "")
            and selection.confidence_score >= 0.75
        )

        accepted_source_count = len(accepted)

        if (
            accepted_source_count >= 2
            and max_link_score >= 0.85
            and max_evidence_score >= 0.60
            and strong_field_count >= 2
        ):
            return "high"

        if max_link_score >= 0.60 or accepted_source_count >= 1:
            return "medium"

        return "low"

    def _best_handle(
        self,
        accepted: list[ProfileAccountBundle],
    ) -> tuple[str, ProfileAccountBundle] | None:
        handle_bundles = [
            (bundle.handle, bundle)
            for bundle in accepted
            if bundle.handle
        ]

        if not handle_bundles:
            return None

        counts = Counter(handle.lower() for handle, _bundle in handle_bundles)

        return sorted(
            handle_bundles,
            key=lambda item: (
                -counts[item[0].lower()],
                -relationship_priority(item[1].relationship_type),
                -SOURCE_PRIORITY.get(item[1].source, 0),
                -item[1].field_confidence_score,
                -len(item[0]),
                item[0].lower(),
            ),
        )[0]

    def _mark_blocked_no_auto_match(
        self,
        *,
        profile: dict[str, Any],
        bundles: list[ProfileAccountBundle],
        review: list[ProfileAccountBundle],
        rejected: list[ProfileAccountBundle],
    ) -> CanonicalProfileBuildResult:
        review_candidates = self._build_review_candidates(review)
        rejected_candidates = self._build_review_candidates(rejected)

        activity_summary = self._build_activity_summary(
            accepted=[],
            review=review,
            rejected=rejected,
        )

        previous_payload = profile.get("profile_payload")
        base_payload = previous_payload if isinstance(previous_payload, dict) else {}
        previous_summary = base_payload.get("resolution_summary")
        previous_summary = previous_summary if isinstance(previous_summary, dict) else {}
        outcome = self._uncertain_outcome(
            previous_summary=previous_summary,
            review_count=len(review_candidates),
            rejected_count=len(rejected_candidates),
            candidate_count=len(bundles),
        )
        outcome_reason = self._uncertain_outcome_reason(outcome)
        blocked_reason = "no_candidates_found" if outcome == "no_candidates_found" else "no_auto_match_accounts"
        resolution_summary = {
            **previous_summary,
            "outcome": outcome,
            "outcome_reason": outcome_reason,
            "candidate_count": len(bundles),
            "auto_match_count": 0,
            "needs_review_count": len(review_candidates),
            "reject_count": len(rejected_candidates),
            "canonical_profile_created": True,
            "canonical_profile_trusted": False,
        }

        profile_payload = {
            **base_payload,
            "profile_stage": "canonical_build_blocked",
            "status": outcome,
            "outcome": outcome,
            "phase": 8,
            "canonical_fields_pending": True,
            "canonical_builder_version": "canonical_builder_v1",
            "blocked_reason": blocked_reason,
            "canonical_build_blocked_reason": blocked_reason,
            "warnings": [
                "Only ambiguous or untrusted candidates were found. Review sources before trusting this identity."
            ] if outcome != "no_candidates_found" else [
                "No public candidate accounts were found for this request."
            ],
            "resolution_summary": resolution_summary,
            "accepted_account_keys": [],
            "platform_profiles": [],
            "canonical_field_policy": {
                "canonical_fields_from": "auto_match_accounts_only",
                "needs_review_usage": "metadata_only",
                "reject_usage": "excluded_from_canonical_fields",
                "llm_usage": "none",
            },
            "review_candidates": [
                item.model_dump(mode="json")
                for item in review_candidates
            ],
            "rejected_candidates": [
                item.model_dump(mode="json")
                for item in rejected_candidates
            ],
            "activity_summary": activity_summary.model_dump(mode="json"),
        }

        updated = self.profiles_repo.update_canonical_profile_fields(
            profile_id=profile["id"],
            display_name=None,
            headline=None,
            location=None,
            bio=None,
            primary_avatar_url=None,
            primary_website_url=None,
            inferred_skills=[],
            confidence_level="uncertain",
            profile_payload=profile_payload,
        )
        self._patch_resolution_run_summary(
            profile=updated,
            patch={
                "canonical_profile_built": False,
                "canonical_profile_stage": "canonical_build_blocked",
                "canonical_build_blocked_reason": blocked_reason,
                "outcome": outcome,
                "outcome_reason": outcome_reason,
                "canonical_profile_trusted": False,
            },
        )

        return CanonicalProfileBuildResult(
            canonical_profile_id=UUID(str(updated["id"])),
            status=CanonicalBuildStatus.BLOCKED_NO_AUTO_MATCH,
            updated=True,
            display_name=updated.get("display_name"),
            headline=updated.get("headline"),
            location=updated.get("location"),
            bio=updated.get("bio"),
            primary_avatar_url=updated.get("primary_avatar_url"),
            primary_website_url=updated.get("primary_website_url"),
            inferred_skills=updated.get("inferred_skills") or [],
            confidence_level=updated.get("confidence_level") or "uncertain",
            review_candidates=review_candidates,
            rejected_candidates=rejected_candidates,
            activity_summary=activity_summary,
            profile_payload=profile_payload,
        )


    def _uncertain_outcome(
        self,
        *,
        previous_summary: dict[str, Any],
        review_count: int,
        rejected_count: int,
        candidate_count: int,
    ) -> str:
        previous_outcome = str(previous_summary.get("outcome") or "").strip()
        if previous_outcome in {"ambiguous_candidates", "no_confident_match", "no_candidates_found"}:
            return previous_outcome
        if candidate_count == 0:
            return "no_candidates_found"
        if review_count > 0:
            return "ambiguous_candidates"
        return "no_confident_match"

    def _uncertain_outcome_reason(self, outcome: str) -> str:
        if outcome == "no_candidates_found":
            return "No public candidate accounts were found for the request."
        if outcome == "ambiguous_candidates":
            return (
                "Possible public accounts were found, but none had enough evidence "
                "to become a trusted canonical identity."
            )
        return (
            "Candidate accounts were evaluated, but no source had enough trusted "
            "evidence to create a canonical identity."
        )
    def _patch_resolution_run_summary(
        self,
        *,
        profile: dict[str, Any],
        patch: dict[str, Any],
    ) -> None:
        if not self.resolution_runs_repo:
            return

        resolution_run_id = profile.get("resolution_run_id")
        if not resolution_run_id:
            return

        self.resolution_runs_repo.merge_result_summary(
            resolution_run_id=resolution_run_id,
            patch=patch,
        )


def relationship_priority(value: str | None) -> int:
    if value == "primary":
        return 100
    if value in {"supporting", "secondary", "alias"}:
        return 80
    if value in {"ambiguous", "possible_alias"}:
        return 30
    if value == "rejected":
        return 0
    return 0


def clean_string(value: Any) -> str | None:
    if value is None:
        return None

    cleaned = str(value).strip()

    if not cleaned:
        return None

    return " ".join(cleaned.split())


def safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def normalize_name_key(value: str) -> str:
    return "".join(char.lower() for char in value if char.isalnum())


def is_handle_like_name(name: str, handle: str | None) -> bool:
    normalized_name = normalize_name_key(name)

    if not normalized_name:
        return True

    if handle and normalized_name == normalize_name_key(handle):
        return True

    if " " not in name and len(name) <= 16:
        if any(char.isdigit() for char in name):
            return True

        if name.islower():
            return True

    return False


def is_meaningful_bio(value: str) -> bool:
    cleaned = value.strip()

    if len(cleaned) < 18:
        return False

    lowered = cleaned.lower()

    if lowered in GENERIC_BIOS:
        return False

    if len(set(lowered.split())) <= 2:
        return False

    return True


def bio_quality_score(value: str) -> int:
    cleaned = value.strip()
    score = 0

    if 30 <= len(cleaned) <= 220:
        score += 30
    elif len(cleaned) > 220:
        score += 20
    else:
        score += 10

    if any(token in cleaned.lower() for token in ("python", "fastapi", "ai", "backend", "data", "open source")):
        score += 20

    if "." in cleaned or "," in cleaned:
        score += 10

    return score


def summarize_headline_from_bio(value: str) -> str | None:
    cleaned = clean_string(value)

    if not cleaned:
        return None

    first_sentence = cleaned.split(".")[0].strip()

    if is_weak_headline(first_sentence):
        return None

    if 12 <= len(first_sentence) <= 120:
        return first_sentence

    if len(cleaned) <= 120 and not is_weak_headline(cleaned):
        return cleaned

    truncated = cleaned[:117].rstrip() + "..."
    if is_weak_headline(truncated):
        return None
    return truncated


def is_weak_headline(value: str) -> bool:
    normalized = " ".join(value.lower().strip(" ,.!?").split())
    if not normalized:
        return True

    if normalized in WEAK_HEADLINES:
        return True

    if normalized.startswith(("hi ", "hi,", "hello ", "hello,")):
        tail = normalized.split(maxsplit=1)[1] if " " in normalized else ""
        if tail in WEAK_HEADLINES or tail.startswith("i am a developer") or tail.startswith("i'm a developer"):
            return True

    if normalized.startswith(("welcome to", "welcome!")):
        return True

    return False


def normalize_location_key(value: str) -> str:
    cleaned = value.lower().strip()
    cleaned = cleaned.replace(".", "")
    cleaned = cleaned.replace(",", " ")
    return " ".join(cleaned.split())


def normalize_url(value: str) -> str | None:
    cleaned = clean_string(value)

    if not cleaned:
        return None

    if not cleaned.startswith(("http://", "https://")):
        cleaned = "https://" + cleaned

    parsed = urlparse(cleaned)

    if not parsed.netloc:
        return None

    scheme = "https"
    domain = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.rstrip("/")

    return f"{scheme}://{domain}{path}"


def domain_from_url(value: str) -> str | None:
    try:
        parsed = urlparse(value)
    except Exception:
        return None

    domain = parsed.netloc.lower().removeprefix("www.")
    return domain or None


def is_probable_url(value: str) -> bool:
    parsed = urlparse(value)
    return bool(parsed.scheme in {"http", "https"} and parsed.netloc)


def domain_matches(domain: str, blocked_domain: str) -> bool:
    domain = domain.lower().removeprefix("www.")
    blocked_domain = blocked_domain.lower().removeprefix("www.")
    return domain == blocked_domain or domain.endswith("." + blocked_domain)


def domain_in_set(domain: str, domains: set[str]) -> bool:
    return any(domain_matches(domain, item) for item in domains)


def item_domain_quality(domain: str) -> int:
    if domain_in_set(domain, PLATFORM_DOMAINS):
        return 0

    if domain_in_set(domain, GENERIC_WEBSITE_DOMAINS):
        return 10

    return 100


def extend_terms_from_value(terms: list[str], value: Any) -> None:
    if isinstance(value, list):
        terms.extend(str(item) for item in value if item)
    elif isinstance(value, dict):
        terms.extend(str(key) for key in value.keys() if key)
    elif isinstance(value, str):
        for part in value.replace(";", ",").split(","):
            cleaned = part.strip()
            if cleaned:
                terms.append(cleaned)


def extract_skill_terms(account: dict[str, Any]) -> list[str]:
    terms: list[str] = []

    for field in ("topics", "skills", "tags", "languages"):
        extend_terms_from_value(terms, account.get(field))

    activity_payload = safe_dict(account.get("activity_payload"))

    for field in (
        "topics",
        "skills",
        "tags",
        "languages",
        "top_languages",
        "repo_topics",
        "article_tags",
    ):
        extend_terms_from_value(terms, activity_payload.get(field))

    return terms


def normalize_skill_key(value: str) -> str | None:
    cleaned = value.strip().lower()

    if not cleaned:
        return None

    cleaned = cleaned.replace("_", "-")
    cleaned = cleaned.replace(" ", "-")

    cleaned = "".join(
        char
        for char in cleaned
        if char.isalnum() or char in {"-", ".", "#", "+"}
    )

    cleaned = cleaned.strip("-.")

    if len(cleaned) < 2:
        return None

    return cleaned


def display_skill_label(value: str) -> str:
    normalized = normalize_skill_key(value) or value

    if normalized in SKILL_DISPLAY_OVERRIDES:
        return SKILL_DISPLAY_OVERRIDES[normalized]

    if "-" in normalized:
        return " ".join(part.capitalize() for part in normalized.split("-"))

    return normalized.capitalize()


