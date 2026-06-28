from __future__ import annotations

from _bootstrap import bootstrap_project_root

bootstrap_project_root()

from app.schemas.enums import PlatformSource
from app.schemas.ingestion import CandidateType
from app.schemas.requests import ProfileResolveRequest
from app.services.candidate_discovery import CandidateDiscoveryService


def _candidate_tuples(result):
    return [
        (candidate.source, candidate.identifier, candidate.candidate_type)
        for candidate in result.candidates
    ]


def _has_candidate(
    result,
    *,
    source: PlatformSource,
    identifier: str,
    candidate_type: CandidateType | None = None,
) -> bool:
    for candidate in result.candidates:
        if candidate.source != source:
            continue

        if candidate.identifier.lower() != identifier.lower():
            continue

        if candidate_type is not None and candidate.candidate_type != candidate_type:
            continue

        return True

    return False


def _has_source(result, source: PlatformSource) -> bool:
    return any(candidate.source == source for candidate in result.candidates)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def verify_github_profile_url() -> None:
    service = CandidateDiscoveryService()

    request = ProfileResolveRequest(
        name="Test User",
        github="https://github.com/octocat",
    )

    result = service.discover(request)

    _assert(
        _has_candidate(
            result,
            source=PlatformSource.GITHUB,
            identifier="octocat",
            candidate_type=CandidateType.PROVIDED_URL,
        ),
        "GitHub profile URL should produce github:octocat provided_url candidate.",
    )

    _assert(
        len(result.candidates) == 1,
        "Valid direct GitHub input should not expand into other platforms by default.",
    )


def verify_github_repo_url_rejected() -> None:
    service = CandidateDiscoveryService()

    request = ProfileResolveRequest(
        name="Test User",
        github="https://github.com/octocat/Hello-World",
    )

    result = service.discover(request)

    _assert(
        not _has_candidate(
            result,
            source=PlatformSource.GITHUB,
            identifier="octocat",
            candidate_type=CandidateType.PROVIDED_URL,
        ),
        "GitHub repo URL must not be accepted as a profile URL.",
    )

    _assert(
        result.warnings,
        "Rejected GitHub repo URL should produce a warning.",
    )


def verify_devto_profile_url() -> None:
    service = CandidateDiscoveryService()

    request = ProfileResolveRequest(
        name="Ben",
        devto="https://dev.to/ben",
    )

    result = service.discover(request)

    _assert(
        _has_candidate(
            result,
            source=PlatformSource.DEVTO,
            identifier="ben",
            candidate_type=CandidateType.PROVIDED_URL,
        ),
        "dev.to profile URL should produce devto:ben provided_url candidate.",
    )

    _assert(
        len(result.candidates) == 1,
        "Valid direct dev.to input should not expand into other platforms by default.",
    )


def verify_devto_article_url_rejected() -> None:
    service = CandidateDiscoveryService()

    request = ProfileResolveRequest(
        name="Ben",
        devto="https://dev.to/ben/some-article-title",
    )

    result = service.discover(request)

    _assert(
        not _has_candidate(
            result,
            source=PlatformSource.DEVTO,
            identifier="ben",
            candidate_type=CandidateType.PROVIDED_URL,
        ),
        "dev.to article URL must not be accepted as a profile URL.",
    )

    _assert(
        result.warnings,
        "Rejected dev.to article URL should produce a warning.",
    )


def verify_hackernews_profile_urls() -> None:
    service = CandidateDiscoveryService()

    ycombinator_request = ProfileResolveRequest(
        name="Paul Graham",
        hackernews="https://news.ycombinator.com/user?id=pg",
    )

    ycombinator_result = service.discover(ycombinator_request)

    _assert(
        _has_candidate(
            ycombinator_result,
            source=PlatformSource.HACKERNEWS,
            identifier="pg",
            candidate_type=CandidateType.PROVIDED_URL,
        ),
        "news.ycombinator.com user URL should produce hackernews:pg candidate.",
    )

    algolia_request = ProfileResolveRequest(
        name="Paul Graham",
        hackernews="https://hn.algolia.com/user/pg",
    )

    algolia_result = service.discover(algolia_request)

    _assert(
        _has_candidate(
            algolia_result,
            source=PlatformSource.HACKERNEWS,
            identifier="pg",
            candidate_type=CandidateType.PROVIDED_URL,
        ),
        "hn.algolia.com user URL should produce hackernews:pg candidate.",
    )


def verify_stackoverflow_profile_url() -> None:
    service = CandidateDiscoveryService()

    request = ProfileResolveRequest(
        name="Jon Skeet",
        stackoverflow_user_id="https://stackoverflow.com/users/22656/jon-skeet",
    )

    result = service.discover(request)

    _assert(
        _has_candidate(
            result,
            source=PlatformSource.STACKOVERFLOW,
            identifier="22656",
            candidate_type=CandidateType.PROVIDED_ID,
        ),
        "Stack Overflow profile URL should produce stackoverflow:22656 candidate.",
    )


def verify_stackoverflow_question_url_rejected() -> None:
    service = CandidateDiscoveryService()

    request = ProfileResolveRequest(
        name="Jon Skeet",
        stackoverflow_user_id="https://stackoverflow.com/questions/12345/example",
    )

    result = service.discover(request)

    _assert(
        not _has_source(result, PlatformSource.STACKOVERFLOW),
        "Stack Overflow question URL must not produce a Stack Overflow user candidate.",
    )

    _assert(
        result.warnings,
        "Rejected Stack Overflow question URL should produce a warning.",
    )


def verify_stackoverflow_leading_zero_normalization() -> None:
    service = CandidateDiscoveryService()

    request = ProfileResolveRequest(
        name="Jon Skeet",
        stackoverflow_user_id="00022656",
    )

    result = service.discover(request)

    _assert(
        _has_candidate(
            result,
            source=PlatformSource.STACKOVERFLOW,
            identifier="22656",
            candidate_type=CandidateType.PROVIDED_ID,
        ),
        "Stack Overflow numeric IDs should normalize leading zeros.",
    )


def verify_name_only_candidates() -> None:
    service = CandidateDiscoveryService()

    request = ProfileResolveRequest(
        name="Muhammed Amil",
    )

    result = service.discover(request)

    _assert(
        _has_source(result, PlatformSource.GITHUB),
        "Name-only request should generate GitHub candidates.",
    )

    _assert(
        _has_source(result, PlatformSource.DEVTO),
        "Name-only request should generate dev.to candidates.",
    )

    _assert(
        _has_source(result, PlatformSource.HACKERNEWS),
        "Name-only request should generate Hacker News candidates.",
    )

    _assert(
        not _has_source(result, PlatformSource.STACKOVERFLOW),
        "Name-only request must not generate Stack Overflow numeric-ID candidates.",
    )


def verify_direct_input_does_not_expand_by_default() -> None:
    service = CandidateDiscoveryService()

    request = ProfileResolveRequest(
        name="Octocat",
        github="octocat",
    )

    result = service.discover(request)

    _assert(
        len(result.candidates) == 1,
        "Direct valid input should not expand to other platform name variants by default.",
    )

    _assert(
        _has_candidate(
            result,
            source=PlatformSource.GITHUB,
            identifier="octocat",
            candidate_type=CandidateType.PROVIDED_HANDLE,
        ),
        "Direct GitHub handle should produce github:octocat provided_handle candidate.",
    )


def main() -> None:
    checks = [
        verify_github_profile_url,
        verify_github_repo_url_rejected,
        verify_devto_profile_url,
        verify_devto_article_url_rejected,
        verify_hackernews_profile_urls,
        verify_stackoverflow_profile_url,
        verify_stackoverflow_question_url_rejected,
        verify_stackoverflow_leading_zero_normalization,
        verify_name_only_candidates,
        verify_direct_input_does_not_expand_by_default,
    ]

    for check in checks:
        check()
        print(f"PASS: {check.__name__}")

    print("\nCandidate discovery verification passed.")


if __name__ == "__main__":
    main()
