from __future__ import annotations

from typing import Any
from uuid import UUID

from app.integrations import (
    DevToClient,
    GitHubClient,
    HackerNewsClient,
    StackOverflowClient,
)
from app.schemas.enums import PlatformSource
from app.schemas.ingestion import (
    CandidateFetchStatus,
    CandidateIdentity,
    CandidateIngestionResult,
    IngestionResult,
    RawRecordInsertSummary,
)
from app.schemas.requests import ProfileResolveRequest
from app.schemas.source_account import RawSourceRecord
from app.services.candidate_discovery import CandidateDiscoveryService
from app.storage.raw_records_repo import RawRecordsRepo
from app.utils.errors import (
    PlatformAPIError,
    PlatformNotFoundError,
    PlatformRateLimitError,
    PlatformTimeoutError,
    StorageError,
)


class IngestionService:
    """
    Fetches raw platform data for discovered candidates and stores it.

    This service intentionally does not normalize or resolve identity.
    """

    def __init__(
        self,
        *,
        candidate_discovery: CandidateDiscoveryService,
        raw_records_repo: RawRecordsRepo,
        github_client: GitHubClient,
        stackoverflow_client: StackOverflowClient,
        devto_client: DevToClient,
        hackernews_client: HackerNewsClient,
    ) -> None:
        self.candidate_discovery = candidate_discovery
        self.raw_records_repo = raw_records_repo
        self.github_client = github_client
        self.stackoverflow_client = stackoverflow_client
        self.devto_client = devto_client
        self.hackernews_client = hackernews_client

    async def ingest(
        self,
        *,
        request: ProfileResolveRequest,
        resolution_run_id: str | UUID,
    ) -> IngestionResult:
        run_uuid = UUID(str(resolution_run_id))
        discovery = self.candidate_discovery.discover(request)

        results: list[CandidateIngestionResult] = []

        for candidate in discovery.candidates:
            result = await self._ingest_candidate(
                candidate=candidate,
                resolution_run_id=run_uuid,
            )
            results.append(result)

        return IngestionResult(
            resolution_run_id=run_uuid,
            discovery=discovery,
            results=results,
        )

    async def _ingest_candidate(
        self,
        *,
        candidate: CandidateIdentity,
        resolution_run_id: UUID,
    ) -> CandidateIngestionResult:
        try:
            raw_bundle = await self._fetch_bundle(
                candidate=candidate,
                resolution_run_id=resolution_run_id,
            )

            inserted_records = self._store_raw_bundle(
                candidate=candidate,
                resolution_run_id=resolution_run_id,
                raw_bundle=raw_bundle,
            )

            return CandidateIngestionResult(
                candidate=candidate,
                status=CandidateFetchStatus.SUCCEEDED,
                raw_records=inserted_records,
                raw_bundle=raw_bundle,
            )

        except PlatformNotFoundError as exc:
            return self._failure_result(
                candidate=candidate,
                status=CandidateFetchStatus.NOT_FOUND,
                error_code="not_found",
                error_message=exc.public_message,
            )

        except PlatformRateLimitError as exc:
            return self._failure_result(
                candidate=candidate,
                status=CandidateFetchStatus.RATE_LIMITED,
                error_code="rate_limited",
                error_message=exc.public_message,
            )

        except PlatformTimeoutError as exc:
            return self._failure_result(
                candidate=candidate,
                status=CandidateFetchStatus.TIMED_OUT,
                error_code="timeout",
                error_message=exc.public_message,
            )

        except PlatformAPIError as exc:
            return self._failure_result(
                candidate=candidate,
                status=CandidateFetchStatus.FAILED,
                error_code=exc.code,
                error_message=exc.public_message,
            )

        except StorageError as exc:
            return self._failure_result(
                candidate=candidate,
                status=CandidateFetchStatus.FAILED,
                error_code=exc.code,
                error_message=exc.public_message,
            )

        except ValueError as exc:
            return self._failure_result(
                candidate=candidate,
                status=CandidateFetchStatus.SKIPPED,
                error_code="invalid_candidate",
                error_message=str(exc),
            )

    async def _fetch_bundle(
        self,
        *,
        candidate: CandidateIdentity,
        resolution_run_id: UUID,
    ) -> dict[str, Any]:
        if candidate.source == PlatformSource.GITHUB:
            return await self.github_client.fetch_profile_bundle(
                candidate.identifier,
                resolution_run_id=str(resolution_run_id),
            )

        if candidate.source == PlatformSource.STACKOVERFLOW:
            return await self.stackoverflow_client.fetch_profile_bundle(
                candidate.identifier,
                resolution_run_id=str(resolution_run_id),
            )

        if candidate.source == PlatformSource.DEVTO:
            return await self.devto_client.fetch_profile_bundle(
                candidate.identifier,
                resolution_run_id=str(resolution_run_id),
            )

        if candidate.source == PlatformSource.HACKERNEWS:
            return await self.hackernews_client.fetch_profile_bundle(
                candidate.identifier,
                resolution_run_id=str(resolution_run_id),
            )

        raise ValueError(f"Unsupported platform source: {candidate.source}")

    def _store_raw_bundle(
        self,
        *,
        candidate: CandidateIdentity,
        resolution_run_id: UUID,
        raw_bundle: dict[str, Any],
    ) -> list[RawRecordInsertSummary]:
        records = self._raw_records_from_bundle(
            candidate=candidate,
            resolution_run_id=resolution_run_id,
            raw_bundle=raw_bundle,
        )

        inserted_rows = self.raw_records_repo.insert_many_records(records)

        return [
            RawRecordInsertSummary(
                id=row["id"],
                source=PlatformSource(row["source"]),
                source_record_type=row["source_record_type"],
                source_user_id=row.get("source_user_id"),
                handle=row.get("handle"),
            )
            for row in inserted_rows
        ]

    def _raw_records_from_bundle(
        self,
        *,
        candidate: CandidateIdentity,
        resolution_run_id: UUID,
        raw_bundle: dict[str, Any],
    ) -> list[RawSourceRecord]:
        if candidate.source == PlatformSource.GITHUB:
            return self._github_raw_records(
                candidate=candidate,
                resolution_run_id=resolution_run_id,
                raw_bundle=raw_bundle,
            )

        if candidate.source == PlatformSource.STACKOVERFLOW:
            return self._stackoverflow_raw_records(
                candidate=candidate,
                resolution_run_id=resolution_run_id,
                raw_bundle=raw_bundle,
            )

        if candidate.source == PlatformSource.DEVTO:
            return self._devto_raw_records(
                candidate=candidate,
                resolution_run_id=resolution_run_id,
                raw_bundle=raw_bundle,
            )

        if candidate.source == PlatformSource.HACKERNEWS:
            return self._hackernews_raw_records(
                candidate=candidate,
                resolution_run_id=resolution_run_id,
                raw_bundle=raw_bundle,
            )

        raise ValueError(f"Unsupported platform source: {candidate.source}")

    def _github_raw_records(
        self,
        *,
        candidate: CandidateIdentity,
        resolution_run_id: UUID,
        raw_bundle: dict[str, Any],
    ) -> list[RawSourceRecord]:
        profile = raw_bundle.get("profile") or {}
        repos = raw_bundle.get("repos") or []

        username = str(profile.get("login") or candidate.identifier)
        source_user_id = self._optional_str(profile.get("id"))

        return [
            RawSourceRecord(
                resolution_run_id=resolution_run_id,
                source=PlatformSource.GITHUB,
                source_record_type="github/profile",
                source_user_id=source_user_id,
                handle=username,
                request_url=f"https://api.github.com/users/{username}",
                profile_url=profile.get("html_url") or f"https://github.com/{username}",
                raw_payload=profile,
                http_status=200,
            ),
            RawSourceRecord(
                resolution_run_id=resolution_run_id,
                source=PlatformSource.GITHUB,
                source_record_type="github/repos",
                source_user_id=source_user_id,
                handle=username,
                request_url=(
                    f"https://api.github.com/users/{username}/repos"
                    "?per_page=30&sort=updated&direction=desc"
                ),
                profile_url=profile.get("html_url") or f"https://github.com/{username}",
                raw_payload=repos,
                http_status=200,
            ),
        ]

    def _stackoverflow_raw_records(
        self,
        *,
        candidate: CandidateIdentity,
        resolution_run_id: UUID,
        raw_bundle: dict[str, Any],
    ) -> list[RawSourceRecord]:
        user_wrapper = raw_bundle.get("user") or {"items": []}
        answers = raw_bundle.get("answers") or {"items": []}
        questions = raw_bundle.get("questions") or {"items": []}

        user_items = user_wrapper.get("items") if isinstance(user_wrapper, dict) else []
        first_user = user_items[0] if isinstance(user_items, list) and user_items else {}

        user_id = candidate.identifier
        profile_url = first_user.get("link") or f"https://stackoverflow.com/users/{user_id}"

        return [
            RawSourceRecord(
                resolution_run_id=resolution_run_id,
                source=PlatformSource.STACKOVERFLOW,
                source_record_type="stackoverflow/user",
                source_user_id=user_id,
                handle=self._optional_str(first_user.get("display_name")),
                request_url=(
                    f"https://api.stackexchange.com/2.3/users/{user_id}"
                    "?site=stackoverflow"
                ),
                profile_url=profile_url,
                raw_payload=user_wrapper,
                http_status=200,
            ),
            RawSourceRecord(
                resolution_run_id=resolution_run_id,
                source=PlatformSource.STACKOVERFLOW,
                source_record_type="stackoverflow/answers",
                source_user_id=user_id,
                handle=self._optional_str(first_user.get("display_name")),
                request_url=(
                    f"https://api.stackexchange.com/2.3/users/{user_id}/answers"
                    "?site=stackoverflow&pagesize=20&sort=votes"
                ),
                profile_url=profile_url,
                raw_payload=answers,
                http_status=200,
            ),
            RawSourceRecord(
                resolution_run_id=resolution_run_id,
                source=PlatformSource.STACKOVERFLOW,
                source_record_type="stackoverflow/questions",
                source_user_id=user_id,
                handle=self._optional_str(first_user.get("display_name")),
                request_url=(
                    f"https://api.stackexchange.com/2.3/users/{user_id}/questions"
                    "?site=stackoverflow&pagesize=20&sort=votes"
                ),
                profile_url=profile_url,
                raw_payload=questions,
                http_status=200,
            ),
        ]

    def _devto_raw_records(
        self,
        *,
        candidate: CandidateIdentity,
        resolution_run_id: UUID,
        raw_bundle: dict[str, Any],
    ) -> list[RawSourceRecord]:
        user = raw_bundle.get("user") or {}
        articles = raw_bundle.get("articles") or []

        username = str(user.get("username") or candidate.identifier)
        user_id = self._optional_str(user.get("id"))
        profile_url = f"https://dev.to/{username}"

        return [
            RawSourceRecord(
                resolution_run_id=resolution_run_id,
                source=PlatformSource.DEVTO,
                source_record_type="devto/user",
                source_user_id=user_id,
                handle=username,
                request_url=f"https://dev.to/api/users/by_username?url={username}",
                profile_url=profile_url,
                raw_payload=user,
                http_status=200,
            ),
            RawSourceRecord(
                resolution_run_id=resolution_run_id,
                source=PlatformSource.DEVTO,
                source_record_type="devto/articles",
                source_user_id=user_id,
                handle=username,
                request_url=(
                    f"https://dev.to/api/articles?username={username}&per_page=30"
                ),
                profile_url=profile_url,
                raw_payload=articles,
                http_status=200,
            ),
        ]

    def _hackernews_raw_records(
        self,
        *,
        candidate: CandidateIdentity,
        resolution_run_id: UUID,
        raw_bundle: dict[str, Any],
    ) -> list[RawSourceRecord]:
        user = raw_bundle.get("user") or {}
        activity = raw_bundle.get("activity") or {"hits": []}

        username = str(user.get("username") or candidate.identifier)
        profile_url = f"https://news.ycombinator.com/user?id={username}"

        return [
            RawSourceRecord(
                resolution_run_id=resolution_run_id,
                source=PlatformSource.HACKERNEWS,
                source_record_type="hackernews/user",
                source_user_id=username,
                handle=username,
                request_url=f"https://hn.algolia.com/api/v1/users/{username}",
                profile_url=profile_url,
                raw_payload=user,
                http_status=200,
            ),
            RawSourceRecord(
                resolution_run_id=resolution_run_id,
                source=PlatformSource.HACKERNEWS,
                source_record_type="hackernews/activity",
                source_user_id=username,
                handle=username,
                request_url=(
                    "https://hn.algolia.com/api/v1/search_by_date"
                    f"?tags=author_{username}&hitsPerPage=30"
                ),
                profile_url=profile_url,
                raw_payload=activity,
                http_status=200,
            ),
        ]

    def _failure_result(
        self,
        *,
        candidate: CandidateIdentity,
        status: CandidateFetchStatus,
        error_code: str,
        error_message: str,
    ) -> CandidateIngestionResult:
        return CandidateIngestionResult(
            candidate=candidate,
            status=status,
            error_code=error_code,
            error_message=error_message,
        )

    def _optional_str(self, value: Any) -> str | None:
        if value is None:
            return None

        cleaned = str(value).strip()
        return cleaned or None
