from __future__ import annotations

from itertools import combinations
from typing import Any

from app.resolution.comparators import (
    NameCompatibility,
    classify_name_compatibility,
    compare_locations,
    normalized_domain,
    profile_link_match,
)
from app.schemas.conflicts import (
    CONFLICT_PENALTIES,
    ConflictDetectionResult,
    ConflictType,
    DetectedConflict,
)
from app.schemas.enums import ConflictSeverity, PlatformSource
from app.schemas.source_account import SourceAccount


_GENERIC_EMAIL_DOMAINS = {
    "gmail.com",
    "googlemail.com",
    "outlook.com",
    "hotmail.com",
    "live.com",
    "yahoo.com",
    "icloud.com",
    "me.com",
    "proton.me",
    "protonmail.com",
    "pm.me",
}

_GENERIC_TOPICS = {
    "app",
    "apps",
    "api",
    "apis",
    "backend",
    "code",
    "coding",
    "dev",
    "developer",
    "development",
    "engineer",
    "engineering",
    "frontend",
    "fullstack",
    "open-source",
    "opensource",
    "programmer",
    "programming",
    "project",
    "projects",
    "software",
    "tech",
    "technology",
    "tutorial",
    "tutorials",
    "web",
}


class ConflictDetector:
    """
    Deterministic account-to-account conflict detection.

    This class does not score, classify, persist, or mutate accounts.
    """

    def detect(
        self,
        *,
        accounts: list[SourceAccount],
    ) -> ConflictDetectionResult:
        ordered_accounts = sorted(
            accounts,
            key=lambda account: account.expected_source_account_key(),
        )

        conflicts: list[DetectedConflict] = []

        for left, right in combinations(ordered_accounts, 2):
            conflicts.extend(
                self._account_pair_conflicts(
                    left=left,
                    right=right,
                )
            )

        return ConflictDetectionResult(
            conflicts=self._dedupe(conflicts),
        )

    def _account_pair_conflicts(
        self,
        *,
        left: SourceAccount,
        right: SourceAccount,
    ) -> list[DetectedConflict]:
        conflicts: list[DetectedConflict] = []

        name_conflict = self._name_conflict(left, right)
        if name_conflict is not None:
            conflicts.append(name_conflict)

        website_conflict = self._website_conflict(left, right)
        if website_conflict is not None:
            conflicts.append(website_conflict)

        location_conflict = self._location_conflict(left, right)
        if location_conflict is not None:
            conflicts.append(location_conflict)

        email_conflict = self._email_conflict(left, right)
        if email_conflict is not None:
            conflicts.append(email_conflict)

        topic_mismatch = self._topic_mismatch(left, right)
        if topic_mismatch is not None:
            conflicts.append(topic_mismatch)

        return conflicts

    def _name_conflict(
        self,
        left: SourceAccount,
        right: SourceAccount,
    ) -> DetectedConflict | None:
        if self._should_skip_name_conflict(left) or self._should_skip_name_conflict(right):
            return None

        compatibility = classify_name_compatibility(left.display_name, right.display_name)

        if compatibility.compatibility != NameCompatibility.CONFLICTING:
            return None

        return self._make_conflict(
            conflict_type=ConflictType.NAME_CONFLICT,
            severity=ConflictSeverity.HIGH,
            left=left,
            right=right,
            description="Accounts have clearly incompatible strong display names.",
            metadata={
                "left_display_name": left.display_name,
                "right_display_name": right.display_name,
                "left_normalized_name": compatibility.left_normalized,
                "right_normalized_name": compatibility.right_normalized,
                "left_tokens": compatibility.left_tokens,
                "right_tokens": compatibility.right_tokens,
                "overlap_tokens": compatibility.overlap_tokens,
                "name_similarity": compatibility.similarity,
                "compatibility": compatibility.compatibility.value,
                "compatibility_reason": compatibility.reason,
                "conflict_basis": "display_name",
                "normalized_value": (
                    f"{compatibility.left_normalized}:{compatibility.right_normalized}"
                ),
            },
        )

    def _website_conflict(
        self,
        left: SourceAccount,
        right: SourceAccount,
    ) -> DetectedConflict | None:
        left_domain = normalized_domain(left.website_url)
        right_domain = normalized_domain(right.website_url)

        if left_domain is None or right_domain is None:
            return None

        if left_domain == right_domain:
            return None

        if (
            self._website_points_to_profile(left.website_url, right)
            or self._website_points_to_profile(right.website_url, left)
        ):
            return None

        return self._make_conflict(
            conflict_type=ConflictType.WEBSITE_CONFLICT,
            severity=ConflictSeverity.LOW,
            penalty=-0.05,
            left=left,
            right=right,
            description="Accounts list different personal website domains; treated as weak identity tension only.",
            metadata={
                "left_website_url": left.website_url,
                "right_website_url": right.website_url,
                "left_domain": left_domain,
                "right_domain": right_domain,
                "weak_identity_signal": True,
                "non_blocking": True,
                "conflict_basis": "website_domain",
                "normalized_value": f"{left_domain}:{right_domain}",
            },
        )

    def _location_conflict(
        self,
        left: SourceAccount,
        right: SourceAccount,
    ) -> DetectedConflict | None:
        comparison = compare_locations(left.location, right.location)

        if comparison.left_normalized is None or comparison.right_normalized is None:
            return None

        if comparison.same or comparison.overlap:
            return None

        return self._make_conflict(
            conflict_type=ConflictType.LOCATION_CONFLICT,
            severity=ConflictSeverity.LOW,
            left=left,
            right=right,
            description="Accounts list different non-overlapping locations.",
            metadata={
                "left_location": left.location,
                "right_location": right.location,
                "left_normalized_location": comparison.left_normalized,
                "right_normalized_location": comparison.right_normalized,
                "overlap_tokens": comparison.overlap_tokens,
                "weak_identity_signal": True,
                "conflict_basis": "location",
                "normalized_value": f"{comparison.left_normalized}:{comparison.right_normalized}",
            },
        )

    def _website_points_to_profile(
        self,
        website_url: str | None,
        target: SourceAccount,
    ) -> bool:
        if not website_url or not target.profile_url:
            return False

        return profile_link_match([website_url], target.profile_url).matched

    def _email_conflict(
        self,
        left: SourceAccount,
        right: SourceAccount,
    ) -> DetectedConflict | None:
        left_email_hash = self._clean_optional_value(left.email_hash)
        right_email_hash = self._clean_optional_value(right.email_hash)

        if left_email_hash and right_email_hash:
            if left_email_hash == right_email_hash:
                return None

            return self._make_conflict(
                conflict_type=ConflictType.EMAIL_CONFLICT,
                severity=ConflictSeverity.HIGH,
                left=left,
                right=right,
                description="Accounts expose different public email hashes.",
                metadata={
                    "left_email_hash": left_email_hash,
                    "right_email_hash": right_email_hash,
                    "normalized_value": f"{left_email_hash}:{right_email_hash}",
                    "conflict_basis": "email_hash",
                },
            )

        left_email_domain = self._email_domain(left)
        right_email_domain = self._email_domain(right)

        if left_email_domain and right_email_domain and left_email_domain != right_email_domain:
            if self._is_generic_email_domain(left_email_domain) or self._is_generic_email_domain(right_email_domain):
                return None

            return self._make_conflict(
                conflict_type=ConflictType.EMAIL_CONFLICT,
                severity=ConflictSeverity.MEDIUM,
                penalty=-0.12,
                left=left,
                right=right,
                description="Accounts expose different non-generic public email domains.",
                metadata={
                    "left_email_domain": left_email_domain,
                    "right_email_domain": right_email_domain,
                    "normalized_value": f"{left_email_domain}:{right_email_domain}",
                    "weak_identity_signal": True,
                    "conflict_basis": "email_domain",
                },
            )

        return None

    def _topic_mismatch(
        self,
        left: SourceAccount,
        right: SourceAccount,
    ) -> DetectedConflict | None:
        if left.source == PlatformSource.HACKERNEWS or right.source == PlatformSource.HACKERNEWS:
            return None

        left_topics = self._meaningful_topics(left.topics)
        right_topics = self._meaningful_topics(right.topics)

        if len(left_topics) < 6 or len(right_topics) < 6:
            return None

        overlap = sorted(left_topics & right_topics)

        if overlap:
            return None

        return self._make_conflict(
            conflict_type=ConflictType.TOPIC_MISMATCH,
            severity=ConflictSeverity.LOW,
            left=left,
            right=right,
            description="Accounts have substantial but non-overlapping technical topic sets.",
            metadata={
                "left_topics": sorted(left_topics)[:30],
                "right_topics": sorted(right_topics)[:30],
                "left_topic_count": len(left_topics),
                "right_topic_count": len(right_topics),
                "overlap_topics": overlap,
                "weak_identity_signal": True,
                "conflict_basis": "topics",
                "normalized_value": f"{','.join(sorted(left_topics)[:10])}:{','.join(sorted(right_topics)[:10])}",
            },
        )

    def _make_conflict(
        self,
        *,
        conflict_type: ConflictType,
        severity: ConflictSeverity,
        left: SourceAccount,
        right: SourceAccount,
        description: str,
        metadata: dict[str, Any],
        penalty: float | None = None,
    ) -> DetectedConflict:
        final_metadata = {
            **metadata,
            "left_source": left.source.value,
            "right_source": right.source.value,
        }

        if left.source == PlatformSource.HACKERNEWS or right.source == PlatformSource.HACKERNEWS:
            final_metadata.setdefault("hn_conservative", True)

        return DetectedConflict(
            conflict_type=conflict_type,
            severity=severity,
            penalty=penalty if penalty is not None else CONFLICT_PENALTIES[conflict_type],
            source_account_id=left.id,
            source_account_key=left.expected_source_account_key(),
            source=left.source,
            target_account_id=right.id,
            target_account_key=right.expected_source_account_key(),
            target_source=right.source,
            description=description,
            metadata=final_metadata,
        )

    def _should_skip_name_conflict(self, account: SourceAccount) -> bool:
        if not account.display_name:
            return True

        display_name = str(account.display_name).strip().lower()
        handle = str(account.handle or "").strip().lower()

        if not display_name:
            return True

        if account.source == PlatformSource.HACKERNEWS:
            return True

        if handle and display_name == handle:
            return True

        return False

    def _email_domain(self, account: SourceAccount) -> str | None:
        payload = account.activity_payload or {}

        value = payload.get("email_domain")
        if value is None:
            return None

        cleaned = str(value).strip().lower()
        if not cleaned:
            return None

        if "@" in cleaned:
            cleaned = cleaned.rsplit("@", 1)[1]

        return cleaned or None

    def _is_generic_email_domain(self, domain: str) -> bool:
        return domain in _GENERIC_EMAIL_DOMAINS

    def _meaningful_topics(self, topics: list[str]) -> set[str]:
        cleaned_topics: set[str] = set()

        for topic in topics:
            cleaned = str(topic).strip().lower()

            if not cleaned:
                continue

            if cleaned in _GENERIC_TOPICS:
                continue

            if len(cleaned) < 2:
                continue

            cleaned_topics.add(cleaned)

        return cleaned_topics

    def _clean_optional_value(self, value: Any) -> str | None:
        if value is None:
            return None

        cleaned = str(value).strip().lower()
        return cleaned or None

    def _dedupe(self, conflicts: list[DetectedConflict]) -> list[DetectedConflict]:
        best_by_key: dict[str, DetectedConflict] = {}

        severity_rank = {
            "low": 1,
            "medium": 2,
            "high": 3,
            "critical": 4,
        }

        for item in conflicts:
            existing = best_by_key.get(item.dedupe_key)

            if existing is None:
                best_by_key[item.dedupe_key] = item
                continue

            existing_rank = severity_rank.get(existing.severity.value, 0)
            item_rank = severity_rank.get(item.severity.value, 0)

            if item_rank > existing_rank:
                best_by_key[item.dedupe_key] = item

        return sorted(
            best_by_key.values(),
            key=lambda item: (
                item.source.value,
                item.target_source.value,
                item.conflict_type.value,
                item.severity.value,
            ),
        )
