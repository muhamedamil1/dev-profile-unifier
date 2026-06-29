from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo


class GeminiRateLimitError(RuntimeError):
    """Raised when a local Gemini limit should fail fast instead of sleeping."""


@dataclass(frozen=True)
class GeminiRateLimitConfig:
    """Local defensive limits for Gemini API usage.

    Google evaluates Gemini API usage across RPM, input TPM, and RPD. Active
    project/model limits can vary in AI Studio, so these defaults are
    intentionally conservative for a free-tier student/internship project.
    """

    enabled: bool = True
    requests_per_minute: int = 5
    tokens_per_minute: int = 30_000
    requests_per_day: int = 500
    min_request_interval_seconds: float = 12.0
    max_wait_seconds: float = 60.0
    day_reset_timezone: str = "America/Los_Angeles"

    @classmethod
    def from_settings(cls, settings: object | None) -> "GeminiRateLimitConfig":
        if settings is None:
            return cls()

        return cls(
            enabled=_bool_setting(
                _setting(
                    settings,
                    "gemini_rate_limit_enabled",
                    _setting(settings, "GEMINI_RATE_LIMIT_ENABLED", True),
                ),
                default=True,
            ),
            requests_per_minute=_int_setting(
                _setting(
                    settings,
                    "gemini_requests_per_minute",
                    _setting(settings, "GEMINI_REQUESTS_PER_MINUTE", 5),
                ),
                default=5,
                minimum=0,
            ),
            tokens_per_minute=_int_setting(
                _setting(
                    settings,
                    "gemini_tokens_per_minute",
                    _setting(settings, "GEMINI_TOKENS_PER_MINUTE", 30_000),
                ),
                default=30_000,
                minimum=0,
            ),
            requests_per_day=_int_setting(
                _setting(
                    settings,
                    "gemini_requests_per_day",
                    _setting(settings, "GEMINI_REQUESTS_PER_DAY", 500),
                ),
                default=500,
                minimum=0,
            ),
            min_request_interval_seconds=_float_setting(
                _setting(
                    settings,
                    "gemini_min_request_interval_seconds",
                    _setting(settings, "GEMINI_MIN_REQUEST_INTERVAL_SECONDS", 12.0),
                ),
                default=12.0,
                minimum=0.0,
            ),
            max_wait_seconds=_float_setting(
                _setting(
                    settings,
                    "gemini_rate_limit_max_wait_seconds",
                    _setting(settings, "GEMINI_RATE_LIMIT_MAX_WAIT_SECONDS", 60.0),
                ),
                default=60.0,
                minimum=0.0,
            ),
            day_reset_timezone=str(
                _setting(
                    settings,
                    "gemini_day_reset_timezone",
                    _setting(settings, "GEMINI_DAY_RESET_TIMEZONE", "America/Los_Angeles"),
                )
                or "America/Los_Angeles"
            ),
        )


@dataclass(frozen=True)
class GeminiRateLimitDecision:
    waited_ms: int = 0
    wait_reason: str | None = None
    rpm_window_count: int = 0
    tpm_window_tokens: int = 0
    daily_request_count: int = 0
    estimated_input_tokens: int = 0
    metadata: dict[str, int | float | str | bool | None] = field(default_factory=dict)


class GeminiRateLimiter:
    """Thread-safe, in-process Gemini request limiter.

    This protects one running FastAPI process. It is intentionally local and
    dependency-free. On multi-instance deployments, each process has its own
    limiter; the Gemini API remains the final quota authority.
    """

    def __init__(
        self,
        config: GeminiRateLimitConfig | None = None,
        *,
        sleep_func: Callable[[float], None] | None = None,
        monotonic_func: Callable[[], float] | None = None,
        now_func: Callable[[], datetime] | None = None,
    ) -> None:
        self.config = config or GeminiRateLimitConfig()
        self._sleep = sleep_func or time.sleep
        self._monotonic = monotonic_func or time.monotonic
        self._now = now_func
        self._lock = threading.RLock()
        self._request_timestamps: deque[float] = deque()
        self._token_events: deque[tuple[float, int]] = deque()
        self._last_request_at: float | None = None
        self._daily_date: str | None = None
        self._daily_count: int = 0

    def acquire(self, *, estimated_input_tokens: int = 0) -> GeminiRateLimitDecision:
        """Wait until a request is locally allowed, or raise for hard caps."""

        estimated_input_tokens = max(0, int(estimated_input_tokens or 0))

        if not self.config.enabled:
            return GeminiRateLimitDecision(
                estimated_input_tokens=estimated_input_tokens,
                metadata={
                    "gemini_rate_limit_enabled": False,
                    "gemini_rate_limit_wait_ms": 0,
                    "gemini_rate_limit_reason": None,
                },
            )

        total_wait_seconds = 0.0
        reasons: list[str] = []

        while True:
            with self._lock:
                now = self._monotonic()
                self._prune(now)
                self._reset_daily_if_needed_locked()

                if self.config.requests_per_day > 0 and self._daily_count >= self.config.requests_per_day:
                    raise GeminiRateLimitError(
                        "Local Gemini requests-per-day budget has been exhausted; retry after the Pacific-time daily reset."
                    )

                wait_seconds, reason = self._required_wait_seconds_locked(
                    now=now,
                    estimated_input_tokens=estimated_input_tokens,
                )

                if wait_seconds <= 0:
                    self._request_timestamps.append(now)
                    self._token_events.append((now, estimated_input_tokens))
                    self._last_request_at = now
                    self._daily_count += 1
                    window_tokens = sum(tokens for _, tokens in self._token_events)
                    unique_reasons = "+".join(sorted(set(reasons))) if reasons else None
                    metadata = {
                        "gemini_rate_limit_enabled": True,
                        "gemini_rate_limit_wait_ms": int(total_wait_seconds * 1000),
                        "gemini_rate_limit_reason": unique_reasons,
                        "gemini_rate_limit_rpm_window_count": len(self._request_timestamps),
                        "gemini_rate_limit_tpm_window_tokens": window_tokens,
                        "gemini_rate_limit_daily_count": self._daily_count,
                        "gemini_rate_limit_requests_per_minute": self.config.requests_per_minute,
                        "gemini_rate_limit_tokens_per_minute": self.config.tokens_per_minute,
                        "gemini_rate_limit_requests_per_day": self.config.requests_per_day,
                    }
                    return GeminiRateLimitDecision(
                        waited_ms=int(total_wait_seconds * 1000),
                        wait_reason=unique_reasons,
                        rpm_window_count=len(self._request_timestamps),
                        tpm_window_tokens=window_tokens,
                        daily_request_count=self._daily_count,
                        estimated_input_tokens=estimated_input_tokens,
                        metadata=metadata,
                    )

            if reason:
                reasons.append(reason)

            wait_seconds = max(0.0, wait_seconds)
            if total_wait_seconds + wait_seconds > self.config.max_wait_seconds:
                raise GeminiRateLimitError(
                    f"Local Gemini rate limiter would wait {total_wait_seconds + wait_seconds:.2f}s, "
                    f"exceeding max_wait_seconds={self.config.max_wait_seconds:.2f}."
                )

            self._sleep(wait_seconds)
            total_wait_seconds += wait_seconds

    def snapshot(self) -> dict[str, int | float | str | bool | None]:
        with self._lock:
            now = self._monotonic()
            self._prune(now)
            self._reset_daily_if_needed_locked()
            return {
                "enabled": self.config.enabled,
                "rpm_window_count": len(self._request_timestamps),
                "tpm_window_tokens": sum(tokens for _, tokens in self._token_events),
                "daily_request_count": self._daily_count,
                "daily_date": self._daily_date,
                "requests_per_minute": self.config.requests_per_minute,
                "tokens_per_minute": self.config.tokens_per_minute,
                "requests_per_day": self.config.requests_per_day,
            }

    def reset(self) -> None:
        with self._lock:
            self._request_timestamps.clear()
            self._token_events.clear()
            self._last_request_at = None
            self._daily_date = None
            self._daily_count = 0

    def _required_wait_seconds_locked(self, *, now: float, estimated_input_tokens: int) -> tuple[float, str | None]:
        waits: list[tuple[float, str]] = []

        if self.config.min_request_interval_seconds > 0 and self._last_request_at is not None:
            elapsed = now - self._last_request_at
            remaining = self.config.min_request_interval_seconds - elapsed
            if remaining > 0:
                waits.append((remaining, "min_interval"))

        if self.config.requests_per_minute > 0 and len(self._request_timestamps) >= self.config.requests_per_minute:
            oldest = self._request_timestamps[0]
            waits.append((max(0.0, 60.0 - (now - oldest)), "rpm"))

        if self.config.tokens_per_minute > 0:
            current_tokens = sum(tokens for _, tokens in self._token_events)
            if estimated_input_tokens > self.config.tokens_per_minute and not self._token_events:
                raise GeminiRateLimitError(
                    "Single Gemini request exceeds configured local tokens-per-minute budget."
                )
            if current_tokens + estimated_input_tokens > self.config.tokens_per_minute:
                if self._token_events:
                    oldest_token_event = self._token_events[0][0]
                    waits.append((max(0.0, 60.0 - (now - oldest_token_event)), "tpm"))
                else:
                    raise GeminiRateLimitError(
                        "Single Gemini request exceeds configured local tokens-per-minute budget."
                    )

        if not waits:
            return 0.0, None

        wait_seconds, reason = max(waits, key=lambda item: item[0])
        return wait_seconds, reason

    def _prune(self, now: float) -> None:
        while self._request_timestamps and now - self._request_timestamps[0] >= 60.0:
            self._request_timestamps.popleft()
        while self._token_events and now - self._token_events[0][0] >= 60.0:
            self._token_events.popleft()

    def _reset_daily_if_needed_locked(self) -> None:
        today = self._current_reset_date()
        if self._daily_date != today:
            self._daily_date = today
            self._daily_count = 0

    def _current_reset_date(self) -> str:
        if self._now is not None:
            current = self._now()
        else:
            try:
                current = datetime.now(ZoneInfo(self.config.day_reset_timezone))
            except Exception:
                current = datetime.utcnow()
        return current.date().isoformat()


def _setting(settings: object, key: str, default: object = None) -> object:
    if isinstance(settings, dict):
        return settings.get(key, default)
    return getattr(settings, key, default)


def _bool_setting(value: object, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off", ""}:
            return False
        return default
    return bool(value)


def _int_setting(value: object, *, default: int, minimum: int | None = None) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        parsed = default
    if minimum is not None:
        parsed = max(minimum, parsed)
    return parsed


def _float_setting(value: object, *, default: float, minimum: float | None = None) -> float:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        parsed = default
    if minimum is not None:
        parsed = max(minimum, parsed)
    return parsed
