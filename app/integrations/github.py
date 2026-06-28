from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

import httpx

from app.integrations.base import BaseExternalAPIClient, RateLimitInfo
from app.schemas.enums import MetricSource
from app.storage.metrics_repo import MetricsRepo
from app.utils.errors import PlatformNotFoundError


_GITHUB_USERNAME_RE = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$"
)


class GitHubClient(BaseExternalAPIClient):
    def __init__(
        self,
        *,
        timeout_seconds: int,
        metrics_repo: MetricsRepo | None = None,
        token: str | None = None,
    ) -> None:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        if token:
            headers["Authorization"] = f"Bearer {token}"

        super().__init__(
            source=MetricSource.GITHUB,
            base_url="https://api.github.com",
            timeout_seconds=timeout_seconds,
            metrics_repo=metrics_repo,
            default_headers=headers,
        )

    def extract_rate_limit(
        self,
        response: httpx.Response,
        data: Any,
    ) -> RateLimitInfo:
        raw = {
            "limit": response.headers.get("x-ratelimit-limit"),
            "remaining": response.headers.get("x-ratelimit-remaining"),
            "used": response.headers.get("x-ratelimit-used"),
            "reset": response.headers.get("x-ratelimit-reset"),
            "resource": response.headers.get("x-ratelimit-resource"),
            "retry_after": response.headers.get("retry-after"),
        }

        return RateLimitInfo(
            total=self._parse_int(raw["limit"]),
            remaining=self._parse_int(raw["remaining"]),
            reset_at=self._epoch_to_datetime(raw["reset"]),
            retry_after_seconds=self._parse_int(raw["retry_after"]),
            raw={key: value for key, value in raw.items() if value is not None},
        )

    async def fetch_user(
        self,
        username: str,
        *,
        resolution_run_id: str | None = None,
    ) -> dict[str, Any]:
        clean_username = self._clean_username(username)

        response = await self.request_json(
            "GET",
            f"/users/{quote(clean_username)}",
            resolution_run_id=resolution_run_id,
        )

        if not isinstance(response.data, dict):
            raise PlatformNotFoundError(
                "GitHub user response was empty.",
                details={"source": "github", "username": clean_username},
            )

        return response.data

    async def fetch_repos(
        self,
        username: str,
        *,
        per_page: int = 30,
        resolution_run_id: str | None = None,
    ) -> list[dict[str, Any]]:
        clean_username = self._clean_username(username)
        safe_per_page = min(max(per_page, 1), 100)

        response = await self.request_json(
            "GET",
            f"/users/{quote(clean_username)}/repos",
            params={
                "per_page": safe_per_page,
                "sort": "updated",
                "direction": "desc",
            },
            resolution_run_id=resolution_run_id,
        )

        if not isinstance(response.data, list):
            return []

        return response.data

    async def fetch_profile_bundle(
        self,
        username: str,
        *,
        resolution_run_id: str | None = None,
    ) -> dict[str, Any]:
        clean_username = self._clean_username(username)

        profile = await self.fetch_user(
            clean_username,
            resolution_run_id=resolution_run_id,
        )
        repos = await self.fetch_repos(
            clean_username,
            per_page=30,
            resolution_run_id=resolution_run_id,
        )

        return {
            "source": "github",
            "username": clean_username,
            "profile": profile,
            "repos": repos,
        }

    def _clean_username(self, username: str) -> str:
        cleaned = username.strip().lstrip("@")

        if not cleaned:
            raise ValueError("GitHub username must not be empty")

        if "/" in cleaned or "\\" in cleaned:
            raise ValueError("GitHub username must not be a URL or path")

        if not _GITHUB_USERNAME_RE.fullmatch(cleaned):
            raise ValueError("GitHub username contains invalid characters")

        return cleaned