from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.enums import HttpMethod, MetricSource


class RateLimitState(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    remaining: int | None = Field(default=None, ge=0)
    limit: int | None = Field(default=None, ge=0)
    reset_at: datetime | None = None
    observed_at: datetime | None = None

    @model_validator(mode="after")
    def validate_limit_pair(self) -> RateLimitState:
        if self.remaining is not None and self.limit is not None:
            if self.remaining > self.limit:
                raise ValueError("rate limit remaining cannot be greater than limit")

        return self


class APICallMetric(BaseModel):
    """
    Represents one attempted external API call.

    The model supports failures/timeouts where status_code may be null but
    error_message is present.
    """

    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    id: UUID | None = None
    resolution_run_id: UUID | None = None

    source: MetricSource
    endpoint: str = Field(..., min_length=1, max_length=2000)
    http_method: HttpMethod = HttpMethod.GET

    status_code: int | None = Field(default=None, ge=100, le=599)
    duration_ms: int | None = Field(default=None, ge=0)

    error_message: str | None = None

    rate_limit_remaining: int | None = Field(default=None, ge=0)
    rate_limit_total: int | None = Field(default=None, ge=0)
    rate_limit_reset_at: datetime | None = None

    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("error_message", mode="before")
    @classmethod
    def clean_error_message(cls, value: Any) -> str | None:
        if value is None:
            return None

        cleaned = str(value).strip()
        return cleaned or None

    @model_validator(mode="after")
    def validate_rate_limit_values(self) -> APICallMetric:
        if self.rate_limit_remaining is not None and self.rate_limit_total is not None:
            if self.rate_limit_remaining > self.rate_limit_total:
                raise ValueError("rate_limit_remaining cannot exceed rate_limit_total")

        return self

    @property
    def failed(self) -> bool:
        if self.error_message:
            return True

        if self.status_code is None:
            return True

        return self.status_code >= 400

    def to_db_payload(self) -> dict[str, Any]:
        data = self.model_dump(mode="json", exclude_none=True)
        data.pop("id", None)
        return data


class LLMUsageMetric(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    profile_id: UUID | None = None

    model: str = Field(default="gemini-2.5-flash", min_length=1, max_length=120)
    prompt_version: str = Field(default="v1", min_length=1, max_length=40)

    prompt_text: str = Field(..., min_length=1)
    summary: str = Field(..., min_length=1)

    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)

    estimated_cost_usd: float = Field(default=0.0, ge=0)

    def to_db_payload(self, profile_id: UUID) -> dict[str, Any]:
        data = self.model_dump(mode="json", exclude_none=True)
        data["profile_id"] = str(profile_id)
        return data


class SourceHealthMetric(BaseModel):
    total: int = Field(default=0, ge=0)
    errors: int = Field(default=0, ge=0)
    average_duration_ms: int = Field(default=0, ge=0)


class ProfileHealthMetrics(BaseModel):
    resolved_total: int = Field(default=0, ge=0)
    partial_total: int = Field(default=0, ge=0)
    failed_total: int = Field(default=0, ge=0)
    average_resolution_time_ms: int = Field(default=0, ge=0)


class LLMHealthMetrics(BaseModel):
    model: str = "gemini-2.5-flash"
    summaries_generated: int = Field(default=0, ge=0)
    total_input_tokens: int = Field(default=0, ge=0)
    total_output_tokens: int = Field(default=0, ge=0)
    estimated_cost_usd: float = Field(default=0.0, ge=0)