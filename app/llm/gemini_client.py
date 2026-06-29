from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from typing import Any, Callable

from app.llm.rate_limiter import GeminiRateLimitDecision, GeminiRateLimitError, GeminiRateLimiter


@dataclass(frozen=True)
class GeminiRetryConfig:
    max_retries: int = 2
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 8.0
    jitter_ratio: float = 0.2

    @classmethod
    def from_settings(cls, settings: object | None) -> "GeminiRetryConfig":
        if settings is None:
            return cls()
        return cls(
            max_retries=_int_setting(
                _setting(settings, "gemini_max_retries", _setting(settings, "GEMINI_MAX_RETRIES", 2)),
                default=2,
                minimum=0,
            ),
            base_delay_seconds=_float_setting(
                _setting(
                    settings,
                    "gemini_retry_base_delay_seconds",
                    _setting(settings, "GEMINI_RETRY_BASE_DELAY_SECONDS", 1.0),
                ),
                default=1.0,
                minimum=0.0,
            ),
            max_delay_seconds=_float_setting(
                _setting(
                    settings,
                    "gemini_retry_max_delay_seconds",
                    _setting(settings, "GEMINI_RETRY_MAX_DELAY_SECONDS", 8.0),
                ),
                default=8.0,
                minimum=0.0,
            ),
            jitter_ratio=_float_setting(
                _setting(
                    settings,
                    "gemini_retry_jitter_ratio",
                    _setting(settings, "GEMINI_RETRY_JITTER_RATIO", 0.2),
                ),
                default=0.2,
                minimum=0.0,
            ),
        )


@dataclass(frozen=True)
class GeminiTextResult:
    text: str
    model: str
    duration_ms: int
    input_tokens: int = 0
    output_tokens: int = 0
    retry_count: int = 0
    rate_limit_wait_ms: int = 0
    raw_metadata: dict[str, Any] = field(default_factory=dict)


class GeminiClientError(RuntimeError):
    """Safe Gemini client exception for service-layer fallback handling."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        status_code: int | None = None,
        error_type: str | None = None,
        retry_count: int = 0,
        rate_limit_wait_ms: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code
        self.error_type = error_type or self.__class__.__name__
        self.retry_count = retry_count
        self.rate_limit_wait_ms = rate_limit_wait_ms
        self.metadata = metadata or {}


class GeminiClient:
    """Production-safe Gemini wrapper isolated from service logic.

    Includes local free-tier-friendly rate limiting, transient retry/backoff,
    Retry-After support, safe error classification, and testable transport
    injection.
    """

    def __init__(
        self,
        *,
        api_key: str | None,
        model_name: str = "gemini-2.5-flash-lite",
        timeout_seconds: float = 30.0,
        retry_config: GeminiRetryConfig | None = None,
        rate_limiter: GeminiRateLimiter | None = None,
        sleep_func: Callable[[float], None] | None = None,
        random_func: Callable[[], float] | None = None,
        generate_content_callable: Callable[[str], Any] | None = None,
    ) -> None:
        if hasattr(api_key, "get_secret_value"):
            api_key = api_key.get_secret_value()
        self.api_key = (api_key or "").strip() or None
        self.model_name = model_name
        self.timeout_seconds = max(0.1, float(timeout_seconds or 30.0))
        self.retry_config = retry_config or GeminiRetryConfig()
        self.rate_limiter = rate_limiter
        self._sleep = sleep_func or time.sleep
        self._random = random_func or random.random
        self._generate_content_callable = generate_content_callable

    @property
    def available(self) -> bool:
        return bool(self.api_key) or self._generate_content_callable is not None

    def generate_text(self, *, prompt: str) -> GeminiTextResult:
        if not self.available:
            raise GeminiClientError(
                "Gemini API key is not configured.",
                retryable=False,
                status_code=401,
                error_type="missing_api_key",
            )

        started = time.perf_counter()
        estimated_input_tokens = estimate_tokens(prompt)
        total_rate_limit_wait_ms = 0
        total_retry_after_wait_ms = 0
        latest_rate_decision: GeminiRateLimitDecision | None = None
        retry_count = 0
        retry_after_seconds_seen: list[float] = []
        backoff_seconds_seen: list[float] = []
        last_error: GeminiClientError | None = None

        max_attempts = max(1, self.retry_config.max_retries + 1)
        for attempt in range(max_attempts):
            try:
                if self.rate_limiter is not None:
                    latest_rate_decision = self.rate_limiter.acquire(
                        estimated_input_tokens=estimated_input_tokens,
                    )
                    total_rate_limit_wait_ms += latest_rate_decision.waited_ms

                response = self._generate_content(prompt)
                duration_ms = int((time.perf_counter() - started) * 1000)
                text, input_tokens, output_tokens, usage_metadata = self._extract_response(response, prompt)
                metadata: dict[str, Any] = {
                    "retry_count": retry_count,
                    "rate_limit_wait_ms": total_rate_limit_wait_ms,
                    "retry_after_wait_ms": total_retry_after_wait_ms,
                    "rate_limit": latest_rate_decision.metadata if latest_rate_decision else {},
                    "usage": usage_metadata,
                    "retry_after_seconds_seen": retry_after_seconds_seen,
                    "backoff_seconds_seen": backoff_seconds_seen,
                }

                return GeminiTextResult(
                    text=text,
                    model=self.model_name,
                    duration_ms=duration_ms,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    retry_count=retry_count,
                    rate_limit_wait_ms=total_rate_limit_wait_ms,
                    raw_metadata=metadata,
                )

            except GeminiRateLimitError as exc:
                raise GeminiClientError(
                    "Local Gemini rate limit blocked the request.",
                    retryable=False,
                    status_code=429,
                    error_type="local_rate_limit_exceeded",
                    retry_count=retry_count,
                    rate_limit_wait_ms=total_rate_limit_wait_ms,
                    metadata={
                        "detail": str(exc),
                        "estimated_input_tokens": estimated_input_tokens,
                    },
                ) from exc
            except Exception as exc:
                classified = classify_gemini_exception(
                    exc,
                    retry_count=retry_count,
                    rate_limit_wait_ms=total_rate_limit_wait_ms,
                )
                last_error = classified

                if not classified.retryable or attempt >= max_attempts - 1:
                    raise classified from exc

                retry_after_seconds = _retry_after_seconds_from_exception(exc)
                backoff_seconds = self._backoff_seconds(attempt)
                if retry_after_seconds is not None:
                    retry_after_seconds_seen.append(retry_after_seconds)
                    backoff_seconds = max(backoff_seconds, retry_after_seconds)

                backoff_seconds_seen.append(backoff_seconds)
                self._sleep(backoff_seconds)
                total_retry_after_wait_ms += int(max(0.0, backoff_seconds) * 1000)
                retry_count += 1

        # Defensive fallback; the loop should always return or raise.
        raise last_error or GeminiClientError("Gemini generation failed.", retryable=False)

    def _generate_content(self, prompt: str) -> Any:
        if self._generate_content_callable is not None:
            return self._generate_content_callable(prompt)

        try:
            import google.generativeai as genai
        except Exception as exc:  # pragma: no cover - dependency import failure is environment specific
            raise GeminiClientError(
                "google-generativeai package is unavailable.",
                retryable=False,
                status_code=500,
                error_type="sdk_unavailable",
            ) from exc

        genai.configure(api_key=self.api_key)
        model = genai.GenerativeModel(self.model_name)
        return model.generate_content(
            prompt,
            generation_config={
                "temperature": 0.2,
                "top_p": 0.9,
                "max_output_tokens": 700,
                "response_mime_type": "application/json",
            },
            request_options={"timeout": self.timeout_seconds},
        )

    def _extract_response(self, response: Any, prompt: str) -> tuple[str, int, int, dict[str, Any]]:
        text = getattr(response, "text", None) or ""
        usage = getattr(response, "usage_metadata", None)

        input_tokens = int(getattr(usage, "prompt_token_count", 0) or 0) if usage else estimate_tokens(prompt)
        output_tokens = int(getattr(usage, "candidates_token_count", 0) or 0) if usage else estimate_tokens(text)

        usage_metadata: dict[str, Any] = {}
        if usage:
            usage_metadata = {
                "prompt_token_count": getattr(usage, "prompt_token_count", None),
                "candidates_token_count": getattr(usage, "candidates_token_count", None),
                "total_token_count": getattr(usage, "total_token_count", None),
            }

        return text, input_tokens, output_tokens, usage_metadata

    def _backoff_seconds(self, attempt: int) -> float:
        base = self.retry_config.base_delay_seconds * (2 ** max(0, attempt))
        capped = min(self.retry_config.max_delay_seconds, base)
        jitter_ratio = max(0.0, self.retry_config.jitter_ratio)
        jitter = capped * jitter_ratio * self._random()
        return capped + jitter


def classify_gemini_exception(
    exc: Exception,
    *,
    retry_count: int = 0,
    rate_limit_wait_ms: int = 0,
) -> GeminiClientError:
    if isinstance(exc, GeminiClientError):
        return exc

    status_code = _status_code_from_exception(exc)
    message = str(exc) or exc.__class__.__name__
    lowered = message.lower()
    exc_name = exc.__class__.__name__.lower()

    non_retryable_markers = (
        "api key not valid",
        "invalid api key",
        "permission denied",
        "unauthenticated",
        "invalid argument",
        "bad request",
        "forbidden",
        "safety",
        "blocked",
    )
    retryable_markers = (
        "resource_exhausted",
        "rate limit",
        "quota",
        "too many requests",
        "temporarily unavailable",
        "unavailable",
        "deadline exceeded",
        "timeout",
        "timed out",
        "internal",
        "server error",
    )

    if status_code in {400, 401, 403, 404} or any(marker in lowered for marker in non_retryable_markers):
        return GeminiClientError(
            "Gemini generation failed with a non-retryable error.",
            retryable=False,
            status_code=status_code,
            error_type="non_retryable_gemini_error",
            retry_count=retry_count,
            rate_limit_wait_ms=rate_limit_wait_ms,
            metadata={"exception_type": exc.__class__.__name__},
        )

    retryable = status_code in {429, 500, 502, 503, 504} or any(marker in lowered for marker in retryable_markers)
    if "timeout" in exc_name:
        retryable = True

    return GeminiClientError(
        "Gemini generation failed." if not retryable else "Gemini transient generation failure.",
        retryable=retryable,
        status_code=status_code,
        error_type="retryable_gemini_error" if retryable else "gemini_generation_failed",
        retry_count=retry_count,
        rate_limit_wait_ms=rate_limit_wait_ms,
        metadata={"exception_type": exc.__class__.__name__},
    )


def _status_code_from_exception(exc: Exception) -> int | None:
    for attr in ("status_code", "code"):
        value = getattr(exc, attr, None)
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            pass

    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    try:
        if value is not None:
            return int(value)
    except (TypeError, ValueError):
        pass
    return None


def _retry_after_seconds_from_exception(exc: Exception) -> float | None:
    headers = None
    response = getattr(exc, "response", None)
    if response is not None:
        headers = getattr(response, "headers", None)

    if headers is None:
        headers = getattr(exc, "headers", None)

    if not headers:
        return None

    value = None
    for key in ("Retry-After", "retry-after"):
        try:
            value = headers.get(key)
        except AttributeError:
            value = headers.get(key) if isinstance(headers, dict) else None
        if value:
            break

    if value is None:
        return None

    try:
        seconds = float(value)
        return max(0.0, seconds)
    except (TypeError, ValueError):
        pass

    try:
        parsed = parsedate_to_datetime(str(value))
        return max(0.0, parsed.timestamp() - time.time())
    except Exception:
        return None


def estimate_tokens(text: str) -> int:
    """Conservative local estimate used only for defensive rate limiting.

    It deliberately overestimates compared with a naive word count because Gemini
    prompts often contain JSON, URLs, code-like strings, and punctuation-heavy
    evidence packets. Live usage metadata from Gemini remains the better source
    after a successful call.
    """

    if not text:
        return 0

    by_words = int(len(text.split()) * 1.6)
    by_chars = int(len(text) / 4)
    return max(1, by_words, by_chars)


def estimate_gemini_cost_usd(*, input_tokens: int, output_tokens: int) -> float:
    """Return zero for this free-tier-oriented project.

    Token counts are still stored for observability. Avoid using stale hard-coded
    paid-tier prices in a student take-home project because public Gemini prices
    and free-tier behavior can change.
    """

    return 0.0


def _setting(settings: object, key: str, default: object = None) -> object:
    if isinstance(settings, dict):
        return settings.get(key, default)
    return getattr(settings, key, default)


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
