from __future__ import annotations

from typing import Any

from app.llm.gemini_client import GeminiClientError, GeminiTextResult


def metadata_from_llm_result(result: GeminiTextResult | None) -> dict[str, Any]:
    """Normalize Gemini success metadata for api_call_metrics.metadata."""

    if result is None:
        return {
            "retry_count": 0,
            "rate_limit_wait_ms": 0,
            "llm_raw_metadata": {},
        }

    return {
        "retry_count": int(getattr(result, "retry_count", 0) or 0),
        "rate_limit_wait_ms": int(getattr(result, "rate_limit_wait_ms", 0) or 0),
        "llm_raw_metadata": getattr(result, "raw_metadata", {}) or {},
    }


def metadata_from_llm_error(error: BaseException | None) -> dict[str, Any]:
    """Normalize Gemini error metadata for fallback metrics.

    Keep details safe and compact. Do not persist full exception strings because
    SDK errors can include environment-specific details.
    """

    if not isinstance(error, GeminiClientError):
        return {
            "retry_count": 0,
            "rate_limit_wait_ms": 0,
            "llm_error_type": error.__class__.__name__ if error else None,
            "llm_raw_metadata": {},
        }

    return {
        "retry_count": int(error.retry_count or 0),
        "rate_limit_wait_ms": int(error.rate_limit_wait_ms or 0),
        "llm_error_type": error.error_type,
        "llm_error_status_code": error.status_code,
        "llm_error_retryable": error.retryable,
        "llm_raw_metadata": error.metadata or {},
    }


def merge_llm_metric_metadata(
    base: dict[str, Any] | None = None,
    *,
    result: GeminiTextResult | None = None,
    error: BaseException | None = None,
) -> dict[str, Any]:
    """Merge service-specific metric metadata with normalized LLM metadata."""

    merged = dict(base or {})
    if result is not None:
        merged.update(metadata_from_llm_result(result))
    elif error is not None:
        merged.update(metadata_from_llm_error(error))
    else:
        merged.update(metadata_from_llm_result(None))
    return merged
