from __future__ import annotations

from app.resolution.comparators import (
    compare_handles,
    compare_locations,
    compare_names,
    keyword_overlap,
    normalize_bio_keyword,
    normalized_domain,
    profile_fingerprint,
    profile_link_match,
    same_website_domain,
)


def test_compare_names_exact_match_normalizes_case_and_spacing() -> None:
    comparison = compare_names("  Muhammed   Amil ", "muhammed amil")

    assert comparison.exact is True
    assert comparison.partial is False
    assert comparison.left_normalized == "muhammed amil"
    assert comparison.right_normalized == "muhammed amil"


def test_compare_names_rejects_common_single_token_partial_match() -> None:
    comparison = compare_names("John Smith", "John")

    assert comparison.exact is False
    assert comparison.partial is False


def test_compare_names_allows_stronger_non_common_single_token_partial_match() -> None:
    comparison = compare_names("Christopher Nolan", "Christopher")

    assert comparison.exact is False
    assert comparison.partial is True


def test_compare_handles_detects_exact_and_near_match() -> None:
    exact = compare_handles("amil122", "@amil122")
    near = compare_handles("muhammedamil", "muhammed-amil")

    assert exact.exact is True
    assert exact.similar is True

    assert near.exact is False
    assert near.similar is True


def test_normalized_domain_ignores_platform_domains() -> None:
    assert normalized_domain("https://github.com/amil122") is None
    assert normalized_domain("https://dev.to/muhammedamil") is None
    assert normalized_domain("https://stackoverflow.com/users/22656/name") is None
    assert normalized_domain("https://news.ycombinator.com/user?id=pg") is None


def test_same_website_domain_normalizes_www_and_scheme() -> None:
    matched, left_domain, right_domain, normalized = same_website_domain(
        "https://www.amil.dev/",
        "http://amil.dev",
    )

    assert matched is True
    assert left_domain == "amil.dev"
    assert right_domain == "amil.dev"
    assert normalized == "amil.dev"


def test_profile_fingerprint_supports_platform_profile_urls() -> None:
    assert profile_fingerprint("https://github.com/amil122") == "github:amil122"
    assert profile_fingerprint("https://dev.to/muhammedamil") == "devto:muhammedamil"
    assert (
        profile_fingerprint("https://stackoverflow.com/users/00022656/muhammed-amil")
        == "stackoverflow:22656"
    )
    assert (
        profile_fingerprint("https://news.ycombinator.com/user?id=amil122")
        == "hackernews:amil122"
    )


def test_profile_link_match_detects_direct_profile_links() -> None:
    match = profile_link_match(
        outbound_links=[
            "https://example.com",
            "https://github.com/amil122",
        ],
        target_profile_url="https://github.com/amil122",
    )

    assert match.matched is True
    assert match.normalized_value == "github:amil122"


def test_compare_locations_detects_alias_overlap() -> None:
    comparison = compare_locations("Bengaluru, India", "Bangalore")

    assert comparison.same is False
    assert comparison.overlap is True
    assert "bangalore" in comparison.overlap_tokens


def test_keyword_overlap_keeps_meaningful_short_technical_terms() -> None:
    overlap = keyword_overlap(
        "AI engineer building FastAPI tools",
        "FastAPI developer writing about AI automation",
        minimum=2,
    )

    assert overlap == ["ai", "fastapi"]

def test_bio_keywords_normalize_real_world_technical_aliases() -> None:
    overlap = keyword_overlap(
        "Building artificial intelligence APIs with Node.js, PostgreSQL, and Kubernetes",
        "AI platform engineer using nodejs, Postgres, and k8s in production",
        minimum=4,
    )

    assert overlap == ["ai", "kubernetes", "nodejs", "postgres"]


def test_bio_keyword_overlap_ignores_generic_profile_words() -> None:
    assert keyword_overlap(
        "software engineer building open source tools",
        "developer writing code and building tools",
        minimum=2,
    ) == []


def test_normalize_bio_keyword_canonicalizes_common_spellings() -> None:
    assert normalize_bio_keyword("React.js") == "react"
    assert normalize_bio_keyword("js") == "javascript"
    assert normalize_bio_keyword("TypeScript") == "typescript"
    assert normalize_bio_keyword("C++") == "cpp"
    assert normalize_bio_keyword("go") is None
