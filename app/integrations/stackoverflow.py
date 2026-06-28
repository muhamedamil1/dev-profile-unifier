from __future__ import annotations

from datetime import datetime, timedelta

try:
    from datetime import UTC
except ImportError:  # Python 3.10 compatibility.
    from datetime import timezone

    UTC = timezone.utc
from typing import Any
from urllib.parse import quote

import httpx

from app.integrations.base import BaseExternalAPIClient, RateLimitInfo
from app.schemas.enums import MetricSource
from app.storage.metrics_repo import MetricsRepo
from app.utils.errors import PlatformAPIError, PlatformNotFoundError, PlatformRateLimitError


class StackOverflowClient(BaseExternalAPIClient):
    def __init__(
        self,
        *,
        timeout_seconds: int,
        metrics_repo: MetricsRepo | None = None,
        api_key: str | None = None,
    ) -> None:
        self.api_key = api_key
        self._backoff_until_by_method: dict[str, datetime] = {}

        super().__init__(
            source=MetricSource.STACKOVERFLOW,
            base_url="https://api.stackexchange.com/2.3",
            timeout_seconds=timeout_seconds,
            metrics_repo=metrics_repo,
            default_headers={
                "Accept": "application/json",
            },
        )

    def extract_rate_limit(
        self,
        response: httpx.Response,
        data: Any,
    ) -> RateLimitInfo:
        retry_after = self._parse_int(response.headers.get("retry-after"))

        if isinstance(data, dict):
            quota_max = data.get("quota_max")
            quota_remaining = data.get("quota_remaining")
            backoff = data.get("backoff")

            raw = {
                "quota_max": quota_max,
                "quota_remaining": quota_remaining,
                "backoff": backoff,
                "error_id": data.get("error_id"),
                "error_name": data.get("error_name"),
            }

            return RateLimitInfo(
                total=quota_max if isinstance(quota_max, int) else None,
                remaining=quota_remaining if isinstance(quota_remaining, int) else None,
                retry_after_seconds=backoff
                if isinstance(backoff, int)
                else retry_after,
                raw={key: value for key, value in raw.items() if value is not None},
            )

        return RateLimitInfo(
            retry_after_seconds=retry_after,
            raw={"retry_after": retry_after} if retry_after is not None else {},
        )

    def is_error_payload(self, data: Any) -> bool:
        return isinstance(data, dict) and data.get("error_id") is not None

    def extract_error_message(self, data: Any) -> str:
        if isinstance(data, dict):
            error_message = data.get("error_message")
            error_name = data.get("error_name")

            if error_name and error_message:
                return f"{error_name}: {error_message}"

            if error_message:
                return str(error_message)

        return super().extract_error_message(data)

    async def fetch_user(
        self,
        user_id: str,
        *,
        resolution_run_id: str | None = None,
    ) -> dict[str, Any]:
        clean_user_id = self._clean_user_id(user_id)
        method_key = "users"
        await self._respect_backoff(method_key)

        response = await self.request_json(
            "GET",
            f"/users/{quote(clean_user_id)}",
            params=self._params(),
            resolution_run_id=resolution_run_id,
        )

        self._raise_if_stack_error(response.data, endpoint="/users/{id}")
        self._remember_backoff(method_key, response.data)

        items = self._items(response.data)
        if not items:
            raise PlatformNotFoundError(
                "Stack Overflow user was not found.",
                details={"source": "stackoverflow", "user_id": clean_user_id},
            )

        return response.data

    async def fetch_answers(
        self,
        user_id: str,
        *,
        pagesize: int = 20,
        resolution_run_id: str | None = None,
    ) -> dict[str, Any]:
        clean_user_id = self._clean_user_id(user_id)
        method_key = "users_answers"
        await self._respect_backoff(method_key)

        response = await self.request_json(
            "GET",
            f"/users/{quote(clean_user_id)}/answers",
            params={
                **self._params(),
                "pagesize": min(max(pagesize, 1), 100),
                "order": "desc",
                "sort": "votes",
            },
            resolution_run_id=resolution_run_id,
        )

        self._raise_if_stack_error(response.data, endpoint="/users/{id}/answers")
        self._remember_backoff(method_key, response.data)

        return response.data if isinstance(response.data, dict) else {"items": []}

    async def fetch_questions(
        self,
        user_id: str,
        *,
        pagesize: int = 20,
        resolution_run_id: str | None = None,
    ) -> dict[str, Any]:
        clean_user_id = self._clean_user_id(user_id)
        method_key = "users_questions"
        await self._respect_backoff(method_key)

        response = await self.request_json(
            "GET",
            f"/users/{quote(clean_user_id)}/questions",
            params={
                **self._params(),
                "pagesize": min(max(pagesize, 1), 100),
                "order": "desc",
                "sort": "votes",
            },
            resolution_run_id=resolution_run_id,
        )

        self._raise_if_stack_error(response.data, endpoint="/users/{id}/questions")
        self._remember_backoff(method_key, response.data)

        return response.data if isinstance(response.data, dict) else {"items": []}

    async def fetch_profile_bundle(
        self,
        user_id: str,
        *,
        resolution_run_id: str | None = None,
    ) -> dict[str, Any]:
        clean_user_id = self._clean_user_id(user_id)

        user = await self.fetch_user(
            clean_user_id,
            resolution_run_id=resolution_run_id,
        )
        answers = await self.fetch_answers(
            clean_user_id,
            pagesize=20,
            resolution_run_id=resolution_run_id,
        )
        questions = await self.fetch_questions(
            clean_user_id,
            pagesize=20,
            resolution_run_id=resolution_run_id,
        )

        return {
            "source": "stackoverflow",
            "user_id": clean_user_id,
            "user": user,
            "answers": answers,
            "questions": questions,
        }

    async def _respect_backoff(self, method_key: str) -> None:
        backoff_until = self._backoff_until_by_method.get(method_key)

        if backoff_until is None:
            return

        now = datetime.now(UTC)
        if now >= backoff_until:
            self._backoff_until_by_method.pop(method_key, None)
            return

        remaining_seconds = max(1, int((backoff_until - now).total_seconds()))

        raise PlatformRateLimitError(
            "Stack Exchange backoff is active for this method.",
            details={
                "source": "stackoverflow",
                "method": method_key,
                "retry_after_seconds": remaining_seconds,
            },
        )

    def _remember_backoff(self, method_key: str, data: Any) -> None:
        if not isinstance(data, dict):
            return

        backoff = data.get("backoff")
        if not isinstance(backoff, int) or backoff <= 0:
            return

        self._backoff_until_by_method[method_key] = datetime.now(UTC) + timedelta(
            seconds=backoff
        )

    def _params(self) -> dict[str, Any]:
        params: dict[str, Any] = {
            "site": "stackoverflow",
        }

        if self.api_key:
            params["key"] = self.api_key

        return params

    def _items(self, data: Any) -> list[Any]:
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            return data["items"]

        return []

    def _raise_if_stack_error(self, data: Any, *, endpoint: str) -> None:
        if not isinstance(data, dict):
            return

        error_id = data.get("error_id")
        error_name = str(data.get("error_name") or "")
        error_message = str(data.get("error_message") or "Stack Exchange API error.")

        if error_id is None:
            return

        details = {
            "source": "stackoverflow",
            "endpoint": endpoint,
            "error_id": error_id,
            "error_name": error_name,
        }

        if "throttle" in error_name.lower() or "quota" in error_name.lower():
            raise PlatformRateLimitError(error_message, details=details)

        raise PlatformAPIError(error_message, details=details)

    def _clean_user_id(self, user_id: str) -> str:
        cleaned = str(user_id).strip()

        if not cleaned:
            raise ValueError("Stack Overflow user_id must not be empty")

        if not cleaned.isdigit():
            raise ValueError("Stack Overflow user_id must be numeric")

        return cleaned
