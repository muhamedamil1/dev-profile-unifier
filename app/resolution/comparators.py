from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Any
from urllib.parse import parse_qs, urlparse

from rapidfuzz import fuzz

from app.schemas.enums import PlatformSource
from app.utils.normalization import clean_optional_str, html_to_text
from app.utils.urls import normalize_url


_NAME_TOKEN_RE = re.compile(r"[a-z0-9]+")
_BIO_KEYWORD_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+#.-]{1,30}")

_NAME_STOPWORDS = {
    "mr",
    "mrs",
    "ms",
    "dr",
    "prof",
    "sir",
}

_WEAK_NAME_TOKENS = {
    "john",
    "mike",
    "michael",
    "david",
    "james",
    "robert",
    "daniel",
    "paul",
    "mark",
    "alex",
    "sam",
    "mohammed",
    "muhammed",
    "muhammad",
    "ali",
    "khan",
    "singh",
    "patel",
}

_BIO_ALLOWED_SHORT_KEYWORDS = {
    "ai",
    "ar",
    "bi",
    "ci",
    "cd",
    "db",
    "dl",
    "dx",
    "js",
    "ml",
    "nlp",
    "qa",
    "rl",
    "ts",
    "ui",
    "ux",
    "vr",
    "xr",
}

_BIO_PHRASE_ALIASES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bartificial\s+intelligence\b"), "ai"),
    (re.compile(r"\bmachine\s+learning\b"), "ml"),
    (re.compile(r"\bdeep\s+learning\b"), "dl"),
    (re.compile(r"\bnatural\s+language\s+processing\b"), "nlp"),
    (re.compile(r"\blarge\s+language\s+models?\b"), "llm"),
    (re.compile(r"\buser\s+interface\b"), "ui"),
    (re.compile(r"\buser\s+experience\b"), "ux"),
    (re.compile(r"\bcontinuous\s+integration\b"), "ci"),
    (re.compile(r"\bcontinuous\s+deployment\b"), "cd"),
)

_BIO_KEYWORD_ALIASES = {
    "amazonwebservices": "aws",
    "aws": "aws",
    "back-end": "backend",
    "backend": "backend",
    "backends": "backend",
    "c#": "csharp",
    "c++": "cpp",
    "dev-ops": "devops",
    "devops": "devops",
    "dockerized": "docker",
    "fast-api": "fastapi",
    "front-end": "frontend",
    "frontend": "frontend",
    "frontends": "frontend",
    "gen-ai": "genai",
    "generative-ai": "genai",
    "golang": "golang",
    "graphql": "graphql",
    "java-script": "javascript",
    "javascript": "javascript",
    "js": "javascript",
    "k8s": "kubernetes",
    "kubernetes": "kubernetes",
    "llms": "llm",
    "next.js": "nextjs",
    "nextjs": "nextjs",
    "node.js": "nodejs",
    "nodejs": "nodejs",
    "postgresql": "postgres",
    "react.js": "react",
    "reactjs": "react",
    "tailwind-css": "tailwind",
    "typescript": "typescript",
    "ts": "typescript",
}

_BIO_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "how",
    "i",
    "in",
    "is",
    "it",
    "me",
    "my",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "with",
    "you",
    "your",
    "about",
    "also",
    "build",
    "building",
    "built",
    "code",
    "coder",
    "coding",
    "creator",
    "developer",
    "developers",
    "engineer",
    "engineering",
    "enthusiast",
    "expert",
    "fan",
    "founder",
    "hacker",
    "maker",
    "open",
    "person",
    "software",
    "source",
    "stuff",
    "tools",
    "work",
    "working",
    "writes",
    "writing",
}

_GENERIC_WEBSITE_DOMAINS = {
    "github.com",
    "www.github.com",
    "dev.to",
    "www.dev.to",
    "stackoverflow.com",
    "www.stackoverflow.com",
    "news.ycombinator.com",
    "hn.algolia.com",
    "linkedin.com",
    "www.linkedin.com",
    "twitter.com",
    "x.com",
    "www.twitter.com",
    "www.x.com",
}

_WEAK_SHARED_WEBSITE_DOMAINS = {
    "medium.com",
    "substack.com",
    "hashnode.dev",
    "about.me",
    "linktr.ee",
    "bio.link",
    "notion.site",
    "vercel.app",
    "netlify.app",
    "pages.dev",
}

_VAGUE_LOCATIONS = {
    "earth",
    "world",
    "worldwide",
    "internet",
    "remote",
    "online",
    "global",
    "somewhere",
    "everywhere",
    "localhost",
}

_LOCATION_ALIASES = {
    "bengaluru": "bangalore",
    "bangaluru": "bangalore",
    "blr": "bangalore",
    "nyc": "new york",
    "sf": "san francisco",
    "sfo": "san francisco",
    "bay area": "san francisco bay area",
    "usa": "united states",
    "us": "united states",
    "u s": "united states",
    "u s a": "united states",
    "united states of america": "united states",
    "uk": "united kingdom",
    "u k": "united kingdom",
    "great britain": "united kingdom",
    "uae": "united arab emirates",
    "u a e": "united arab emirates",
}


class NameCompatibility(str, Enum):
    EXACT = "exact"
    COMPATIBLE = "compatible"
    WEAKLY_COMPATIBLE = "weakly_compatible"
    INCONCLUSIVE = "inconclusive"
    CONFLICTING = "conflicting"
    NOT_COMPARABLE = "not_comparable"


@dataclass(frozen=True)
class NameComparison:
    exact: bool
    partial: bool
    left_normalized: str | None
    right_normalized: str | None
    overlap_tokens: list[str]


@dataclass(frozen=True)
class NameCompatibilityResult:
    compatibility: NameCompatibility
    reason: str
    left_normalized: str | None
    right_normalized: str | None
    left_tokens: list[str]
    right_tokens: list[str]
    overlap_tokens: list[str]
    similarity: float


@dataclass(frozen=True)
class LocationComparison:
    same: bool
    overlap: bool
    left_normalized: str | None
    right_normalized: str | None
    overlap_tokens: list[str]


@dataclass(frozen=True)
class HandleComparison:
    similar: bool
    exact: bool
    similarity: float
    left_normalized: str | None
    right_normalized: str | None


@dataclass(frozen=True)
class LinkMatch:
    matched: bool
    source_link: str | None = None
    target_profile_url: str | None = None
    normalized_value: str | None = None


def normalize_name(value: Any) -> str | None:
    cleaned = clean_optional_str(value)
    if cleaned is None:
        return None

    ascii_value = (
        unicodedata.normalize("NFKD", cleaned)
        .encode("ascii", "ignore")
        .decode("ascii")
    )

    lowered = ascii_value.lower()
    tokens = [
        token
        for token in _NAME_TOKEN_RE.findall(lowered)
        if token and token not in _NAME_STOPWORDS
    ]

    if not tokens:
        return None

    return " ".join(tokens)


def name_tokens(value: Any) -> list[str]:
    normalized = normalize_name(value)
    if normalized is None:
        return []

    return normalized.split()


def compare_names(left: Any, right: Any) -> NameComparison:
    left_normalized = normalize_name(left)
    right_normalized = normalize_name(right)

    if left_normalized is None or right_normalized is None:
        return NameComparison(
            exact=False,
            partial=False,
            left_normalized=left_normalized,
            right_normalized=right_normalized,
            overlap_tokens=[],
        )

    left_tokens = set(left_normalized.split())
    right_tokens = set(right_normalized.split())
    overlap = sorted(left_tokens & right_tokens)

    exact = left_normalized == right_normalized

    meaningful_overlap = [
        token
        for token in overlap
        if len(token) > 1 and token not in _WEAK_NAME_TOKENS
    ]

    partial = False

    if not exact:
        if len(meaningful_overlap) >= 2:
            partial = True
        elif len(meaningful_overlap) == 1:
            token = meaningful_overlap[0]
            partial = len(token) >= 8 and (
                len(left_tokens) == 1 or len(right_tokens) == 1
            )

    return NameComparison(
        exact=exact,
        partial=partial,
        left_normalized=left_normalized,
        right_normalized=right_normalized,
        overlap_tokens=overlap,
    )



def classify_name_compatibility(left: Any, right: Any) -> NameCompatibilityResult:
    left_normalized = normalize_name(left)
    right_normalized = normalize_name(right)

    if left_normalized is None or right_normalized is None:
        return NameCompatibilityResult(
            compatibility=NameCompatibility.NOT_COMPARABLE,
            reason="missing_or_unparseable_name",
            left_normalized=left_normalized,
            right_normalized=right_normalized,
            left_tokens=[] if left_normalized is None else left_normalized.split(),
            right_tokens=[] if right_normalized is None else right_normalized.split(),
            overlap_tokens=[],
            similarity=0.0,
        )

    left_tokens = left_normalized.split()
    right_tokens = right_normalized.split()
    left_set = set(left_tokens)
    right_set = set(right_tokens)
    overlap = sorted(left_set & right_set)
    similarity = float(fuzz.token_set_ratio(left_normalized, right_normalized))

    if _name_tokens_too_weak(left_tokens) or _name_tokens_too_weak(right_tokens):
        return NameCompatibilityResult(
            compatibility=NameCompatibility.NOT_COMPARABLE,
            reason="name_too_weak",
            left_normalized=left_normalized,
            right_normalized=right_normalized,
            left_tokens=left_tokens,
            right_tokens=right_tokens,
            overlap_tokens=overlap,
            similarity=similarity,
        )

    if left_normalized == right_normalized:
        return NameCompatibilityResult(
            compatibility=NameCompatibility.EXACT,
            reason="normalized_names_identical",
            left_normalized=left_normalized,
            right_normalized=right_normalized,
            left_tokens=left_tokens,
            right_tokens=right_tokens,
            overlap_tokens=overlap,
            similarity=similarity,
        )

    if len(left_tokens) == 1 or len(right_tokens) == 1:
        return NameCompatibilityResult(
            compatibility=NameCompatibility.INCONCLUSIVE,
            reason="single_token_name_not_safe_for_conflict",
            left_normalized=left_normalized,
            right_normalized=right_normalized,
            left_tokens=left_tokens,
            right_tokens=right_tokens,
            overlap_tokens=overlap,
            similarity=similarity,
        )

    if left_set == right_set:
        return NameCompatibilityResult(
            compatibility=NameCompatibility.COMPATIBLE,
            reason="same_tokens_different_order",
            left_normalized=left_normalized,
            right_normalized=right_normalized,
            left_tokens=left_tokens,
            right_tokens=right_tokens,
            overlap_tokens=overlap,
            similarity=similarity,
        )

    if _initial_compatible(left_tokens, right_tokens):
        return NameCompatibilityResult(
            compatibility=NameCompatibility.COMPATIBLE,
            reason="initials_align_with_full_name",
            left_normalized=left_normalized,
            right_normalized=right_normalized,
            left_tokens=left_tokens,
            right_tokens=right_tokens,
            overlap_tokens=overlap,
            similarity=similarity,
        )

    if overlap:
        return NameCompatibilityResult(
            compatibility=NameCompatibility.INCONCLUSIVE,
            reason="shared_name_token_without_safe_full_compatibility",
            left_normalized=left_normalized,
            right_normalized=right_normalized,
            left_tokens=left_tokens,
            right_tokens=right_tokens,
            overlap_tokens=overlap,
            similarity=similarity,
        )

    if similarity >= 82.0:
        return NameCompatibilityResult(
            compatibility=NameCompatibility.WEAKLY_COMPATIBLE,
            reason="high_fuzzy_name_similarity",
            left_normalized=left_normalized,
            right_normalized=right_normalized,
            left_tokens=left_tokens,
            right_tokens=right_tokens,
            overlap_tokens=overlap,
            similarity=similarity,
        )

    if _strong_full_name(left_tokens) and _strong_full_name(right_tokens) and similarity < 72.0:
        return NameCompatibilityResult(
            compatibility=NameCompatibility.CONFLICTING,
            reason="strong_full_names_with_no_overlap_or_compatibility",
            left_normalized=left_normalized,
            right_normalized=right_normalized,
            left_tokens=left_tokens,
            right_tokens=right_tokens,
            overlap_tokens=overlap,
            similarity=similarity,
        )

    return NameCompatibilityResult(
        compatibility=NameCompatibility.INCONCLUSIVE,
        reason="insufficient_signal_for_name_conflict",
        left_normalized=left_normalized,
        right_normalized=right_normalized,
        left_tokens=left_tokens,
        right_tokens=right_tokens,
        overlap_tokens=overlap,
        similarity=similarity,
    )


def _initial_compatible(left_tokens: list[str], right_tokens: list[str]) -> bool:
    if len(left_tokens) != len(right_tokens):
        return False

    unmatched_right = list(right_tokens)
    matched_full_tokens = 0

    for left_token in left_tokens:
        match_index = next(
            (
                index
                for index, right_token in enumerate(unmatched_right)
                if _token_or_initial_match(left_token, right_token)
            ),
            None,
        )

        if match_index is None:
            return False

        right_token = unmatched_right.pop(match_index)
        if len(left_token) > 1 and len(right_token) > 1:
            matched_full_tokens += 1

    return matched_full_tokens >= 1


def _token_or_initial_match(left: str, right: str) -> bool:
    if left == right:
        return True

    if len(left) == 1 and len(right) > 1:
        return left == right[0]

    if len(right) == 1 and len(left) > 1:
        return right == left[0]

    return False


def _name_tokens_too_weak(tokens: list[str]) -> bool:
    if not tokens:
        return True

    return all(len(token) == 1 for token in tokens)


def _strong_full_name(tokens: list[str]) -> bool:
    return len([token for token in tokens if len(token) > 1]) >= 2
def normalize_handle(value: Any) -> str | None:
    cleaned = clean_optional_str(value)
    if cleaned is None:
        return None

    cleaned = cleaned.lstrip("@").lower()
    cleaned = re.sub(r"[^a-z0-9_-]+", "", cleaned)

    return cleaned or None


def compare_handles(left: Any, right: Any) -> HandleComparison:
    left_normalized = normalize_handle(left)
    right_normalized = normalize_handle(right)

    if left_normalized is None or right_normalized is None:
        return HandleComparison(
            similar=False,
            exact=False,
            similarity=0.0,
            left_normalized=left_normalized,
            right_normalized=right_normalized,
        )

    if len(left_normalized) < 3 or len(right_normalized) < 3:
        return HandleComparison(
            similar=False,
            exact=False,
            similarity=0.0,
            left_normalized=left_normalized,
            right_normalized=right_normalized,
        )

    exact = left_normalized == right_normalized
    similarity = float(fuzz.ratio(left_normalized, right_normalized))

    return HandleComparison(
        similar=exact or similarity >= 90.0,
        exact=exact,
        similarity=similarity,
        left_normalized=left_normalized,
        right_normalized=right_normalized,
    )


def normalized_domain(value: Any) -> str | None:
    normalized = normalize_url(value)
    if normalized is None:
        return None

    parsed = urlparse(normalized)
    host = parsed.netloc.lower()

    if host.startswith("www."):
        host = host[4:]

    if host in _GENERIC_WEBSITE_DOMAINS or _is_weak_shared_website_domain(host):
        return None

    return host or None


def _is_weak_shared_website_domain(host: str) -> bool:
    return any(
        host == domain or host.endswith(f".{domain}")
        for domain in _WEAK_SHARED_WEBSITE_DOMAINS
    )


def same_website_domain(left_url: Any, right_url: Any) -> tuple[bool, str | None, str | None, str | None]:
    left_domain = normalized_domain(left_url)
    right_domain = normalized_domain(right_url)

    if left_domain is None or right_domain is None:
        return False, left_domain, right_domain, None

    if left_domain == right_domain:
        return True, left_domain, right_domain, left_domain

    return False, left_domain, right_domain, None


def normalize_location(value: Any) -> str | None:
    cleaned = clean_optional_str(value)
    if cleaned is None:
        return None

    lowered = cleaned.lower()
    lowered = lowered.replace("&", " and ")
    lowered = re.sub(r"[^a-z0-9,\s-]+", " ", lowered)
    lowered = re.sub(r"\s+", " ", lowered).strip(" ,")

    if not lowered:
        return None

    lowered = _LOCATION_ALIASES.get(lowered, lowered)

    if lowered in _VAGUE_LOCATIONS:
        return None

    return lowered


def location_tokens(value: Any) -> set[str]:
    normalized = normalize_location(value)
    if normalized is None:
        return set()

    tokens = {
        _LOCATION_ALIASES.get(token, token)
        for token in re.split(r"[\s,/-]+", normalized)
        if token and token not in _VAGUE_LOCATIONS
    }

    return tokens


def compare_locations(left: Any, right: Any) -> LocationComparison:
    left_normalized = normalize_location(left)
    right_normalized = normalize_location(right)

    if left_normalized is None or right_normalized is None:
        return LocationComparison(
            same=False,
            overlap=False,
            left_normalized=left_normalized,
            right_normalized=right_normalized,
            overlap_tokens=[],
        )

    same = left_normalized == right_normalized

    left_tokens = location_tokens(left)
    right_tokens = location_tokens(right)
    overlap_tokens = sorted(left_tokens & right_tokens)

    overlap = not same and bool(overlap_tokens)

    return LocationComparison(
        same=same,
        overlap=overlap,
        left_normalized=left_normalized,
        right_normalized=right_normalized,
        overlap_tokens=overlap_tokens,
    )


def bio_keywords(value: Any) -> set[str]:
    cleaned = html_to_text(value)
    if cleaned is None:
        return set()

    ascii_value = (
        unicodedata.normalize("NFKD", cleaned)
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    lowered = ascii_value.lower()
    keywords: set[str] = set()

    for pattern, canonical in _BIO_PHRASE_ALIASES:
        if pattern.search(lowered):
            keywords.add(canonical)

    for raw_word in _BIO_KEYWORD_RE.findall(lowered):
        keyword = normalize_bio_keyword(raw_word)
        if keyword is not None:
            keywords.add(keyword)

    return keywords


def normalize_bio_keyword(value: Any) -> str | None:
    cleaned = clean_optional_str(value)
    if cleaned is None:
        return None

    token = cleaned.strip().lower().strip("._-")
    if not token:
        return None

    token = token.replace("_", "-")
    token = re.sub(r"-{2,}", "-", token)
    alias = _BIO_KEYWORD_ALIASES.get(token)
    if alias is not None:
        token = alias
    elif "." in token:
        compact = token.replace(".", "")
        token = _BIO_KEYWORD_ALIASES.get(compact, compact)
    elif "-" in token:
        compact = token.replace("-", "")
        token = _BIO_KEYWORD_ALIASES.get(compact, token)

    if token in _BIO_STOPWORDS:
        return None

    if len(token) < 3 and token not in _BIO_ALLOWED_SHORT_KEYWORDS:
        return None

    if not re.search(r"[a-z0-9]", token):
        return None

    return token


def keyword_overlap(left: Any, right: Any, *, minimum: int = 2) -> list[str]:
    left_keywords = bio_keywords(left)
    right_keywords = bio_keywords(right)

    overlap = sorted(left_keywords & right_keywords)

    if len(overlap) < minimum:
        return []

    return overlap


def topic_overlap(left_topics: list[str], right_topics: list[str], *, minimum: int = 2) -> list[str]:
    left = {
        str(topic).strip().lower()
        for topic in left_topics
        if str(topic).strip()
    }

    right = {
        str(topic).strip().lower()
        for topic in right_topics
        if str(topic).strip()
    }

    overlap = sorted(left & right)

    if len(overlap) < minimum:
        return []

    return overlap


def profile_fingerprint(url: Any) -> str | None:
    normalized = normalize_url(url)
    if normalized is None:
        return None

    parsed = urlparse(normalized)
    host = parsed.netloc.lower()

    if host.startswith("www."):
        host = host[4:]

    path_parts = [
        part
        for part in parsed.path.strip("/").split("/")
        if part
    ]

    if host == "github.com" and len(path_parts) == 1:
        return f"github:{path_parts[0].lower()}"

    if host == "dev.to" and len(path_parts) == 1:
        return f"devto:{path_parts[0].lower()}"

    if host == "stackoverflow.com" and len(path_parts) >= 2 and path_parts[0].lower() == "users":
        user_id = path_parts[1]
        if user_id.isdigit():
            return f"stackoverflow:{int(user_id)}"

    if host == "news.ycombinator.com":
        query = parse_qs(parsed.query)
        user_ids = query.get("id")
        if user_ids and user_ids[0].strip():
            return f"hackernews:{user_ids[0].strip().lower()}"

    if host == "hn.algolia.com" and len(path_parts) >= 2 and path_parts[0].lower() == "user":
        return f"hackernews:{path_parts[1].lower()}"

    return normalized


def profile_link_match(outbound_links: list[str], target_profile_url: Any) -> LinkMatch:
    target_fingerprint = profile_fingerprint(target_profile_url)

    if target_fingerprint is None:
        return LinkMatch(matched=False)

    for link in outbound_links:
        link_fingerprint = profile_fingerprint(link)

        if link_fingerprint is None:
            continue

        if link_fingerprint == target_fingerprint:
            return LinkMatch(
                matched=True,
                source_link=normalize_url(link),
                target_profile_url=normalize_url(target_profile_url),
                normalized_value=target_fingerprint,
            )

    return LinkMatch(matched=False)


def is_hackernews_account(source: PlatformSource) -> bool:
    return source == PlatformSource.HACKERNEWS