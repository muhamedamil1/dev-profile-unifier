from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.utils.errors import SettingsError


class Settings(BaseSettings):
    """
    Central application settings.

    Development mode is intentionally tolerant of missing external credentials so the
    API can boot while the project is being assembled. Production mode is strict and
    refuses to start unless required secrets are configured.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # App metadata
    app_name: str = "Dev Profile Unifier"
    app_version: str = "1.0.0"
    app_env: Literal["development", "test", "production"] = "development"
    log_level: Literal["debug", "info", "warning", "error", "critical"] = "info"

    # Runtime behavior
    request_timeout_seconds: int = Field(default=20, ge=3, le=120)
    max_candidates_per_platform: int = Field(default=3, ge=1, le=10)
    enable_llm_ambiguous_review: bool = False

    # Resolution thresholds
    auto_match_threshold: float = Field(default=0.85, ge=0.0, le=1.0)
    needs_review_threshold: float = Field(default=0.60, ge=0.0, le=1.0)
    evidence_confidence_cap: float = Field(default=0.97, ge=0.0, le=1.0)

    # CORS
    backend_cors_origins: str = ""

    # Supabase
    supabase_url: str = ""
    supabase_service_role_key: SecretStr = SecretStr("")
    supabase_anon_key: SecretStr = SecretStr("")

    # External APIs
    github_token: SecretStr = SecretStr("")
    stackexchange_key: SecretStr = SecretStr("")
    gemini_api_key: SecretStr = SecretStr("")

    ENABLE_LLM_AMBIGUITY_REVIEW: bool = False
    LLM_REVIEW_MIN_SCORE: float = 0.55
    LLM_REVIEW_MAX_SCORE: float = 0.84
    LLM_REVIEW_PROMOTION_MIN_SCORE: float = 0.72
    LLM_REVIEW_PROMPT_VERSION: str = "identity_match_review_v1_2026_06_hardened"

    #LLM client call
    
    gemini_model: str = "gemini-2.5-flash-lite"
    gemini_timeout_seconds: float = 30.0
    
    gemini_max_retries: int = 2
    gemini_retry_base_delay_seconds: float = 1.0
    gemini_retry_max_delay_seconds: float = 8.0
    gemini_retry_jitter_ratio: float = 0.2
    
    gemini_rate_limit_enabled: bool = True
    gemini_requests_per_minute: int = 5
    gemini_tokens_per_minute: int = 30_000
    gemini_requests_per_day: int = 500
    gemini_min_request_interval_seconds: float = 12.0
    gemini_rate_limit_max_wait_seconds: float = 60.0
    gemini_day_reset_timezone: str = "America/Los_Angeles"


    @field_validator("app_name")
    @classmethod
    def app_name_must_not_be_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("APP_NAME must not be empty")
        return value

    @field_validator("backend_cors_origins")
    @classmethod
    def cors_origins_must_be_clean(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def validate_thresholds(self) -> Settings:
        if self.needs_review_threshold >= self.auto_match_threshold:
            raise ValueError(
                "NEEDS_REVIEW_THRESHOLD must be lower than AUTO_MATCH_THRESHOLD"
            )

        if self.evidence_confidence_cap < self.auto_match_threshold:
            raise ValueError(
                "EVIDENCE_CONFIDENCE_CAP must be greater than or equal to "
                "AUTO_MATCH_THRESHOLD"
            )

        return self

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def is_development(self) -> bool:
        return self.app_env == "development"

    @property
    def cors_origins(self) -> list[str]:
        """
        Returns explicit CORS origins.

        In development, if BACKEND_CORS_ORIGINS is not set, common local origins are
        allowed. In production, origins must be explicitly configured.
        """
        if self.backend_cors_origins:
            return [
                origin.strip()
                for origin in self.backend_cors_origins.split(",")
                if origin.strip()
            ]

        if self.is_development:
            return [
                "http://localhost:3000",
                "http://127.0.0.1:3000",
                "http://localhost:8000",
                "http://127.0.0.1:8000",
            ]

        return []

    @staticmethod
    def _secret_is_configured(value: SecretStr) -> bool:
        return bool(value.get_secret_value().strip())

    def configured_integrations(self) -> dict[str, bool]:
        return {
            "supabase": bool(self.supabase_url.strip())
            and self._secret_is_configured(self.supabase_service_role_key),
            "github": self._secret_is_configured(self.github_token),
            "stackoverflow": self._secret_is_configured(self.stackexchange_key),
            "gemini": self._secret_is_configured(self.gemini_api_key),
        }

    def missing_required_settings(self) -> list[str]:
        """
        Required for the final production app.

        STACKEXCHANGE_KEY is not treated as required because Stack Exchange can be
        called without a key at a much lower quota. It should still be configured
        before final submission if possible.
        """
        missing: list[str] = []

        if not self.supabase_url.strip():
            missing.append("SUPABASE_URL")

        if not self._secret_is_configured(self.supabase_service_role_key):
            missing.append("SUPABASE_SERVICE_ROLE_KEY")

        if not self._secret_is_configured(self.github_token):
            missing.append("GITHUB_TOKEN")

        if not self._secret_is_configured(self.gemini_api_key):
            missing.append("GEMINI_API_KEY")

        return missing

    def missing_recommended_settings(self) -> list[str]:
        missing: list[str] = []

        if not self._secret_is_configured(self.stackexchange_key):
            missing.append("STACKEXCHANGE_KEY")

        if not self._secret_is_configured(self.supabase_anon_key):
            missing.append("SUPABASE_ANON_KEY")

        return missing

    def assert_production_ready(self) -> None:
        """
        Fails fast in production if critical config is missing.

        This prevents deploying a Render service that starts successfully but cannot
        actually resolve profiles.
        """
        missing = self.missing_required_settings()
        if self.is_production and missing:
            raise SettingsError(
                message="Production configuration is incomplete.",
                details={"missing_settings": missing},
            )

    def safe_runtime_config(self) -> dict:
        """
        Safe config snapshot for logs and health responses.

        Secrets are never returned.
        """
        return {
            "app_name": self.app_name,
            "app_version": self.app_version,
            "app_env": self.app_env,
            "log_level": self.log_level,
            "request_timeout_seconds": self.request_timeout_seconds,
            "max_candidates_per_platform": self.max_candidates_per_platform,
            "enable_llm_ambiguous_review": self.enable_llm_ambiguous_review,
            "auto_match_threshold": self.auto_match_threshold,
            "needs_review_threshold": self.needs_review_threshold,
            "evidence_confidence_cap": self.evidence_confidence_cap,
            "cors_origins_configured": bool(self.cors_origins),
            "integrations": self.configured_integrations(),
            "missing_required_settings": self.missing_required_settings(),
            "missing_recommended_settings": self.missing_recommended_settings(),
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()