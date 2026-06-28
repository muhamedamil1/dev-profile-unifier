from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from app.integrations.base import AsyncWindowRateLimiter, BaseExternalAPIClient
from app.schemas.enums import MetricSource
from app.storage.metrics_repo import MetricsRepo
from app.utils.errors import PlatformNotFoundError


# HN Algolia is public and unauthenticated, so this client stays conservative.
_HN_LIMITER = AsyncWindowRateLimiter(max_calls=30, window_seconds=60.0)
_HN_USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,100}$")


class HackerNewsClient(BaseExternalAPIClient):
    def __init__(
        self,
        *,
        timeout_seconds: int,
        metrics_repo: MetricsRepo | None = None,
    ) -> None:
        super().__init__(
            source=MetricSource.HACKERNEWS,
            base_url="https://hn.algolia.com/api/v1",
            timeout_seconds=timeout_seconds,
            metrics_repo=metrics_repo,
            default_headers={
                "Accept": "application/json",
            },
            rate_limiter=_HN_LIMITER,
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
                "Hacker News user response was empty.",
                details={"source": "hackernews", "username": clean_username},
            )

        if not response.data.get("username"):
            raise PlatformNotFoundError(
                "Hacker News user was not found.",
                details={"source": "hackernews", "username": clean_username},
            )

        return response.data

    async def fetch_activity(
        self,
        username: str,
        *,
        hits_per_page: int = 30,
        resolution_run_id: str | None = None,
    ) -> dict[str, Any]:
        clean_username = self._clean_username(username)

        response = await self.request_json(
            "GET",
            "/search_by_date",
            params={
                "tags": f"author_{clean_username}",
                "hitsPerPage": min(max(hits_per_page, 1), 100),
            },
            resolution_run_id=resolution_run_id,
        )

        if not isinstance(response.data, dict):
            return {"hits": []}

        return response.data

    async def fetch_profile_bundle(
        self,
        username: str,
        *,
        resolution_run_id: str | None = None,
    ) -> dict[str, Any]:
        clean_username = self._clean_username(username)

        user = await self.fetch_user(
            clean_username,
            resolution_run_id=resolution_run_id,
        )
        activity = await self.fetch_activity(
            clean_username,
            hits_per_page=30,
            resolution_run_id=resolution_run_id,
        )

        return {
            "source": "hackernews",
            "username": clean_username,
            "user": user,
            "activity": activity,
        }

    def _clean_username(self, username: str) -> str:
        cleaned = username.strip().lstrip("@")

        if not cleaned:
            raise ValueError("Hacker News username must not be empty")

        if "/" in cleaned or "\\" in cleaned:
            raise ValueError("Hacker News username must not be a URL or path")

        if not _HN_USERNAME_RE.fullmatch(cleaned):
            raise ValueError("Hacker News username contains invalid characters")

        return cleaned