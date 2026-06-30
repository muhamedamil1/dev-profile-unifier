from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4
import sys

sys.modules.setdefault("supabase", SimpleNamespace(Client=object, create_client=lambda *args, **kwargs: object()))

from app.schemas.enums import PlatformSource
from app.schemas.normalization import NormalizedAccountResult
from app.schemas.source_account import SourceAccount
from app.services.devto_normalizer import DevToAccountNormalizer
from app.services.github_normalizer import GitHubAccountNormalizer
from app.services.hackernews_normalizer import HackerNewsAccountNormalizer
from app.services.source_account_normalization_service import SourceAccountNormalizationService
from app.services.stackoverflow_normalizer import StackOverflowAccountNormalizer
from app.storage.source_accounts_repo import SourceAccountsRepo
from app.utils.urls import extract_urls_from_text


def _record(
    *,
    source: str,
    source_record_type: str,
    raw_payload,
    source_user_id: str | None = None,
    handle: str | None = None,
    profile_url: str | None = None,
) -> dict:
    return {
        "id": str(uuid4()),
        "source": source,
        "source_record_type": source_record_type,
        "source_user_id": source_user_id,
        "handle": handle,
        "profile_url": profile_url,
        "raw_payload": raw_payload,
    }


def test_github_outbound_links_exclude_profile_url_and_warn_without_repos() -> None:
    profile = _record(
        source="github",
        source_record_type="github/profile",
        source_user_id="583231",
        handle="octocat",
        profile_url="https://github.com/octocat",
        raw_payload={
            "id": 583231,
            "login": "octocat",
            "html_url": "https://github.com/octocat",
            "blog": "https://example.com",
            "bio": "Writing at https://blog.example.com.",
        },
    )

    result = GitHubAccountNormalizer().normalize([profile])

    assert result is not None
    assert result.source_account.profile_url == "https://github.com/octocat"
    assert "https://github.com/octocat" not in result.source_account.outbound_links
    assert "https://example.com" in result.source_account.outbound_links
    assert "https://blog.example.com" in result.source_account.outbound_links
    assert [warning.message for warning in result.warnings] == [
        "GitHub profile normalized without repos raw record."
    ]


def test_stackoverflow_outbound_links_exclude_profile_url_and_warn_without_activity() -> None:
    user = _record(
        source="stackoverflow",
        source_record_type="stackoverflow/user",
        source_user_id="123",
        raw_payload={
            "items": [
                {
                    "user_id": 123,
                    "display_name": "Ada",
                    "link": "https://stackoverflow.com/users/123/ada",
                    "website_url": "https://ada.example.com",
                }
            ]
        },
    )

    result = StackOverflowAccountNormalizer().normalize([user])

    assert result is not None
    assert result.source_account.outbound_links == ["https://ada.example.com"]
    assert {warning.source_record_type for warning in result.warnings} == {
        "stackoverflow/answers",
        "stackoverflow/questions",
    }


def test_devto_outbound_links_exclude_profile_url_and_warn_without_articles() -> None:
    user = _record(
        source="devto",
        source_record_type="devto/user",
        handle="ada",
        raw_payload={
            "id": 55,
            "username": "ada",
            "url": "https://dev.to/ada",
            "website_url": "https://ada.example.com",
            "github_username": "ada-gh",
            "summary": "More at https://writing.example.com.",
        },
    )

    result = DevToAccountNormalizer().normalize([user])

    assert result is not None
    assert "https://dev.to/ada" not in result.source_account.outbound_links
    assert "https://github.com/ada-gh" in result.source_account.outbound_links
    assert "https://writing.example.com" in result.source_account.outbound_links
    assert [warning.source_record_type for warning in result.warnings] == ["devto/articles"]


def test_hackernews_outbound_links_exclude_profile_url_and_warn_without_activity() -> None:
    user = _record(
        source="hackernews",
        source_record_type="hackernews/user",
        handle="ada",
        profile_url="https://news.ycombinator.com/user?id=ada",
        raw_payload={
            "username": "ada",
            "about": "homepage https://ada.example.com.",
        },
    )

    result = HackerNewsAccountNormalizer().normalize([user])

    assert result is not None
    assert "https://news.ycombinator.com/user?id=ada" not in result.source_account.outbound_links
    assert result.source_account.outbound_links == ["https://ada.example.com"]
    assert [warning.source_record_type for warning in result.warnings] == ["hackernews/activity"]


def test_extract_urls_from_text_strips_sentence_punctuation() -> None:
    assert extract_urls_from_text("See https://example.com/path.") == [
        "https://example.com/path"
    ]


class _RawRepo:
    def __init__(self, records: list[dict]) -> None:
        self.records = records

    def list_by_run(self, resolution_run_id: UUID) -> list[dict]:
        return self.records


class _SourceAccountsRepo:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.accounts: list[SourceAccount] = []

    def upsert_account(self, account: SourceAccount) -> dict:
        self.accounts.append(account)
        if self.fail:
            raise RuntimeError("database unavailable")
        return {"id": str(uuid4()), **account.to_db_payload()}


class _ExplodingNormalizer:
    def normalize(self, records: list[dict]) -> NormalizedAccountResult | None:
        raise RuntimeError("unexpected profile shape")


class _PassthroughNormalizer:
    def normalize(self, records: list[dict]) -> NormalizedAccountResult | None:
        account = SourceAccount(
            source=PlatformSource.GITHUB,
            source_user_id="583231",
            handle="octocat",
        )
        return NormalizedAccountResult(source_account=account)


def test_grouping_uses_handle_alias_for_secondary_records() -> None:
    profile_id = str(uuid4())
    repos_id = str(uuid4())
    records = [
        {
            "id": profile_id,
            "source": "github",
            "source_record_type": "github/profile",
            "source_user_id": "583231",
            "handle": "Octocat",
            "raw_payload": {},
        },
        {
            "id": repos_id,
            "source": "github",
            "source_record_type": "github/repos",
            "source_user_id": None,
            "handle": "octocat",
            "raw_payload": [],
        },
    ]
    service = SourceAccountNormalizationService(
        raw_records_repo=_RawRepo(records),
        source_accounts_repo=_SourceAccountsRepo(),
    )

    grouped, warnings = service._group_records(records)

    assert warnings == []
    assert list(grouped[PlatformSource.GITHUB]) == ["id:583231"]
    assert {record["id"] for record in grouped[PlatformSource.GITHUB]["id:583231"]} == {
        profile_id,
        repos_id,
    }


def test_normalize_run_isolates_normalizer_and_persistence_exceptions() -> None:
    records = [
        _record(
            source="github",
            source_record_type="github/profile",
            source_user_id="583231",
            handle="octocat",
            raw_payload={},
        )
    ]
    service = SourceAccountNormalizationService(
        raw_records_repo=_RawRepo(records),
        source_accounts_repo=_SourceAccountsRepo(fail=True),
        github_normalizer=_PassthroughNormalizer(),
    )

    result = service.normalize_run(resolution_run_id=uuid4(), persist=True)

    assert result.normalized_count == 1
    assert result.accounts[0].persisted_row is None
    assert result.accounts[0].warnings[0].details["group_key"] == "id:583231"
    assert result.accounts[0].warnings[0].details["error_type"] == "RuntimeError"

    service = SourceAccountNormalizationService(
        raw_records_repo=_RawRepo(records),
        source_accounts_repo=_SourceAccountsRepo(),
        github_normalizer=_ExplodingNormalizer(),
    )

    result = service.normalize_run(resolution_run_id=uuid4(), persist=True)

    assert result.normalized_count == 0
    assert result.warnings[0].details["group_key"] == "id:583231"
    assert result.warnings[0].details["record_types"] == ["github/profile"]
    assert result.warnings[0].details["error_type"] == "RuntimeError"


def test_normalize_run_copies_persisted_id_to_source_account() -> None:
    records = [
        _record(
            source="github",
            source_record_type="github/profile",
            source_user_id="583231",
            handle="octocat",
            raw_payload={},
        )
    ]
    service = SourceAccountNormalizationService(
        raw_records_repo=_RawRepo(records),
        source_accounts_repo=_SourceAccountsRepo(),
        github_normalizer=_PassthroughNormalizer(),
    )

    result = service.normalize_run(resolution_run_id=uuid4(), persist=True)

    persisted_id = result.accounts[0].persisted_row["id"]
    assert result.accounts[0].source_account.id == UUID(persisted_id)


class _ExecuteResponse:
    def __init__(self, data=None) -> None:
        self.data = data if data is not None else [{"id": "persisted"}]


class _Query:
    def __init__(self, client=None) -> None:
        self.client = client
        self.operation = "table"
        self.upsert_payload = None
        self.on_conflict = None
        self.filters = []

    def upsert(self, payload: dict, *, on_conflict: str):
        self.operation = "upsert"
        self.upsert_payload = payload
        self.on_conflict = on_conflict
        return self

    def select(self, payload: str):
        self.operation = "select"
        return self

    def eq(self, key: str, value: str):
        self.filters.append((key, value))
        return self

    def limit(self, value: int):
        return self

    def execute(self) -> _ExecuteResponse:
        if self.client is not None and self.client.empty_upsert_response and self.operation == "upsert":
            return _ExecuteResponse([])
        if self.operation == "select" and self.client is not None and self.client.select_row is not None:
            return _ExecuteResponse([self.client.select_row])
        return _ExecuteResponse()


class _Client:
    def __init__(self) -> None:
        self.empty_upsert_response = False
        self.select_row = None
        self.query = _Query(self)
        self.table_name = None

    def table(self, table_name: str) -> _Query:
        self.table_name = table_name
        return self.query


def test_source_accounts_repo_upsert_uses_source_account_key_conflict() -> None:
    client = _Client()
    repo = SourceAccountsRepo(client)  # type: ignore[arg-type]
    account = SourceAccount(
        source=PlatformSource.GITHUB,
        source_user_id="583231",
        handle="octocat",
        topics=["python"],
        outbound_links=["https://example.com"],
    )

    row = repo.upsert_account(account)

    assert row == {"id": "persisted"}
    assert client.table_name == "source_accounts"
    assert client.query.on_conflict == "source_account_key"
    assert client.query.upsert_payload["source_account_key"] == "github:583231"
    assert client.query.upsert_payload["source"] == "github"

def test_source_accounts_repo_reads_back_empty_upsert_response_by_key() -> None:
    client = _Client()
    client.empty_upsert_response = True
    client.select_row = {"id": "persisted", "source_account_key": "github:583231"}
    repo = SourceAccountsRepo(client)  # type: ignore[arg-type]
    account = SourceAccount(
        source=PlatformSource.GITHUB,
        source_user_id="583231",
        handle="octocat",
    )

    row = repo.upsert_account(account)

    assert row == {"id": "persisted", "source_account_key": "github:583231"}
    assert ("source_account_key", "github:583231") in client.query.filters
