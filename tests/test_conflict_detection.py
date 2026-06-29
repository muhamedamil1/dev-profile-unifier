from __future__ import annotations

import hashlib

import pytest

from app.resolution.comparators import NameCompatibility, classify_name_compatibility
from app.resolution.conflict_detector import ConflictDetector
from app.schemas.conflicts import ConflictType
from app.schemas.enums import ConflictSeverity, PlatformSource
from app.schemas.source_account import SourceAccount


@pytest.fixture
def detector() -> ConflictDetector:
    return ConflictDetector()


def account(
    source: PlatformSource,
    identifier: str,
    *,
    handle: str | None = None,
    display_name: str | None = None,
    website_url: str | None = None,
    location: str | None = None,
    email_hash: str | None = None,
    email_domain: str | None = None,
    topics: list[str] | None = None,
) -> SourceAccount:
    payload = {"email_domain": email_domain} if email_domain is not None else {}
    return SourceAccount(
        source=source,
        source_user_id=identifier,
        handle=handle or identifier,
        display_name=display_name,
        website_url=website_url,
        location=location,
        email_hash=email_hash,
        activity_payload=payload,
        topics=topics or [],
    )


def conflict_types(result) -> list[ConflictType]:
    return [item.conflict_type for item in result.conflicts]


def test_name_compatibility_classifies_exact_and_compatible_names() -> None:
    exact = classify_name_compatibility("Muhammed Amil", "muhammed amil")
    reversed_name = classify_name_compatibility("Muhammed Amil", "Amil Muhammed")
    initial = classify_name_compatibility("M Amil", "Muhammed Amil")
    trailing_initial = classify_name_compatibility("Muhammed A", "Muhammed Amil")

    assert exact.compatibility == NameCompatibility.EXACT
    assert reversed_name.compatibility == NameCompatibility.COMPATIBLE
    assert initial.compatibility == NameCompatibility.COMPATIBLE
    assert trailing_initial.compatibility == NameCompatibility.COMPATIBLE


def test_name_compatibility_keeps_shared_token_names_inconclusive() -> None:
    assert (
        classify_name_compatibility("John Smith", "John Carter").compatibility
        == NameCompatibility.INCONCLUSIVE
    )
    assert (
        classify_name_compatibility("Muhammed Amil", "Muhammed Khan").compatibility
        == NameCompatibility.INCONCLUSIVE
    )
    assert (
        classify_name_compatibility("Muhammed Amil", "Amil").compatibility
        == NameCompatibility.INCONCLUSIVE
    )


def test_name_compatibility_flags_only_strong_incompatible_full_names() -> None:
    assert (
        classify_name_compatibility("Muhammed Amil", "David Lee").compatibility
        == NameCompatibility.CONFLICTING
    )
    assert (
        classify_name_compatibility("Sarah Chen", "Robert Martin").compatibility
        == NameCompatibility.CONFLICTING
    )


@pytest.mark.parametrize(
    ("left_name", "right_name"),
    [
        ("Muhammed Amil", "Muhammed Amil"),
        ("Muhammed Amil", "Amil Muhammed"),
        ("M Amil", "Muhammed Amil"),
        ("John Smith", "John Carter"),
        ("Muhammed Amil", "Muhammed Khan"),
        ("Muhammed Amil", "Amil"),
    ],
)
def test_detector_does_not_emit_name_conflict_for_compatible_or_inconclusive_names(
    detector: ConflictDetector,
    left_name: str,
    right_name: str,
) -> None:
    left = account(PlatformSource.GITHUB, "1", display_name=left_name)
    right = account(PlatformSource.DEVTO, "2", display_name=right_name)

    result = detector.detect(accounts=[left, right])

    assert ConflictType.NAME_CONFLICT not in conflict_types(result)


@pytest.mark.parametrize(
    ("left_name", "right_name"),
    [
        ("Muhammed Amil", "David Lee"),
        ("Sarah Chen", "Robert Martin"),
    ],
)
def test_detector_emits_name_conflict_for_strong_incompatible_full_names(
    detector: ConflictDetector,
    left_name: str,
    right_name: str,
) -> None:
    left = account(PlatformSource.GITHUB, "1", display_name=left_name)
    right = account(PlatformSource.DEVTO, "2", display_name=right_name)

    result = detector.detect(accounts=[left, right])

    name_conflicts = [item for item in result.conflicts if item.conflict_type == ConflictType.NAME_CONFLICT]
    assert len(name_conflicts) == 1
    assert name_conflicts[0].metadata["conflict_basis"] == "display_name"
    assert name_conflicts[0].metadata["compatibility"] == NameCompatibility.CONFLICTING.value
    assert name_conflicts[0].metadata["name_similarity"] < 72.0


def test_detector_skips_hn_handle_like_display_name_and_single_token_name_conflicts(
    detector: ConflictDetector,
) -> None:
    github = account(PlatformSource.GITHUB, "1", display_name="David Lee")
    hn = account(
        PlatformSource.HACKERNEWS,
        "amil122",
        handle="amil122",
        display_name="amil122",
    )
    single_token = account(PlatformSource.DEVTO, "2", display_name="Amil")

    assert ConflictType.NAME_CONFLICT not in conflict_types(
        detector.detect(accounts=[github, hn])
    )
    assert ConflictType.NAME_CONFLICT not in conflict_types(
        detector.detect(accounts=[github, single_token])
    )


def test_email_hash_conflict_is_high_with_strong_penalty(detector: ConflictDetector) -> None:
    left = account(PlatformSource.GITHUB, "1", email_hash="a" * 64)
    right = account(PlatformSource.DEVTO, "2", email_hash="b" * 64)

    result = detector.detect(accounts=[left, right])

    conflict = result.conflicts[0]
    assert conflict.conflict_type == ConflictType.EMAIL_CONFLICT
    assert conflict.severity == ConflictSeverity.HIGH
    assert conflict.penalty == -0.35
    assert conflict.metadata["conflict_basis"] == "email_hash"


def test_same_email_hash_does_not_conflict(detector: ConflictDetector) -> None:
    email_hash = hashlib.sha256(b"amil@example.com").hexdigest()
    left = account(PlatformSource.GITHUB, "1", email_hash=email_hash)
    right = account(PlatformSource.DEVTO, "2", email_hash=email_hash)

    assert detector.detect(accounts=[left, right]).conflicts == []


@pytest.mark.parametrize(
    ("left_domain", "right_domain"),
    [
        ("gmail.com", "outlook.com"),
        ("gmail.com", "company.com"),
    ],
)
def test_generic_email_domains_do_not_create_domain_conflict(
    detector: ConflictDetector,
    left_domain: str,
    right_domain: str,
) -> None:
    left = account(PlatformSource.GITHUB, "1", email_domain=left_domain)
    right = account(PlatformSource.DEVTO, "2", email_domain=right_domain)

    assert detector.detect(accounts=[left, right]).conflicts == []


def test_non_generic_email_domain_conflict_is_weaker(detector: ConflictDetector) -> None:
    left = account(PlatformSource.GITHUB, "1", email_domain="company1.com")
    right = account(PlatformSource.DEVTO, "2", email_domain="company2.com")

    result = detector.detect(accounts=[left, right])

    conflict = result.conflicts[0]
    assert conflict.conflict_type == ConflictType.EMAIL_CONFLICT
    assert conflict.severity == ConflictSeverity.MEDIUM
    assert conflict.penalty == -0.12
    assert conflict.metadata["weak_identity_signal"] is True
    assert conflict.metadata["conflict_basis"] == "email_domain"


def test_website_conflict_rules_ignore_same_and_weak_domains(detector: ConflictDetector) -> None:
    same_left = account(PlatformSource.GITHUB, "1", website_url="https://www.amil.dev")
    same_right = account(PlatformSource.DEVTO, "2", website_url="http://amil.dev/about")
    weak_left = account(PlatformSource.GITHUB, "3", website_url="https://linktr.ee/amil")
    personal_right = account(PlatformSource.DEVTO, "4", website_url="https://amil.dev")
    medium_left = account(PlatformSource.GITHUB, "5", website_url="https://medium.com/@amil")

    assert detector.detect(accounts=[same_left, same_right]).conflicts == []
    assert detector.detect(accounts=[weak_left, personal_right]).conflicts == []
    assert detector.detect(accounts=[medium_left, personal_right]).conflicts == []


def test_different_strong_personal_domains_emit_website_conflict(
    detector: ConflictDetector,
) -> None:
    left = account(PlatformSource.GITHUB, "1", website_url="https://amil.dev")
    right = account(PlatformSource.DEVTO, "2", website_url="https://davidlee.dev")

    result = detector.detect(accounts=[left, right])

    conflict = result.conflicts[0]
    assert conflict.conflict_type == ConflictType.WEBSITE_CONFLICT
    assert conflict.metadata["conflict_basis"] == "website_domain"


def test_location_aliases_and_missing_locations_do_not_conflict(
    detector: ConflictDetector,
) -> None:
    assert detector.detect(
        accounts=[
            account(PlatformSource.GITHUB, "1", location="USA"),
            account(PlatformSource.DEVTO, "2", location="United States"),
        ]
    ).conflicts == []
    assert detector.detect(
        accounts=[
            account(PlatformSource.GITHUB, "3", location="UK"),
            account(PlatformSource.DEVTO, "4", location="United Kingdom"),
        ]
    ).conflicts == []
    assert detector.detect(
        accounts=[
            account(PlatformSource.GITHUB, "5", location="Bengaluru"),
            account(PlatformSource.DEVTO, "6", location="Bangalore"),
        ]
    ).conflicts == []
    assert detector.detect(
        accounts=[
            account(PlatformSource.GITHUB, "7", location=None),
            account(PlatformSource.DEVTO, "8", location="Toronto"),
        ]
    ).conflicts == []


def test_different_non_overlapping_locations_emit_low_conflict(
    detector: ConflictDetector,
) -> None:
    left = account(PlatformSource.GITHUB, "1", location="Bangalore, India")
    right = account(PlatformSource.DEVTO, "2", location="Toronto, Canada")

    result = detector.detect(accounts=[left, right])

    conflict = result.conflicts[0]
    assert conflict.conflict_type == ConflictType.LOCATION_CONFLICT
    assert conflict.severity == ConflictSeverity.LOW
    assert conflict.metadata["conflict_basis"] == "location"


def test_topic_mismatch_is_conservative_and_skips_hn(detector: ConflictDetector) -> None:
    five_topics = ["python", "fastapi", "postgres", "docker", "kubernetes"]
    six_left = ["python", "fastapi", "postgres", "docker", "kubernetes", "redis"]
    six_with_overlap = ["python", "react", "nextjs", "tailwind", "typescript", "vite"]
    six_right = ["go", "rust", "elixir", "phoenix", "mysql", "graphql"]

    assert detector.detect(
        accounts=[
            account(PlatformSource.GITHUB, "1", topics=five_topics),
            account(PlatformSource.DEVTO, "2", topics=six_right),
        ]
    ).conflicts == []
    assert detector.detect(
        accounts=[
            account(PlatformSource.GITHUB, "3", topics=six_left),
            account(PlatformSource.DEVTO, "4", topics=six_with_overlap),
        ]
    ).conflicts == []
    assert detector.detect(
        accounts=[
            account(PlatformSource.HACKERNEWS, "5", topics=six_left),
            account(PlatformSource.DEVTO, "6", topics=six_right),
        ]
    ).conflicts == []

    mismatch = detector.detect(
        accounts=[
            account(PlatformSource.GITHUB, "7", topics=six_left),
            account(PlatformSource.DEVTO, "8", topics=six_right),
        ]
    )

    conflict = mismatch.conflicts[0]
    assert conflict.conflict_type == ConflictType.TOPIC_MISMATCH
    assert conflict.severity == ConflictSeverity.LOW
    assert conflict.metadata["conflict_basis"] == "topics"


def test_conflict_detection_is_deterministic_regardless_of_input_order(
    detector: ConflictDetector,
) -> None:
    left = account(
        PlatformSource.GITHUB,
        "1",
        display_name="Muhammed Amil",
        website_url="https://amil.dev",
        location="Bangalore, India",
        email_domain="company1.com",
        topics=["python", "fastapi", "postgres", "docker", "kubernetes", "redis"],
    )
    right = account(
        PlatformSource.DEVTO,
        "2",
        display_name="David Lee",
        website_url="https://davidlee.dev",
        location="Toronto, Canada",
        email_domain="company2.com",
        topics=["go", "rust", "elixir", "phoenix", "mysql", "graphql"],
    )

    first = detector.detect(accounts=[left, right])
    second = detector.detect(accounts=[right, left])

    assert first.model_dump(mode="json") == second.model_dump(mode="json")
