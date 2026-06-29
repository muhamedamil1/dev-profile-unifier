from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class SourceAPIMetrics(BaseModel):
    source: str
    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    avg_duration_ms: float | None = None
    last_called_at: datetime | None = None


class GitHubRateLimitMetrics(BaseModel):
    remaining: int | None = None
    total: int | None = None
    reset_at: datetime | None = None
    last_checked_at: datetime | None = None


class LLMMetrics(BaseModel):
    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0
    retry_count: int = 0
    rate_limit_wait_ms: int = 0


class ResolutionMetrics(BaseModel):
    profiles_resolved: int = 0
    resolution_runs: int = 0
    resolved_runs: int = 0
    partial_runs: int = 0
    failed_runs: int = 0
    average_resolution_time_ms: float | None = None


class HealthDashboardResponse(BaseModel):
    status: Literal["ok", "degraded"] = "ok"
    generated_at: datetime
    github_rate_limit: GitHubRateLimitMetrics = Field(default_factory=GitHubRateLimitMetrics)
    external_api_calls: list[SourceAPIMetrics] = Field(default_factory=list)
    llm_usage: LLMMetrics = Field(default_factory=LLMMetrics)
    resolution_metrics: ResolutionMetrics = Field(default_factory=ResolutionMetrics)
    raw_views: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
