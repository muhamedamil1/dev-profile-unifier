from __future__ import annotations

import re
from typing import Any

from app.integrations.base import AsyncWindowRateLimiter, BaseExternalAPIClient
from app.schemas.enums import MetricSource
from app.storage.metrics_repo import MetricsRepo
from app.utils.errors import PlatformNotFoundError


_DEVTO_LIMITER = AsyncWindowRateLimiter(max_calls=10, window_seconds=30.0)
_DEVTO_USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,49}$")


class DevToClient(BaseExternalAPIClient):
    def __init__(
        self,
        *,
        timeout_seconds: int,
        metrics_repo: MetricsRepo | None = None,
    ) -> None:
        super().__init__(
            source=MetricSource.DEVTO,
            base_url="https://dev.to/api",
            timeout_seconds=timeout_seconds,
            metrics_repo=metrics_repo,
            default_headers={
                "Accept": "application/json",
            },
            rate_limiter=_DEVTO_LIMITER,
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
            "/users/by_username",
            params={"url": clean_username},
            resolution_run_id=resolution_run_id,
        )

        if not isinstance(response.data, dict):
            raise PlatformNotFoundError(
                "dev.to user response was empty.",
                details={"source": "devto", "username": clean_username},
            )

        return response.data

    async def fetch_articles(
        self,
        username: str,
        *,
        per_page: int = 30,
        resolution_run_id: str | None = None,
    ) -> list[dict[str, Any]]:
        clean_username = self._clean_username(username)

        response = await self.request_json(
            "GET",
            "/articles",
            params={
                "username": clean_username,
                "per_page": min(max(per_page, 1), 100),
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

        user = await self.fetch_user(
            clean_username,
            resolution_run_id=resolution_run_id,
        )
        articles = await self.fetch_articles(
            clean_username,
            per_page=30,
            resolution_run_id=resolution_run_id,
        )

        return {
            "source": "devto",
            "username": clean_username,
            "user": user,
            "articles": articles,
        }

    def _clean_username(self, username: str) -> str:
        cleaned = username.strip().lstrip("@")

        if not cleaned:
            raise ValueError("dev.to username must not be empty")

        if "/" in cleaned or "\\" in cleaned:
            raise ValueError("dev.to username must not be a URL or path")

        if not _DEVTO_USERNAME_RE.fullmatch(cleaned):
            raise ValueError("dev.to username contains invalid characters")

        return cleaned