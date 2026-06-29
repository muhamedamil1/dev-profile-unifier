from __future__ import annotations

from itertools import combinations
from typing import Any

from app.resolution.comparators import (
    compare_handles,
    compare_locations,
    compare_names,
    is_hackernews_account,
    keyword_overlap,
    profile_link_match,
    same_website_domain,
    topic_overlap,
)
from app.schemas.enums import EvidenceDirection, PlatformSource
from app.schemas.evidence import (
    EVIDENCE_INDEPENDENCE_GROUPS,
    EVIDENCE_WEIGHTS,
    EvidenceExtractionResult,
    EvidenceIndependenceGroup,
    EvidenceTargetType,
    EvidenceType,
    ExtractedEvidence,
)
from app.schemas.requests import ProfileResolveRequest
from app.schemas.source_account import SourceAccount


_GITHUB_RESERVED_PATHS = {
    "about",
    "apps",
    "collections",
    "customer-stories",
    "events",
    "explore",
    "features",
    "issues",
    "login",
    "marketplace",
    "new",
    "notifications",
    "orgs",
    "pricing",
    "pulls",
    "readme",
    "search",
    "settings",
    "sponsors",
    "topics",
    "trending",
}

_DEVTO_RESERVED_PATHS = {
    "about",
    "api",
    "code-of-conduct",
    "contact",
    "dashboard",
    "enter",
    "listings",
    "new",
    "pod",
    "podcasts",
    "privacy",
    "search",
    "settings",
    "shop",
    "signin",
    "tag",
    "tags",
    "t",
    "terms",
    "top",
    "videos",
}

_WEAK_EVIDENCE_TYPES = {
    EvidenceType.PARTIAL_NAME_MATCH,
    EvidenceType.SIMILAR_HANDLE,
    EvidenceType.SAME_LOCATION,
    EvidenceType.LOCATION_OVERLAP,
    EvidenceType.BIO_KEYWORD_OVERLAP,
    EvidenceType.TOPIC_OVERLAP,
}


class EvidenceExtractor:
    """
    Deterministic evidence extraction.

    This class does not score, classify, persist, or mutate accounts.
    """

    def extract(
        self,
        *,
        request: ProfileResolveRequest,
        accounts: list[SourceAccount],
    ) -> EvidenceExtractionResult:
        evidence: list[ExtractedEvidence] = []
        sorted_accounts = sorted(accounts, key=self._account_sort_key)

        for account in sorted_accounts:
            evidence.extend(
                self._request_to_account_evidence(
                    request=request,
                    account=account,
                )
            )

        for left, right in combinations(sorted_accounts, 2):
            evidence.extend(
                self._account_pair_evidence(
                    left=left,
                    right=right,
                )
            )

        return EvidenceExtractionResult(
            evidence=self._dedupe(evidence),
        )

    def _request_to_account_evidence(
        self,
        *,
        request: ProfileResolveRequest,
        account: SourceAccount,
    ) -> list[ExtractedEvidence]:
        evidence: list[ExtractedEvidence] = []

        handle_match = self._input_handle_match(request, account)
        if handle_match is not None:
            evidence.append(handle_match)

        exact_or_partial_name = self._request_name_match(request, account)
        if exact_or_partial_name is not None:
            evidence.append(exact_or_partial_name)

        email_match = self._email_hint_match(request, account)
        if email_match is not None:
            evidence.append(email_match)

        email_domain_match = self._email_domain_match(request, account)
        if email_domain_match is not None:
            evidence.append(email_domain_match)

        return evidence

    def _account_pair_evidence(
        self,
        *,
        left: SourceAccount,
        right: SourceAccount,
    ) -> list[ExtractedEvidence]:
        evidence: list[ExtractedEvidence] = []

        website = self._same_website(left, right)
        if website is not None:
            evidence.append(website)

        left_to_right = self._direct_profile_link(left, right)
        right_to_left = self._direct_profile_link(right, left)

        if left_to_right is not None:
            evidence.append(left_to_right)

        if right_to_left is not None:
            evidence.append(right_to_left)

        if left_to_right is not None and right_to_left is not None:
            reciprocal = self._reciprocal_profile_link(left, right)
            if reciprocal is not None:
                evidence.append(reciprocal)

        similar_handle = self._similar_handle(left, right)
        if similar_handle is not None:
            evidence.append(similar_handle)

        location = self._location_match(left, right)
        if location is not None:
            evidence.append(location)

        bio = self._bio_keyword_overlap(left, right)
        if bio is not None:
            evidence.append(bio)

        topics = self._topic_overlap(left, right)
        if topics is not None:
            evidence.append(topics)

        return evidence

    def _input_handle_match(
        self,
        request: ProfileResolveRequest,
        account: SourceAccount,
    ) -> ExtractedEvidence | None:
        requested_identifier = self._request_identifier_for_source(request, account.source)

        if requested_identifier is None:
            return None

        account_identifiers = self._account_identifiers(account)

        if requested_identifier.lower() not in account_identifiers:
            return None

        return self._make_evidence(
            evidence_type=EvidenceType.INPUT_HANDLE_MATCH,
            target_type=EvidenceTargetType.REQUEST,
            account=account,
            reason=(
                f"{account.source.value} account matched the platform identifier "
                "directly provided in the request."
            ),
            metadata={
                "requested_identifier": requested_identifier,
                "account_identifiers": sorted(account_identifiers),
                "normalized_value": requested_identifier.lower(),
            },
        )

    def _request_name_match(
        self,
        request: ProfileResolveRequest,
        account: SourceAccount,
    ) -> ExtractedEvidence | None:
        display_name = account.display_name

        if self._should_skip_strong_hn_name_match(account):
            return None

        comparison = compare_names(request.name, display_name)

        if comparison.exact:
            return self._make_evidence(
                evidence_type=EvidenceType.EXACT_NAME_MATCH,
                target_type=EvidenceTargetType.REQUEST,
                account=account,
                reason="Account display name exactly matched the requested name.",
                metadata={
                    "request_name": request.name,
                    "account_display_name": display_name,
                    "normalized_request_name": comparison.left_normalized,
                    "normalized_account_name": comparison.right_normalized,
                    "normalized_value": comparison.right_normalized,
                },
            )

        if comparison.partial:
            return self._make_evidence(
                evidence_type=EvidenceType.PARTIAL_NAME_MATCH,
                target_type=EvidenceTargetType.REQUEST,
                account=account,
                reason="Account display name partially matched the requested name.",
                metadata={
                    "request_name": request.name,
                    "account_display_name": display_name,
                    "normalized_request_name": comparison.left_normalized,
                    "normalized_account_name": comparison.right_normalized,
                    "overlap_tokens": comparison.overlap_tokens,
                    "normalized_value": ",".join(comparison.overlap_tokens),
                },
            )

        return None

    def _email_hint_match(
        self,
        request: ProfileResolveRequest,
        account: SourceAccount,
    ) -> ExtractedEvidence | None:
        email_hash = request.email_hint_sha256()

        if not email_hash or not account.email_hash:
            return None

        if email_hash != account.email_hash:
            return None

        return self._make_evidence(
            evidence_type=EvidenceType.EMAIL_HINT_MATCH,
            target_type=EvidenceTargetType.REQUEST,
            account=account,
            reason="Account public email hash matched the request email hint hash.",
            metadata={
                "email_hint_sha256": email_hash,
                "normalized_value": email_hash,
            },
        )

    def _email_domain_match(
        self,
        request: ProfileResolveRequest,
        account: SourceAccount,
    ) -> ExtractedEvidence | None:
        request_domain = self._request_email_domain(request)
        account_domain = self._account_email_domain(account)

        if request_domain is None or account_domain is None:
            return None

        if request_domain != account_domain:
            return None

        return self._make_evidence(
            evidence_type=EvidenceType.EMAIL_DOMAIN_MATCH,
            target_type=EvidenceTargetType.REQUEST,
            account=account,
            reason="Account public email domain matched the request email hint domain.",
            metadata={
                "request_email_domain": request_domain,
                "account_email_domain": account_domain,
                "normalized_value": request_domain,
            },
        )

    def _same_website(
        self,
        left: SourceAccount,
        right: SourceAccount,
    ) -> ExtractedEvidence | None:
        matched, left_domain, right_domain, normalized_domain = same_website_domain(
            left.website_url,
            right.website_url,
        )

        if not matched or normalized_domain is None:
            return None

        return self._make_pair_evidence(
            evidence_type=EvidenceType.SAME_WEBSITE,
            left=left,
            right=right,
            reason="Accounts share the same normalized website domain.",
            metadata={
                "left_website_url": left.website_url,
                "right_website_url": right.website_url,
                "left_domain": left_domain,
                "right_domain": right_domain,
                "normalized_value": normalized_domain,
            },
        )

    def _direct_profile_link(
        self,
        source_account: SourceAccount,
        target_account: SourceAccount,
    ) -> ExtractedEvidence | None:
        if not source_account.outbound_links or not target_account.profile_url:
            return None

        match = profile_link_match(
            source_account.outbound_links,
            target_account.profile_url,
        )

        if not match.matched:
            return None

        return self._make_pair_evidence(
            evidence_type=EvidenceType.DIRECT_PROFILE_LINK,
            left=source_account,
            right=target_account,
            reason=(
                f"{source_account.source.value} account directly links to "
                f"{target_account.source.value} profile."
            ),
            metadata={
                "source_link": match.source_link,
                "target_profile_url": match.target_profile_url,
                "normalized_value": match.normalized_value,
                "directional": True,
            },
        )

    def _reciprocal_profile_link(
        self,
        left: SourceAccount,
        right: SourceAccount,
    ) -> ExtractedEvidence | None:
        return self._make_pair_evidence(
            evidence_type=EvidenceType.RECIPROCAL_PROFILE_LINK,
            left=left,
            right=right,
            reason="Accounts link to each other's platform profiles.",
            metadata={
                "left_profile_url": left.profile_url,
                "right_profile_url": right.profile_url,
                "normalized_value": self._pair_key(left, right),
            },
        )

    def _similar_handle(
        self,
        left: SourceAccount,
        right: SourceAccount,
    ) -> ExtractedEvidence | None:
        if self._has_display_name_like_stackoverflow_handle(left) or self._has_display_name_like_stackoverflow_handle(right):
            return None

        comparison = compare_handles(left.handle, right.handle)

        if not comparison.similar:
            return None

        metadata: dict[str, Any] = {
            "left_handle": left.handle,
            "right_handle": right.handle,
            "left_normalized": comparison.left_normalized,
            "right_normalized": comparison.right_normalized,
            "similarity": comparison.similarity,
            "exact": comparison.exact,
            "normalized_value": f"{comparison.left_normalized}:{comparison.right_normalized}",
        }

        if is_hackernews_account(left.source) or is_hackernews_account(right.source):
            metadata["hn_conservative"] = True
            metadata["weak_identity_signal"] = True

        return self._make_pair_evidence(
            evidence_type=EvidenceType.SIMILAR_HANDLE,
            left=left,
            right=right,
            reason="Accounts use the same or highly similar handle.",
            metadata=metadata,
        )

    def _location_match(
        self,
        left: SourceAccount,
        right: SourceAccount,
    ) -> ExtractedEvidence | None:
        comparison = compare_locations(left.location, right.location)

        if comparison.same:
            return self._make_pair_evidence(
                evidence_type=EvidenceType.SAME_LOCATION,
                left=left,
                right=right,
                reason="Accounts share the same normalized location.",
                metadata={
                    "left_location": left.location,
                    "right_location": right.location,
                    "left_normalized": comparison.left_normalized,
                    "right_normalized": comparison.right_normalized,
                    "normalized_value": comparison.left_normalized,
                },
            )

        if comparison.overlap:
            return self._make_pair_evidence(
                evidence_type=EvidenceType.LOCATION_OVERLAP,
                left=left,
                right=right,
                reason="Accounts have overlapping location tokens.",
                metadata={
                    "left_location": left.location,
                    "right_location": right.location,
                    "left_normalized": comparison.left_normalized,
                    "right_normalized": comparison.right_normalized,
                    "overlap_tokens": comparison.overlap_tokens,
                    "normalized_value": ",".join(comparison.overlap_tokens),
                },
            )

        return None

    def _bio_keyword_overlap(
        self,
        left: SourceAccount,
        right: SourceAccount,
    ) -> ExtractedEvidence | None:
        overlap = keyword_overlap(left.bio, right.bio, minimum=2)

        if not overlap:
            return None

        return self._make_pair_evidence(
            evidence_type=EvidenceType.BIO_KEYWORD_OVERLAP,
            left=left,
            right=right,
            reason="Accounts share meaningful bio/about keywords.",
            metadata={
                "overlap_keywords": overlap[:20],
                "overlap_count": len(overlap),
                "normalized_value": ",".join(overlap[:10]),
            },
        )

    def _topic_overlap(
        self,
        left: SourceAccount,
        right: SourceAccount,
    ) -> ExtractedEvidence | None:
        overlap = topic_overlap(left.topics, right.topics, minimum=2)

        if not overlap:
            return None

        weight = min(
            EVIDENCE_WEIGHTS[EvidenceType.TOPIC_OVERLAP] * min(len(overlap), 2),
            0.12,
        )

        return self._make_pair_evidence(
            evidence_type=EvidenceType.TOPIC_OVERLAP,
            left=left,
            right=right,
            reason="Accounts share normalized technical topics.",
            weight=weight,
            metadata={
                "overlap_topics": overlap[:20],
                "overlap_count": len(overlap),
                "normalized_value": ",".join(overlap[:10]),
                "topic_weight_capped": True,
            },
        )

    def _make_evidence(
        self,
        *,
        evidence_type: EvidenceType,
        target_type: EvidenceTargetType,
        account: SourceAccount,
        reason: str,
        metadata: dict[str, Any],
        weight: float | None = None,
    ) -> ExtractedEvidence:
        final_metadata = {
            **metadata,
            "independence_group": EVIDENCE_INDEPENDENCE_GROUPS[evidence_type].value,
        }

        if evidence_type in _WEAK_EVIDENCE_TYPES:
            final_metadata.setdefault("weak_identity_signal", True)

        if account.source == PlatformSource.HACKERNEWS:
            final_metadata.setdefault("hn_conservative", True)

        return ExtractedEvidence(
            evidence_type=evidence_type,
            direction=EvidenceDirection.POSITIVE,
            target_type=target_type,
            source_account_id=account.id,
            source_account_key=account.expected_source_account_key(),
            source=account.source,
            weight=weight if weight is not None else EVIDENCE_WEIGHTS[evidence_type],
            independence_group=EVIDENCE_INDEPENDENCE_GROUPS[evidence_type],
            reason=reason,
            metadata=final_metadata,
        )

    def _make_pair_evidence(
        self,
        *,
        evidence_type: EvidenceType,
        left: SourceAccount,
        right: SourceAccount,
        reason: str,
        metadata: dict[str, Any],
        weight: float | None = None,
    ) -> ExtractedEvidence:
        final_metadata = {
            **metadata,
            "left_source": left.source.value,
            "right_source": right.source.value,
            "independence_group": EVIDENCE_INDEPENDENCE_GROUPS[evidence_type].value,
        }

        if evidence_type in _WEAK_EVIDENCE_TYPES:
            final_metadata.setdefault("weak_identity_signal", True)

        if left.source == PlatformSource.HACKERNEWS or right.source == PlatformSource.HACKERNEWS:
            final_metadata.setdefault("hn_conservative", True)

        return ExtractedEvidence(
            evidence_type=evidence_type,
            direction=EvidenceDirection.POSITIVE,
            target_type=EvidenceTargetType.ACCOUNT_PAIR,
            source_account_id=left.id,
            source_account_key=left.expected_source_account_key(),
            source=left.source,
            target_account_id=right.id,
            target_account_key=right.expected_source_account_key(),
            target_source=right.source,
            weight=weight if weight is not None else EVIDENCE_WEIGHTS[evidence_type],
            independence_group=EVIDENCE_INDEPENDENCE_GROUPS[evidence_type],
            reason=reason,
            metadata=final_metadata,
        )

    def _request_identifier_for_source(
        self,
        request: ProfileResolveRequest,
        source: PlatformSource,
    ) -> str | None:
        platform_inputs = request.platform_inputs()
        value = platform_inputs.get(source)

        if value is None:
            return None

        cleaned = str(value).strip().lstrip("@")

        if source == PlatformSource.STACKOVERFLOW:
            return self._extract_stackoverflow_identifier(cleaned)

        if source == PlatformSource.GITHUB:
            return self._extract_simple_profile_identifier(
                cleaned,
                expected_host="github.com",
                reserved_paths=_GITHUB_RESERVED_PATHS,
            )

        if source == PlatformSource.DEVTO:
            return self._extract_simple_profile_identifier(
                cleaned,
                expected_host="dev.to",
                reserved_paths=_DEVTO_RESERVED_PATHS,
            )

        if source == PlatformSource.HACKERNEWS:
            return self._extract_hackernews_identifier(cleaned)

        return cleaned.lower() or None

    def _extract_simple_profile_identifier(
        self,
        value: str,
        *,
        expected_host: str,
        reserved_paths: set[str] | None = None,
    ) -> str | None:
        lower_value = value.lower()

        if "://" not in lower_value and expected_host not in lower_value:
            return value.strip().lstrip("@").lower() or None

        from urllib.parse import urlparse

        candidate_url = value
        if not candidate_url.startswith(("http://", "https://")):
            candidate_url = f"https://{candidate_url}"

        parsed = urlparse(candidate_url)
        host = parsed.netloc.lower()

        if host.startswith("www."):
            host = host[4:]

        if host != expected_host:
            return None

        path_parts = [
            part
            for part in parsed.path.strip("/").split("/")
            if part
        ]

        if len(path_parts) != 1:
            return None

        identifier = path_parts[0].lower()
        if reserved_paths and identifier in reserved_paths:
            return None

        return identifier

    def _extract_stackoverflow_identifier(self, value: str) -> str | None:
        if value.isdigit():
            return str(int(value))

        from urllib.parse import urlparse

        candidate_url = value
        if not candidate_url.startswith(("http://", "https://")):
            candidate_url = f"https://{candidate_url}"

        parsed = urlparse(candidate_url)
        host = parsed.netloc.lower()

        if host.startswith("www."):
            host = host[4:]

        if host != "stackoverflow.com":
            return None

        path_parts = [
            part
            for part in parsed.path.strip("/").split("/")
            if part
        ]

        if len(path_parts) < 2 or path_parts[0].lower() != "users":
            return None

        if not path_parts[1].isdigit():
            return None

        return str(int(path_parts[1]))

    def _extract_hackernews_identifier(self, value: str) -> str | None:
        lower_value = value.lower()

        if "://" not in lower_value and "news.ycombinator.com" not in lower_value and "hn.algolia.com" not in lower_value:
            return value.strip().lstrip("@").lower() or None

        from urllib.parse import parse_qs, urlparse

        candidate_url = value
        if not candidate_url.startswith(("http://", "https://")):
            candidate_url = f"https://{candidate_url}"

        parsed = urlparse(candidate_url)
        host = parsed.netloc.lower()

        if host.startswith("www."):
            host = host[4:]

        if host == "news.ycombinator.com":
            user_ids = parse_qs(parsed.query).get("id")
            if user_ids and user_ids[0].strip():
                return user_ids[0].strip().lower()
            return None

        if host == "hn.algolia.com":
            path_parts = [
                part
                for part in parsed.path.strip("/").split("/")
                if part
            ]

            if len(path_parts) >= 2 and path_parts[0].lower() == "user":
                return path_parts[1].lower()

        return None

    def _account_identifiers(self, account: SourceAccount) -> set[str]:
        identifiers: set[str] = set()

        if account.source_user_id:
            identifiers.add(str(account.source_user_id).strip().lower())

            if account.source == PlatformSource.STACKOVERFLOW and str(account.source_user_id).isdigit():
                identifiers.add(str(int(str(account.source_user_id))))

        if account.handle:
            identifiers.add(str(account.handle).strip().lstrip("@").lower())

        return {
            identifier
            for identifier in identifiers
            if identifier
        }

    def _request_email_domain(self, request: ProfileResolveRequest) -> str | None:
        if not request.email_hint or "@" not in request.email_hint:
            return None

        domain = request.email_hint.rsplit("@", 1)[1].strip().lower()
        return domain or None

    def _account_email_domain(self, account: SourceAccount) -> str | None:
        value = account.activity_payload.get("email_domain")

        if value is None:
            return None

        domain = str(value).strip().lower()
        return domain or None

    def _has_display_name_like_stackoverflow_handle(self, account: SourceAccount) -> bool:
        return account.source == PlatformSource.STACKOVERFLOW and bool(
            account.handle and any(char.isspace() for char in account.handle)
        )

    def _account_sort_key(self, account: SourceAccount) -> tuple[str, str, str, str]:
        try:
            source_account_key = account.expected_source_account_key()
        except ValueError:
            source_account_key = ""

        return (
            source_account_key,
            account.source.value,
            str(account.handle or "").lower(),
            str(account.source_user_id or "").lower(),
        )

    def _should_skip_strong_hn_name_match(self, account: SourceAccount) -> bool:
        if account.source != PlatformSource.HACKERNEWS:
            return False

        if not account.display_name or not account.handle:
            return True

        return account.display_name.strip().lower() == account.handle.strip().lower()

    def _pair_key(self, left: SourceAccount, right: SourceAccount) -> str:
        keys = sorted(
            [
                left.expected_source_account_key(),
                right.expected_source_account_key(),
            ]
        )
        return "::".join(keys)

    def _dedupe(self, evidence: list[ExtractedEvidence]) -> list[ExtractedEvidence]:
        best_by_key: dict[str, ExtractedEvidence] = {}

        for item in evidence:
            existing = best_by_key.get(item.dedupe_key)

            if existing is None or item.weight > existing.weight:
                best_by_key[item.dedupe_key] = item

        return sorted(
            best_by_key.values(),
            key=lambda item: (
                item.source.value,
                item.target_source.value if item.target_source else "",
                item.evidence_type.value,
                -item.weight,
            ),
        )