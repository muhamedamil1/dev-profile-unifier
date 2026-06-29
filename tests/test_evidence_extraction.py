from __future__ import annotations

import hashlib

import pytest

from app.resolution.evidence import EvidenceExtractor
from app.schemas.enums import PlatformSource
from app.schemas.evidence import EvidenceType
from app.schemas.requests import ProfileResolveRequest
from app.schemas.source_account import SourceAccount


@pytest.fixture
def extractor() -> EvidenceExtractor:
    return EvidenceExtractor()


def test_phase_7a_extracts_request_and_account_pair_evidence(
    extractor: EvidenceExtractor,
) -> None:
    request = ProfileResolveRequest(
        name="Muhammed Amil",
        github="amil122",
    )

    github = SourceAccount(
        source=PlatformSource.GITHUB,
        source_user_id="101",
        handle="amil122",
        display_name="Muhammed Amil",
        bio="AI engineer building FastAPI tools",
        website_url="https://amil.dev",
        profile_url="https://github.com/amil122",
        outbound_links=["https://dev.to/muhammedamil"],
        topics=["python", "fastapi", "supabase"],
    )

    devto = SourceAccount(
        source=PlatformSource.DEVTO,
        source_user_id="202",
        handle="muhammedamil",
        display_name="Muhammed Amil",
        bio="FastAPI developer writing about AI automation",
        website_url="https://www.amil.dev/",
        profile_url="https://dev.to/muhammedamil",
        outbound_links=["https://github.com/amil122"],
        topics=["python", "fastapi", "backend"],
    )

    hn = SourceAccount(
        source=PlatformSource.HACKERNEWS,
        source_user_id="amil122",
        handle="amil122",
        display_name="amil122",
        bio="",
        profile_url="https://news.ycombinator.com/user?id=amil122",
        topics=["python"],
    )

    result = extractor.extract(
        request=request,
        accounts=[github, devto, hn],
    )

    assert result.count > 0

    assert result.by_type.get(EvidenceType.INPUT_HANDLE_MATCH.value, 0) == 1
    assert result.by_type.get(EvidenceType.EXACT_NAME_MATCH.value, 0) >= 2
    assert result.by_type.get(EvidenceType.SAME_WEBSITE.value, 0) == 1
    assert result.by_type.get(EvidenceType.DIRECT_PROFILE_LINK.value, 0) == 2
    assert result.by_type.get(EvidenceType.RECIPROCAL_PROFILE_LINK.value, 0) == 1
    assert result.by_type.get(EvidenceType.SIMILAR_HANDLE.value, 0) >= 1
    assert result.by_type.get(EvidenceType.BIO_KEYWORD_OVERLAP.value, 0) == 1
    assert result.by_type.get(EvidenceType.TOPIC_OVERLAP.value, 0) == 1

    assert not any(
        item.evidence_type == EvidenceType.EXACT_NAME_MATCH
        and item.source == PlatformSource.HACKERNEWS
        for item in result.evidence
    )

    hn_similar_handle_items = [
        item
        for item in result.evidence
        if item.evidence_type == EvidenceType.SIMILAR_HANDLE
        and (
            item.source == PlatformSource.HACKERNEWS
            or item.target_source == PlatformSource.HACKERNEWS
        )
    ]

    assert hn_similar_handle_items

    for item in hn_similar_handle_items:
        assert item.metadata.get("hn_conservative") is True
        assert item.metadata.get("weak_identity_signal") is True


def test_phase_7a_marks_weak_identity_signals(
    extractor: EvidenceExtractor,
) -> None:
    request = ProfileResolveRequest(name="Muhammed Amil")

    github = SourceAccount(
        source=PlatformSource.GITHUB,
        source_user_id="101",
        handle="amil122",
        display_name="Muhammed Amil",
        bio="AI engineer building FastAPI tools",
        location="Bangalore, India",
        profile_url="https://github.com/amil122",
        topics=["python", "fastapi"],
    )

    devto = SourceAccount(
        source=PlatformSource.DEVTO,
        source_user_id="202",
        handle="amil-dev",
        display_name="Muhammed A",
        bio="FastAPI developer writing about AI tools",
        location="Bangalore",
        profile_url="https://dev.to/amil-dev",
        topics=["python", "fastapi"],
    )

    result = extractor.extract(request=request, accounts=[github, devto])

    weak_types = {
        EvidenceType.SIMILAR_HANDLE,
        EvidenceType.PARTIAL_NAME_MATCH,
        EvidenceType.SAME_LOCATION,
        EvidenceType.LOCATION_OVERLAP,
        EvidenceType.BIO_KEYWORD_OVERLAP,
        EvidenceType.TOPIC_OVERLAP,
    }

    weak_items = [
        item
        for item in result.evidence
        if item.evidence_type in weak_types
    ]

    assert weak_items

    for item in weak_items:
        assert item.metadata.get("weak_identity_signal") is True


def test_phase_7a_rejects_reserved_github_path_for_input_handle_match(
    extractor: EvidenceExtractor,
) -> None:
    request = ProfileResolveRequest(
        name="John Smith",
        github="https://github.com/topics",
    )

    github = SourceAccount(
        source=PlatformSource.GITHUB,
        source_user_id="1",
        handle="topics",
        display_name="John",
        profile_url="https://github.com/topics",
    )

    result = extractor.extract(request=request, accounts=[github])

    assert result.by_type.get(EvidenceType.INPUT_HANDLE_MATCH.value, 0) == 0


def test_phase_7a_rejects_reserved_devto_path_for_input_handle_match(
    extractor: EvidenceExtractor,
) -> None:
    request = ProfileResolveRequest(
        name="Jane Developer",
        devto="https://dev.to/tags",
    )

    devto = SourceAccount(
        source=PlatformSource.DEVTO,
        source_user_id="2",
        handle="tags",
        display_name="Jane",
        profile_url="https://dev.to/tags",
    )

    result = extractor.extract(request=request, accounts=[devto])

    assert result.by_type.get(EvidenceType.INPUT_HANDLE_MATCH.value, 0) == 0


def test_phase_7a_rejects_common_one_token_partial_name_match(
    extractor: EvidenceExtractor,
) -> None:
    request = ProfileResolveRequest(name="John Smith")

    github = SourceAccount(
        source=PlatformSource.GITHUB,
        source_user_id="1",
        handle="john-dev",
        display_name="John",
        profile_url="https://github.com/john-dev",
    )

    result = extractor.extract(request=request, accounts=[github])

    assert result.by_type.get(EvidenceType.PARTIAL_NAME_MATCH.value, 0) == 0


def test_phase_7a_skips_stackoverflow_display_name_like_handle_similarity(
    extractor: EvidenceExtractor,
) -> None:
    request = ProfileResolveRequest(name="Muhammed Amil")

    github = SourceAccount(
        source=PlatformSource.GITHUB,
        source_user_id="101",
        handle="muhammedamil",
        display_name="Muhammed Amil",
        profile_url="https://github.com/muhammedamil",
    )

    stackoverflow = SourceAccount(
        source=PlatformSource.STACKOVERFLOW,
        source_user_id="22656",
        handle="Muhammed Amil",
        display_name="Muhammed Amil",
        profile_url="https://stackoverflow.com/users/22656/muhammed-amil",
    )

    result = extractor.extract(
        request=request,
        accounts=[github, stackoverflow],
    )

    assert result.by_type.get(EvidenceType.SIMILAR_HANDLE.value, 0) == 0


def test_phase_7a_output_is_deterministic_regardless_of_account_order(
    extractor: EvidenceExtractor,
) -> None:
    request = ProfileResolveRequest(name="Muhammed Amil")

    github = SourceAccount(
        source=PlatformSource.GITHUB,
        source_user_id="101",
        handle="amil122",
        display_name="Muhammed Amil",
        bio="AI engineer building FastAPI tools",
        website_url="https://amil.dev",
        profile_url="https://github.com/amil122",
        outbound_links=["https://dev.to/muhammedamil"],
        topics=["python", "fastapi"],
    )

    devto = SourceAccount(
        source=PlatformSource.DEVTO,
        source_user_id="202",
        handle="muhammedamil",
        display_name="Muhammed Amil",
        bio="FastAPI developer writing about AI automation",
        website_url="https://www.amil.dev",
        profile_url="https://dev.to/muhammedamil",
        outbound_links=["https://github.com/amil122"],
        topics=["python", "fastapi"],
    )

    hn = SourceAccount(
        source=PlatformSource.HACKERNEWS,
        source_user_id="amil122",
        handle="amil122",
        display_name="amil122",
        profile_url="https://news.ycombinator.com/user?id=amil122",
    )

    first = extractor.extract(request=request, accounts=[github, devto, hn])
    second = extractor.extract(request=request, accounts=[hn, github, devto])
    third = extractor.extract(request=request, accounts=[devto, hn, github])

    def signature(result) -> list[tuple[str, str, str | None, str, float]]:
        return [
            (
                item.evidence_type.value,
                item.source_account_key,
                item.target_account_key,
                item.independence_group.value,
                item.weight,
            )
            for item in result.evidence
        ]

    assert signature(first) == signature(second) == signature(third)


def test_phase_7a_email_hint_and_email_domain_match_without_raw_email_leak(
    extractor: EvidenceExtractor,
) -> None:
    email = "amil@example.com"
    email_hash = hashlib.sha256(email.lower().encode("utf-8")).hexdigest()

    request = ProfileResolveRequest(
        name="Muhammed Amil",
        github="amil122",
        email_hint=email,
    )

    github = SourceAccount(
        source=PlatformSource.GITHUB,
        source_user_id="101",
        handle="amil122",
        display_name="Muhammed Amil",
        profile_url="https://github.com/amil122",
        email_hash=email_hash,
        activity_payload={
            "email_domain": "example.com",
        },
    )

    result = extractor.extract(request=request, accounts=[github])

    assert result.by_type.get(EvidenceType.EMAIL_HINT_MATCH.value, 0) == 1
    assert result.by_type.get(EvidenceType.EMAIL_DOMAIN_MATCH.value, 0) == 1

    for item in result.evidence:
        metadata_text = str(item.metadata).lower()
        assert email not in metadata_text
