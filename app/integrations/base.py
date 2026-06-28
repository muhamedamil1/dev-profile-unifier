from __future__ import annotations

import asyncio
import logging
import random
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta

try:
    from datetime import UTC
except ImportError:  # Python 3.10 compatibility.
    from datetime import timezone

    UTC = timezone.utc
from typing import Any
from urllib.parse import urlencode

import httpx

from app.schemas.enums import HttpMethod, MetricSource
from app.schemas.metrics import APICallMetric
from app.storage.metrics_repo import MetricsRepo
from app.utils.errors import (
    PlatformAPIError,
    PlatformNotFoundError,
    PlatformRateLimitError,
    PlatformTimeoutError,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RateLimitInfo:
    total: int | None = None
    remaining: int | None = None
    reset_at: datetime | None = None
    retry_after_seconds: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def to_metric_metadata(self) -> dict[str, Any]:
        metadata: dict[str, Any] = {}

        if self.retry_after_seconds is not None:
            metadata["retry_after_seconds"] = self.retry_after_seconds

        if self.raw:
            metadata["rate_limit_raw"] = self.raw

        return metadata


@dataclass(frozen=True)
class ExternalAPIResponse:
    source: MetricSource
    endpoint: str
    status_code: int
    data: Any
    headers: dict[str, str]
    duration_ms: int
    rate_limit: RateLimitInfo = field(default_factory=RateLimitInfo)


@dataclass(frozen=True)
class RetryConfig:
    max_attempts: int = 3
    base_delay_seconds: float = 0.5
    max_delay_seconds: float = 3.0
    max_retry_after_seconds: int = 3
    retryable_status_codes: frozenset[int] = frozenset({408, 429, 500, 502, 503, 504})


class AsyncWindowRateLimiter:
    """
    Lightweight in-process limiter.

    This is not a distributed rate limiter, but it prevents accidental bursts
    from a single API process. It is intentionally conservative and simple.
    """

    def __init__(self, *, max_calls: int, window_seconds: float) -> None:
        if max_calls <= 0:
            raise ValueError("max_calls must be positive")

        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")

        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self._calls: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = asyncio.get_running_loop().time()
            cutoff = now - self.window_seconds

            while self._calls and self._calls[0] <= cutoff:
                self._calls.popleft()

            if len(self._calls) >= self.max_calls:
                sleep_for = self.window_seconds - (now - self._calls[0])
                if sleep_for > 0:
                    await asyncio.sleep(sleep_for)

                now = asyncio.get_running_loop().time()
                cutoff = now - self.window_seconds

                while self._calls and self._calls[0] <= cutoff:
                    self._calls.popleft()

            self._calls.append(asyncio.get_running_loop().time())


class BaseExternalAPIClient:
    """
    Shared external API client foundation.

    Responsibilities:
    - Execute HTTP requests through httpx.
    - Apply small retry policy.
    - Parse JSON safely.
    - Record api_call_metrics for every attempt.
    - Raise typed platform errors.
    """

    source: MetricSource
    base_url: str

    def __init__(
        self,
        *,
        source: MetricSource,
        base_url: str,
        timeout_seconds: int,
        metrics_repo: MetricsRepo | None = None,
        default_headers: dict[str, str] | None = None,
        retry_config: RetryConfig | None = None,
        rate_limiter: AsyncWindowRateLimiter | None = None,
    ) -> None:
        self.source = source
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.metrics_repo = metrics_repo
        self.default_headers = {
            "User-Agent": "DevProfileUnifier/1.0",
            **(default_headers or {}),
        }
        self.retry_config = retry_config or RetryConfig()
        self.rate_limiter = rate_limiter

    async def request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        resolution_run_id: str | None = None,
    ) -> ExternalAPIResponse:
        method_upper = method.upper()
        endpoint = self._safe_endpoint(path, params)

        last_exc: Exception | None = None

        for attempt in range(1, self.retry_config.max_attempts + 1):
            if self.rate_limiter is not None:
                await self.rate_limiter.acquire()

            started = datetime.now(UTC)
            duration_ms = 0
            response: httpx.Response | None = None
            data: Any = None
            rate_limit = RateLimitInfo()

            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(self.timeout_seconds),
                    headers=self.default_headers,
                    follow_redirects=True,
                ) as client:
                    response = await client.request(
                        method_upper,
                        self._url(path),
                        params=params,
                        headers=headers,
                    )

                duration_ms = self._duration_ms(started)
                try:
                    data = self._parse_json_response(response)
                except PlatformAPIError as exc:
                    rate_limit = self.extract_rate_limit(response, None)

                    self._record_api_metric(
                        resolution_run_id=resolution_run_id,
                        endpoint=endpoint,
                        method=method_upper,
                        status_code=response.status_code,
                        duration_ms=duration_ms,
                        error_message="External API returned invalid JSON.",
                        rate_limit=rate_limit,
                    )

                    raise exc

                rate_limit = self.extract_rate_limit(response, data)

                error_message = None
                if response.status_code >= 400 or self.is_error_payload(data):
                    error_message = self.extract_error_message(data)

                self._record_api_metric(
                    resolution_run_id=resolution_run_id,
                    endpoint=endpoint,
                    method=method_upper,
                    status_code=response.status_code,
                    duration_ms=duration_ms,
                    error_message=error_message,
                    rate_limit=rate_limit,
                )

                if 200 <= response.status_code <= 299:
                    return ExternalAPIResponse(
                        source=self.source,
                        endpoint=endpoint,
                        status_code=response.status_code,
                        data=data,
                        headers=dict(response.headers),
                        duration_ms=duration_ms,
                        rate_limit=rate_limit,
                    )

                if self._should_retry_response(response, rate_limit, attempt):
                    await self._sleep_before_retry(attempt, rate_limit)
                    continue

                self._raise_response_error(
                    response=response,
                    data=data,
                    endpoint=endpoint,
                    rate_limit=rate_limit,
                )

            except httpx.TimeoutException as exc:
                duration_ms = self._duration_ms(started)
                last_exc = exc

                self._record_api_metric(
                    resolution_run_id=resolution_run_id,
                    endpoint=endpoint,
                    method=method_upper,
                    status_code=None,
                    duration_ms=duration_ms,
                    error_message="Request timed out.",
                    rate_limit=rate_limit,
                )

                if attempt < self.retry_config.max_attempts:
                    await self._sleep_before_retry(attempt, rate_limit)
                    continue

                raise PlatformTimeoutError(
                    f"{self.source.value} request timed out.",
                    details={
                        "source": self.source.value,
                        "endpoint": endpoint,
                        "attempts": attempt,
                    },
                ) from exc

            except httpx.TransportError as exc:
                duration_ms = self._duration_ms(started)
                last_exc = exc

                self._record_api_metric(
                    resolution_run_id=resolution_run_id,
                    endpoint=endpoint,
                    method=method_upper,
                    status_code=None,
                    duration_ms=duration_ms,
                    error_message="Network transport error.",
                    rate_limit=rate_limit,
                )

                if attempt < self.retry_config.max_attempts:
                    await self._sleep_before_retry(attempt, rate_limit)
                    continue

                raise PlatformAPIError(
                    f"{self.source.value} network request failed.",
                    details={
                        "source": self.source.value,
                        "endpoint": endpoint,
                        "attempts": attempt,
                    },
                ) from exc

        raise PlatformAPIError(
            f"{self.source.value} request failed.",
            details={
                "source": self.source.value,
                "endpoint": endpoint,
                "last_error": str(last_exc) if last_exc else None,
            },
        )

    def extract_rate_limit(
        self,
        response: httpx.Response,
        data: Any,
    ) -> RateLimitInfo:
        retry_after = self._parse_int(response.headers.get("retry-after"))

        return RateLimitInfo(
            retry_after_seconds=retry_after,
            raw={"retry_after": retry_after} if retry_after is not None else {},
        )

    def is_error_payload(self, data: Any) -> bool:
        return False

    def extract_error_message(self, data: Any) -> str:
        if isinstance(data, dict):
            for key in ("message", "error_message", "error", "detail"):
                value = data.get(key)
                if value:
                    return str(value)

        return "External API request failed."

    def _url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path

        return f"{self.base_url}/{path.lstrip('/')}"

    def _safe_endpoint(self, path: str, params: dict[str, Any] | None) -> str:
        if not params:
            return path

        sensitive_keys = {
            "key",
            "token",
            "access_token",
            "client_secret",
            "api_key",
        }

        safe_params = {
            key: value
            for key, value in params.items()
            if key.lower() not in sensitive_keys and value is not None
        }

        if not safe_params:
            return path

        return f"{path}?{urlencode(safe_params, doseq=True)}"

    def _parse_json_response(self, response: httpx.Response) -> Any:
        if response.status_code == 204:
            return None

        try:
            return response.json()
        except ValueError as exc:
            raise PlatformAPIError(
                f"{self.source.value} returned invalid JSON.",
                details={
                    "source": self.source.value,
                    "status_code": response.status_code,
                },
            ) from exc

    def _should_retry_response(
        self,
        response: httpx.Response,
        rate_limit: RateLimitInfo,
        attempt: int,
    ) -> bool:
        if attempt >= self.retry_config.max_attempts:
            return False

        if response.status_code not in self.retry_config.retryable_status_codes:
            return False

        if rate_limit.remaining == 0 and rate_limit.reset_at is not None:
            return False

        if response.status_code == 429 and rate_limit.retry_after_seconds is not None:
            return rate_limit.retry_after_seconds <= self.retry_config.max_retry_after_seconds

        return True

    async def _sleep_before_retry(
        self,
        attempt: int,
        rate_limit: RateLimitInfo,
    ) -> None:
        if rate_limit.retry_after_seconds is not None:
            sleep_for = min(
                rate_limit.retry_after_seconds,
                self.retry_config.max_delay_seconds,
            )
        else:
            exponential = self.retry_config.base_delay_seconds * (2 ** (attempt - 1))
            jitter = random.uniform(0, 0.25)
            sleep_for = min(exponential + jitter, self.retry_config.max_delay_seconds)

        if sleep_for > 0:
            await asyncio.sleep(sleep_for)

    def _raise_response_error(
        self,
        *,
        response: httpx.Response,
        data: Any,
        endpoint: str,
        rate_limit: RateLimitInfo,
    ) -> None:
        details = {
            "source": self.source.value,
            "endpoint": endpoint,
            "status_code": response.status_code,
            "rate_limit_remaining": rate_limit.remaining,
            "rate_limit_total": rate_limit.total,
            "rate_limit_reset_at": rate_limit.reset_at.isoformat()
            if rate_limit.reset_at
            else None,
            "retry_after_seconds": rate_limit.retry_after_seconds,
        }

        if response.status_code == 404:
            raise PlatformNotFoundError(
                f"{self.source.value} resource not found.",
                details=details,
            )

        if response.status_code == 429:
            raise PlatformRateLimitError(
                f"{self.source.value} rate limit exceeded.",
                details=details,
            )

        if response.status_code == 403 and self._looks_rate_limited(data, rate_limit):
            raise PlatformRateLimitError(
                f"{self.source.value} rate limit exceeded.",
                details=details,
            )

        raise PlatformAPIError(
            self.extract_error_message(data),
            details=details,
        )

    def _looks_rate_limited(self, data: Any, rate_limit: RateLimitInfo) -> bool:
        if rate_limit.remaining == 0:
            return True

        message = self.extract_error_message(data).lower()
        return "rate limit" in message or "throttle" in message

    def _record_api_metric(
        self,
        *,
        resolution_run_id: str | None,
        endpoint: str,
        method: str,
        status_code: int | None,
        duration_ms: int | None,
        error_message: str | None,
        rate_limit: RateLimitInfo,
    ) -> None:
        if self.metrics_repo is None:
            return

        metadata = rate_limit.to_metric_metadata()

        try:
            self.metrics_repo.record_api_call(
                APICallMetric(
                    resolution_run_id=resolution_run_id,
                    source=self.source,
                    endpoint=endpoint,
                    http_method=HttpMethod(method),
                    status_code=status_code,
                    duration_ms=duration_ms,
                    error_message=error_message,
                    rate_limit_remaining=rate_limit.remaining,
                    rate_limit_total=rate_limit.total,
                    rate_limit_reset_at=rate_limit.reset_at,
                    metadata=metadata,
                )
            )
        except Exception:
            logger.exception(
                "Failed to record API metric.",
                extra={
                    "source": self.source.value,
                    "endpoint": endpoint,
                },
            )

    def _duration_ms(self, started: datetime) -> int:
        delta = datetime.now(UTC) - started
        return max(0, int(delta.total_seconds() * 1000))

    def _parse_int(self, value: str | None) -> int | None:
        if value is None:
            return None

        try:
            return int(value)
        except ValueError:
            return None

    def _epoch_to_datetime(self, value: str | None) -> datetime | None:
        parsed = self._parse_int(value)
        if parsed is None:
            return None

        return datetime.fromtimestamp(parsed, tz=UTC)

    def _retry_after_to_datetime(self, seconds: int | None) -> datetime | None:
        if seconds is None:
            return None

        return datetime.now(UTC) + timedelta(seconds=seconds)