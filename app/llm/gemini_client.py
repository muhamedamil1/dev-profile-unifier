from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GeminiTextResult:
    text: str
    model: str
    duration_ms: int
    input_tokens: int = 0
    output_tokens: int = 0
    raw_metadata: dict[str, Any] | None = None


class GeminiClientError(RuntimeError):
    pass


class GeminiClient:
    """Small Gemini wrapper isolated from service logic for testability."""

    def __init__(
        self,
        *,
        api_key: str | None,
        model_name: str = "gemini-1.5-flash",
        timeout_seconds: float = 30.0,
    ) -> None:
        self.api_key = (api_key or "").strip() or None
        self.model_name = model_name
        self.timeout_seconds = timeout_seconds

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def generate_text(self, *, prompt: str) -> GeminiTextResult:
        if not self.api_key:
            raise GeminiClientError("Gemini API key is not configured.")

        started = time.perf_counter()

        try:
            import google.generativeai as genai
        except Exception as exc:  # pragma: no cover - dependency import failure is environment specific
            raise GeminiClientError("google-generativeai package is unavailable.") from exc

        try:
            genai.configure(api_key=self.api_key)
            model = genai.GenerativeModel(self.model_name)
            response = model.generate_content(
                prompt,
                generation_config={
                    "temperature": 0.2,
                    "top_p": 0.9,
                    "max_output_tokens": 700,
                    "response_mime_type": "application/json",
                },
                request_options={"timeout": self.timeout_seconds},
            )
        except Exception as exc:  # pragma: no cover - live SDK behavior varies
            raise GeminiClientError("Gemini generation failed.") from exc

        duration_ms = int((time.perf_counter() - started) * 1000)
        text = getattr(response, "text", None) or ""

        usage = getattr(response, "usage_metadata", None)
        input_tokens = int(getattr(usage, "prompt_token_count", 0) or 0) if usage else estimate_tokens(prompt)
        output_tokens = int(getattr(usage, "candidates_token_count", 0) or 0) if usage else estimate_tokens(text)

        metadata: dict[str, Any] = {}
        if usage:
            metadata = {
                "prompt_token_count": getattr(usage, "prompt_token_count", None),
                "candidates_token_count": getattr(usage, "candidates_token_count", None),
                "total_token_count": getattr(usage, "total_token_count", None),
            }

        return GeminiTextResult(
            text=text,
            model=self.model_name,
            duration_ms=duration_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            raw_metadata=metadata,
        )


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, int(len(text.split()) * 1.3))


def estimate_gemini_cost_usd(*, input_tokens: int, output_tokens: int) -> float:
    """Free-tier friendly conservative estimate; keep numeric for metrics/reporting."""
    if input_tokens <= 0 and output_tokens <= 0:
        return 0.0
    # Gemini Flash pricing changes over time; this project uses the value only as an estimate.
    estimated_input_cost = (input_tokens / 1_000_000) * 0.075
    estimated_output_cost = (output_tokens / 1_000_000) * 0.30
    return round(estimated_input_cost + estimated_output_cost, 8)
