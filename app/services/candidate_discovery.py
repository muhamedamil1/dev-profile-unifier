from __future__ import annotations

import re
import unicodedata
from urllib.parse import parse_qs, urlparse

from app.schemas.enums import PlatformSource
from app.schemas.ingestion import (
    CandidateConfidenceHint,
    CandidateDiscoveryResult,
    CandidateDiscoveryWarning,
    CandidateIdentity,
    CandidateType,
)
from app.schemas.requests import ProfileResolveRequest


_GITHUB_USERNAME_RE = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$"
)
_DEVTO_USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,49}$")
_HN_USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
_STACKOVERFLOW_USER_ID_RE = re.compile(r"^\d+$")

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

_FIELD_BY_SOURCE = {
    PlatformSource.GITHUB: "github",
    PlatformSource.DEVTO: "devto",
    PlatformSource.HACKERNEWS: "hackernews",
    PlatformSource.STACKOVERFLOW: "stackoverflow_user_id",
}


class CandidateDiscoveryService:
    """
    Produces platform identifiers worth fetching.

    This service does not call external APIs, does not store database rows, and
    does not decide identity matches. It only creates fetch candidates.
    """

    def __init__(
        self,
        *,
        max_candidates_per_platform: int = 3,
        max_total_candidates: int = 12,
        expand_name_variants_when_direct_input_exists: bool = False,
    ) -> None:
        if max_candidates_per_platform <= 0:
            raise ValueError("max_candidates_per_platform must be positive")

        if max_total_candidates <= 0:
            raise ValueError("max_total_candidates must be positive")

        self.max_candidates_per_platform = max_candidates_per_platform
        self.max_total_candidates = max_total_candidates
        self.expand_name_variants_when_direct_input_exists = (
            expand_name_variants_when_direct_input_exists
        )

    def discover(self, request: ProfileResolveRequest) -> CandidateDiscoveryResult:
        candidates: list[CandidateIdentity] = []
        warnings: list[CandidateDiscoveryWarning] = []

        direct_sources: set[PlatformSource] = set()

        direct_candidates, direct_warnings = self._direct_candidates(request)
        candidates.extend(direct_candidates)
        warnings.extend(direct_warnings)
        direct_sources.update(candidate.source for candidate in direct_candidates)

        if (
            not direct_candidates
            or self.expand_name_variants_when_direct_input_exists
        ):
            name_candidates = self._name_variant_candidates(
                request.name,
                skip_sources=direct_sources,
            )
            candidates.extend(name_candidates)

        deduped = self._dedupe_and_rank(candidates)
        capped, cap_warnings = self._apply_caps(deduped)
        warnings.extend(cap_warnings)

        return CandidateDiscoveryResult(
            candidates=capped,
            warnings=warnings,
        )

    def _direct_candidates(
        self,
        request: ProfileResolveRequest,
    ) -> tuple[list[CandidateIdentity], list[CandidateDiscoveryWarning]]:
        candidates: list[CandidateIdentity] = []
        warnings: list[CandidateDiscoveryWarning] = []

        platform_inputs = request.platform_inputs()

        for source, value in platform_inputs.items():
            candidate = self._candidate_from_platform_value(source, value)

            if candidate is not None:
                candidates.append(candidate)
                continue

            warnings.append(
                CandidateDiscoveryWarning(
                    source=source,
                    field=_FIELD_BY_SOURCE[source],
                    value=value,
                    message=(
                        f"Provided {source.value} value could not be parsed as a valid "
                        "handle, ID, or profile URL and was skipped."
                    ),
                )
            )

        return candidates, warnings

    def _candidate_from_platform_value(
        self,
        source: PlatformSource,
        value: str,
    ) -> CandidateIdentity | None:
        parsed_url = self._parse_maybe_url(value)

        if parsed_url is not None:
            extracted = self._extract_identifier_from_url(source, parsed_url)
            if extracted is None:
                return None

            identifier, reason = extracted

            if not self._is_valid_identifier(source, identifier):
                return None

            candidate_type = (
                CandidateType.PROVIDED_ID
                if source == PlatformSource.STACKOVERFLOW
                else CandidateType.PROVIDED_URL
            )

            normalized_identifier = self._normalize_identifier(source, identifier)

            return CandidateIdentity(
                source=source,
                identifier=normalized_identifier,
                candidate_type=candidate_type,
                confidence_hint=CandidateConfidenceHint.HIGH,
                reason=reason,
                rank=95,
                metadata={"input_kind": "profile_url"},
            )

        identifier = value.strip().lstrip("@")

        if not self._is_valid_identifier(source, identifier):
            return None

        candidate_type = (
            CandidateType.PROVIDED_ID
            if source == PlatformSource.STACKOVERFLOW
            else CandidateType.PROVIDED_HANDLE
        )

        normalized_identifier = self._normalize_identifier(source, identifier)

        return CandidateIdentity(
            source=source,
            identifier=normalized_identifier,
            candidate_type=candidate_type,
            confidence_hint=CandidateConfidenceHint.HIGH,
            reason=f"User provided {source.value} identifier directly",
            rank=100,
            metadata={"input_kind": "direct"},
        )

    def _name_variant_candidates(
        self,
        name: str,
        *,
        skip_sources: set[PlatformSource],
    ) -> list[CandidateIdentity]:
        variants = self._name_variants(name)
        candidates: list[CandidateIdentity] = []

        if PlatformSource.GITHUB not in skip_sources:
            github_variants = [
                variant
                for variant in variants
                if self._is_valid_identifier(PlatformSource.GITHUB, variant)
            ][: self.max_candidates_per_platform]

            for index, variant in enumerate(github_variants):
                candidates.append(
                    CandidateIdentity(
                        source=PlatformSource.GITHUB,
                        identifier=variant,
                        candidate_type=CandidateType.NAME_VARIANT,
                        confidence_hint=CandidateConfidenceHint.LOW,
                        reason="Generated conservative GitHub candidate from normalized name",
                        rank=max(40, 60 - index * 5),
                        metadata={"name_variant_rank": index + 1},
                    )
                )

        if PlatformSource.DEVTO not in skip_sources:
            devto_variants = [
                variant
                for variant in variants
                if self._is_valid_identifier(PlatformSource.DEVTO, variant)
            ][: self.max_candidates_per_platform]

            for index, variant in enumerate(devto_variants):
                candidates.append(
                    CandidateIdentity(
                        source=PlatformSource.DEVTO,
                        identifier=variant,
                        candidate_type=CandidateType.NAME_VARIANT,
                        confidence_hint=CandidateConfidenceHint.LOW,
                        reason="Generated conservative dev.to candidate from normalized name",
                        rank=max(35, 55 - index * 5),
                        metadata={"name_variant_rank": index + 1},
                    )
                )

        if PlatformSource.HACKERNEWS not in skip_sources:
            hn_variants = [
                variant
                for variant in variants
                if self._is_valid_identifier(PlatformSource.HACKERNEWS, variant)
            ][: min(2, self.max_candidates_per_platform)]

            for index, variant in enumerate(hn_variants):
                candidates.append(
                    CandidateIdentity(
                        source=PlatformSource.HACKERNEWS,
                        identifier=variant,
                        candidate_type=CandidateType.NAME_VARIANT,
                        confidence_hint=CandidateConfidenceHint.LOW,
                        reason=(
                            "Generated low-confidence Hacker News candidate from "
                            "normalized name"
                        ),
                        rank=max(25, 35 - index * 5),
                        metadata={
                            "name_variant_rank": index + 1,
                            "hn_conservative_candidate": True,
                        },
                    )
                )

        # Stack Overflow is intentionally excluded from name-only candidate
        # generation because profile identity is numeric-ID based.
        return candidates

    def _name_variants(self, name: str) -> list[str]:
        tokens = self._name_tokens(name)

        if not tokens:
            return []

        variants: list[str] = []

        joined = "".join(tokens)
        if joined:
            variants.append(joined)

        if len(tokens) > 1:
            variants.append("-".join(tokens))
            variants.append("_".join(tokens))

            first = tokens[0]
            last = tokens[-1]

            if first and last:
                variants.append(f"{first}{last}")

                if len(first) > 0:
                    variants.append(f"{first[0]}{last}")

                if len(last) > 0:
                    variants.append(f"{first}{last[0]}")

        return self._dedupe_strings(variants)

    def _name_tokens(self, name: str) -> list[str]:
        normalized = unicodedata.normalize("NFKD", name)
        ascii_name = normalized.encode("ascii", "ignore").decode("ascii")
        lowered = ascii_name.lower()

        cleaned = re.sub(r"[^a-z0-9]+", " ", lowered)
        tokens = [token for token in cleaned.split() if token]

        return tokens[:4]

    def _parse_maybe_url(self, value: str):
        cleaned = value.strip()

        if not cleaned:
            return None

        lower = cleaned.lower()

        known_domain_present = any(
            domain in lower
            for domain in (
                "github.com",
                "dev.to",
                "news.ycombinator.com",
                "hn.algolia.com",
                "stackoverflow.com",
            )
        )

        if not known_domain_present and not lower.startswith(("http://", "https://")):
            return None

        if not lower.startswith(("http://", "https://")):
            cleaned = f"https://{cleaned}"

        parsed = urlparse(cleaned)

        if not parsed.netloc:
            return None

        return parsed

    def _extract_identifier_from_url(
        self,
        source: PlatformSource,
        parsed_url,
    ) -> tuple[str, str] | None:
        host = parsed_url.netloc.lower()
        if host.startswith("www."):
            host = host[4:]

        path_parts = [
            part
            for part in parsed_url.path.strip("/").split("/")
            if part
        ]

        if source == PlatformSource.GITHUB:
            if host != "github.com" or len(path_parts) != 1:
                return None

            username = path_parts[0]
            if username.lower() in _GITHUB_RESERVED_PATHS:
                return None

            return username, "Extracted GitHub username from provided profile URL"

        if source == PlatformSource.DEVTO:
            if host != "dev.to" or len(path_parts) != 1:
                return None

            username = path_parts[0]
            if username.lower() in _DEVTO_RESERVED_PATHS:
                return None

            return username, "Extracted dev.to username from provided profile URL"

        if source == PlatformSource.HACKERNEWS:
            if host == "news.ycombinator.com":
                query = parse_qs(parsed_url.query)
                user_ids = query.get("id")
                if not user_ids:
                    return None

                return user_ids[0], "Extracted Hacker News username from provided profile URL"

            if host == "hn.algolia.com" and len(path_parts) >= 2:
                if path_parts[0].lower() != "user":
                    return None

                return path_parts[1], "Extracted Hacker News username from Algolia profile URL"

            return None

        if source == PlatformSource.STACKOVERFLOW:
            if host != "stackoverflow.com":
                return None

            if len(path_parts) < 2 or path_parts[0].lower() != "users":
                return None

            user_id = path_parts[1]
            if not user_id.isdigit():
                return None

            return user_id, "Extracted Stack Overflow user ID from provided profile URL"

        return None

    def _is_valid_identifier(self, source: PlatformSource, identifier: str) -> bool:
        cleaned = identifier.strip().lstrip("@")

        if source == PlatformSource.GITHUB:
            return bool(_GITHUB_USERNAME_RE.fullmatch(cleaned))

        if source == PlatformSource.DEVTO:
            return bool(_DEVTO_USERNAME_RE.fullmatch(cleaned))

        if source == PlatformSource.HACKERNEWS:
            return bool(_HN_USERNAME_RE.fullmatch(cleaned))

        if source == PlatformSource.STACKOVERFLOW:
            return bool(_STACKOVERFLOW_USER_ID_RE.fullmatch(cleaned))

        return False

    def _normalize_identifier(self, source: PlatformSource, identifier: str) -> str:
        cleaned = identifier.strip().lstrip("@")

        if source == PlatformSource.STACKOVERFLOW:
            return str(int(cleaned))

        return cleaned

    def _dedupe_and_rank(
        self,
        candidates: list[CandidateIdentity],
    ) -> list[CandidateIdentity]:
        best_by_key: dict[str, CandidateIdentity] = {}

        for candidate in candidates:
            existing = best_by_key.get(candidate.dedupe_key)

            if existing is None or candidate.rank > existing.rank:
                best_by_key[candidate.dedupe_key] = candidate

        return sorted(
            best_by_key.values(),
            key=lambda candidate: (
                -candidate.rank,
                candidate.source.value,
                candidate.identifier.lower(),
            ),
        )

    def _apply_caps(
        self,
        candidates: list[CandidateIdentity],
    ) -> tuple[list[CandidateIdentity], list[CandidateDiscoveryWarning]]:
        warnings: list[CandidateDiscoveryWarning] = []
        per_platform_counts: dict[PlatformSource, int] = {}
        capped: list[CandidateIdentity] = []

        for candidate in candidates:
            current_count = per_platform_counts.get(candidate.source, 0)

            if current_count >= self.max_candidates_per_platform:
                continue

            if len(capped) >= self.max_total_candidates:
                warnings.append(
                    CandidateDiscoveryWarning(
                        message=(
                            "Candidate list was capped to prevent excessive "
                            "external API calls."
                        )
                    )
                )
                break

            capped.append(candidate)
            per_platform_counts[candidate.source] = current_count + 1

        return capped, warnings

    def _dedupe_strings(self, values: list[str]) -> list[str]:
        seen: set[str] = set()
        output: list[str] = []

        for value in values:
            cleaned = value.strip().lower()

            if not cleaned or cleaned in seen:
                continue

            seen.add(cleaned)
            output.append(cleaned)

        return output
