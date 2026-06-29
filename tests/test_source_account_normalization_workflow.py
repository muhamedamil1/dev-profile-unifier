from __future__ import annotations

from typing import Any

import pytest

from app.config import get_settings
from app.dependencies import (
    get_ingestion_service,
    get_resolution_runs_repo,
    get_source_account_normalization_service,
)
from app.schemas.requests import ProfileResolveRequest
from app.storage.supabase_client import get_supabase_client


pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio,
]


def _secret_present(value: Any) -> bool:
    if value is None:
        return False

    if hasattr(value, "get_secret_value"):
        return bool(value.get_secret_value().strip())

    return bool(str(value).strip())


def _skip_if_integration_settings_missing() -> None:
    settings = get_settings()

    missing: list[str] = []

    if not getattr(settings, "supabase_url", None):
        missing.append("SUPABASE_URL")

    if not _secret_present(getattr(settings, "supabase_service_role_key", None)):
        missing.append("SUPABASE_SERVICE_ROLE_KEY")

    if not _secret_present(getattr(settings, "github_token", None)):
        missing.append("GITHUB_TOKEN")

    if missing:
        pytest.skip(
            "Integration test skipped because required settings are missing: "
            + ", ".join(missing)
        )


def _create_resolution_run(request: ProfileResolveRequest, sources: list[str]) -> dict[str, Any]:
    runs_repo = get_resolution_runs_repo()

    return runs_repo.create_run(
        input_name=request.name,
        input_payload=request.safe_input_payload(),
        sources_attempted=sources,
    )


def _all_warning_messages(normalization_result: Any) -> list[str]:
    messages: list[str] = []

    for warning in normalization_result.warnings:
        messages.append(warning.message)

    for account_result in normalization_result.accounts:
        for warning in account_result.warnings:
            messages.append(warning.message)

    return messages


def _assert_no_blocking_normalization_warnings(normalization_result: Any) -> None:
    blocking_fragments = (
        "normalizer failed",
        "persistence failed",
        "could not be normalized",
    )

    messages = _all_warning_messages(normalization_result)
    blocking_messages = [
        message
        for message in messages
        if any(fragment in message.lower() for fragment in blocking_fragments)
    ]

    assert blocking_messages == []


def _assert_lineage_is_preserved(account_result: Any, *, minimum_raw_records: int) -> None:
    account = account_result.source_account

    assert account.raw_source_record_id is not None

    raw_lineage = account.activity_payload.get("raw_source_record_ids")

    assert isinstance(raw_lineage, list)
    assert len(raw_lineage) >= minimum_raw_records
    assert str(account.raw_source_record_id) in raw_lineage

    normalized_result_ids = {str(record_id) for record_id in account_result.raw_record_ids}
    lineage_ids = {str(record_id) for record_id in raw_lineage}

    assert normalized_result_ids.issubset(lineage_ids)


def _assert_own_profile_url_not_in_outbound_links(account: Any) -> None:
    if not account.profile_url:
        return

    outbound_links = {link.rstrip("/") for link in account.outbound_links}
    assert account.profile_url.rstrip("/") not in outbound_links


def _rows_for_source_account_key(source_account_key: str) -> list[dict[str, Any]]:
    response = (
        get_supabase_client()
        .table("source_accounts")
        .select("id, source_account_key")
        .eq("source_account_key", source_account_key)
        .execute()
    )

    return response.data or []


async def test_github_raw_ingestion_normalizes_to_source_account() -> None:
    _skip_if_integration_settings_missing()

    request = ProfileResolveRequest(
        name="Octocat",
        github="octocat",
    )

    run = _create_resolution_run(request, ["github"])

    ingestion_service = get_ingestion_service()
    ingestion_result = await ingestion_service.ingest(
        request=request,
        resolution_run_id=run["id"],
    )

    assert len(ingestion_result.discovery.candidates) == 1
    assert len(ingestion_result.succeeded) == 1
    assert len(ingestion_result.failed) == 0

    normalization_service = get_source_account_normalization_service()
    normalization_result = normalization_service.normalize_run(
        resolution_run_id=run["id"],
        persist=True,
    )

    assert normalization_result.normalized_count == 1
    _assert_no_blocking_normalization_warnings(normalization_result)

    account_result = normalization_result.accounts[0]
    account = account_result.source_account

    assert account.source.value == "github"
    assert account.source_user_id
    assert account.handle == "octocat"
    assert account.profile_url == "https://github.com/octocat"
    assert account.activity_payload.get("normalization_version") == "github_normalizer_v1"
    
    assert account_result.persisted_row is not None
    assert account_result.persisted_row["source_account_key"] == account.expected_source_account_key()

    _assert_lineage_is_preserved(account_result, minimum_raw_records=2)
    _assert_own_profile_url_not_in_outbound_links(account)

    rows = _rows_for_source_account_key(account.expected_source_account_key())
    assert len(rows) == 1

    second_normalization_result = normalization_service.normalize_run(
        resolution_run_id=run["id"],
        persist=True,
    )

    assert second_normalization_result.normalized_count == 1

    rows_after_second_run = _rows_for_source_account_key(account.expected_source_account_key())
    assert len(rows_after_second_run) == 1


async def test_multiple_platform_raw_ingestion_normalizes_to_source_accounts() -> None:
    _skip_if_integration_settings_missing()

    request = ProfileResolveRequest(
        name="Multi Platform Test",
        github="octocat",
        devto="ben",
        hackernews="pg",
        stackoverflow_user_id="22656",
    )

    expected_sources = {
        "github",
        "devto",
        "hackernews",
        "stackoverflow",
    }

    run = _create_resolution_run(
        request,
        ["github", "devto", "hackernews", "stackoverflow"],
    )

    ingestion_service = get_ingestion_service()
    ingestion_result = await ingestion_service.ingest(
        request=request,
        resolution_run_id=run["id"],
    )

    failed_sources = [
        {
            "source": item.candidate.source.value,
            "identifier": item.candidate.identifier,
            "status": item.status.value,
            "error_code": item.error_code,
            "error_message": item.error_message,
        }
        for item in ingestion_result.failed
    ]

    assert failed_sources == []
    assert set(ingestion_result.sources_succeeded) == expected_sources

    normalization_service = get_source_account_normalization_service()
    normalization_result = normalization_service.normalize_run(
        resolution_run_id=run["id"],
        persist=True,
    )

    _assert_no_blocking_normalization_warnings(normalization_result)

    normalized_sources = {
        account_result.source_account.source.value
        for account_result in normalization_result.accounts
    }

    assert normalized_sources == expected_sources
    assert normalization_result.normalized_count == len(expected_sources)

    accounts_by_source = {
        account_result.source_account.source.value: account_result
        for account_result in normalization_result.accounts
    }

    github_result = accounts_by_source["github"]
    assert github_result.source_account.handle == "octocat"
    assert github_result.source_account.profile_url == "https://github.com/octocat"
    assert github_result.source_account.activity_payload.get("normalization_version") == "github_normalizer_v1"
    _assert_lineage_is_preserved(github_result, minimum_raw_records=2)

    devto_result = accounts_by_source["devto"]
    assert devto_result.source_account.handle == "ben"
    assert devto_result.source_account.profile_url
    assert devto_result.source_account.activity_payload.get("normalization_version") == "devto_normalizer_v1"
    _assert_lineage_is_preserved(devto_result, minimum_raw_records=2)

    hackernews_result = accounts_by_source["hackernews"]
    assert hackernews_result.source_account.handle == "pg"
    assert hackernews_result.source_account.profile_url == "https://news.ycombinator.com/user?id=pg"
    assert hackernews_result.source_account.activity_payload.get("normalization_version") == "hackernews_normalizer_v1"
    assert hackernews_result.source_account.activity_payload.get("weak_identity_source") is True
    _assert_lineage_is_preserved(hackernews_result, minimum_raw_records=2)

    stackoverflow_result = accounts_by_source["stackoverflow"]
    assert stackoverflow_result.source_account.source_user_id == "22656"
    assert stackoverflow_result.source_account.profile_url
    assert stackoverflow_result.source_account.activity_payload.get("normalization_version") == "stackoverflow_normalizer_v1"
    _assert_lineage_is_preserved(stackoverflow_result, minimum_raw_records=3)

    for account_result in normalization_result.accounts:
        account = account_result.source_account

        assert account_result.persisted_row is not None
        assert account_result.persisted_row["source_account_key"] == account.expected_source_account_key()

        assert account.profile_url
        assert account.activity_payload.get("raw_source_record_ids")
        assert account.activity_payload.get("normalization_version")
        assert isinstance(account.topics, list)
        assert isinstance(account.outbound_links, list)

        _assert_own_profile_url_not_in_outbound_links(account)

        rows = _rows_for_source_account_key(account.expected_source_account_key())
        assert len(rows) == 1