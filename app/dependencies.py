from __future__ import annotations

from pydantic import SecretStr

from app.config import Settings, get_settings
from app.integrations import (
    DevToClient,
    GitHubClient,
    HackerNewsClient,
    StackOverflowClient,
)
from app.services import (
    CandidateDiscoveryService,
    IngestionService,
    SourceAccountNormalizationService,
)
from app.storage import (
    ConflictsRepo,
    EvidenceRepo,
    FactsRepo,
    MetricsRepo,
    ProfilesRepo,
    RawRecordsRepo,
    ResolutionRunsRepo,
    SourceAccountsRepo,
    SummariesRepo,
    get_supabase_client,
)
from app.resolution.classifier import DecisionClassifier
from app.resolution.conflict_detector import ConflictDetector
from app.resolution.evidence import EvidenceExtractor
from app.resolution.scorer import ResolutionScorer
from app.services.resolution_service import ResolutionService
from app.storage.conflicts_repo import ConflictsRepo
from app.storage.evidence_repo import EvidenceRepo
from app.storage.profiles_repo import ProfilesRepo
from app.storage.resolution_runs_repo import ResolutionRunsRepo
from app.storage.source_accounts_repo import SourceAccountsRepo

from app.services.canonical_profile_service import CanonicalProfileService

from app.llm.gemini_client import GeminiClient
from app.services.summary_service import SummaryService
from app.storage.metrics_repo import MetricsRepo
from app.storage.resolution_runs_repo import ResolutionRunsRepo
from app.storage.summaries_repo import SummariesRepo



from app.config import get_settings
from app.llm.gemini_client import GeminiClient
from app.resolution.ambiguity_reviewer import GeminiAmbiguityReviewer
from app.storage.metrics_repo import MetricsRepo

from app.llm.gemini_client import GeminiClient, GeminiRetryConfig
from app.llm.rate_limiter import GeminiRateLimitConfig, GeminiRateLimiter


from app.services.health_dashboard_service import HealthDashboardService
from app.services.profile_orchestration_service import ProfileOrchestrationService
from app.services.profile_read_service import ProfileReadService


_SHARED_GEMINI_RATE_LIMITER: GeminiRateLimiter | None = None
_SHARED_GEMINI_CLIENT: GeminiClient | None = None


def _secret_value(value: SecretStr | None) -> str | None:
    if value is None:
        return None

    secret = value.get_secret_value().strip()
    return secret or None


def get_app_settings() -> Settings:
    """
    FastAPI dependency for accessing application settings.
    """
    return get_settings()


def get_resolution_runs_repo() -> ResolutionRunsRepo:
    return ResolutionRunsRepo(get_supabase_client())


def get_raw_records_repo() -> RawRecordsRepo:
    return RawRecordsRepo(get_supabase_client())


def get_source_accounts_repo() -> SourceAccountsRepo:
    return SourceAccountsRepo(get_supabase_client())


def get_profiles_repo() -> ProfilesRepo:
    return ProfilesRepo(get_supabase_client())


def get_evidence_repo() -> EvidenceRepo:
    return EvidenceRepo(get_supabase_client())


def get_conflicts_repo() -> ConflictsRepo:
    return ConflictsRepo(get_supabase_client())


def get_facts_repo() -> FactsRepo:
    return FactsRepo(get_supabase_client())


def get_summaries_repo() -> SummariesRepo:
    return SummariesRepo(get_supabase_client())


def get_metrics_repo() -> MetricsRepo:
    return MetricsRepo(get_supabase_client())


def get_github_client() -> GitHubClient:
    settings = get_settings()

    return GitHubClient(
        timeout_seconds=settings.request_timeout_seconds,
        metrics_repo=get_metrics_repo(),
        token=_secret_value(settings.github_token),
    )


def get_stackoverflow_client() -> StackOverflowClient:
    settings = get_settings()

    return StackOverflowClient(
        timeout_seconds=settings.request_timeout_seconds,
        metrics_repo=get_metrics_repo(),
        api_key=_secret_value(settings.stackexchange_key),
    )


def get_devto_client() -> DevToClient:
    settings = get_settings()

    return DevToClient(
        timeout_seconds=settings.request_timeout_seconds,
        metrics_repo=get_metrics_repo(),
    )


def get_hackernews_client() -> HackerNewsClient:
    settings = get_settings()

    return HackerNewsClient(
        timeout_seconds=settings.request_timeout_seconds,
        metrics_repo=get_metrics_repo(),
    )


def get_candidate_discovery_service() -> CandidateDiscoveryService:
    settings = get_settings()

    return CandidateDiscoveryService(
        max_candidates_per_platform=min(settings.max_candidates_per_platform, 3),
        max_total_candidates=12,
        expand_name_variants_when_direct_input_exists=False,
    )


def get_ingestion_service() -> IngestionService:
    return IngestionService(
        candidate_discovery=get_candidate_discovery_service(),
        raw_records_repo=get_raw_records_repo(),
        github_client=get_github_client(),
        stackoverflow_client=get_stackoverflow_client(),
        devto_client=get_devto_client(),
        hackernews_client=get_hackernews_client(),
    )


def get_source_account_normalization_service() -> SourceAccountNormalizationService:
    return SourceAccountNormalizationService(
        raw_records_repo=get_raw_records_repo(),
        source_accounts_repo=get_source_accounts_repo(),
    )

def get_resolution_service() -> ResolutionService:
    return ResolutionService(
        evidence_extractor=EvidenceExtractor(),
        conflict_detector=ConflictDetector(),
        scorer=ResolutionScorer(),
        classifier=DecisionClassifier(),
        evidence_repo=get_evidence_repo(),
        conflicts_repo=get_conflicts_repo(),
        profiles_repo=get_profiles_repo(),
        source_accounts_repo=get_source_accounts_repo(),
        resolution_runs_repo=get_resolution_runs_repo(),
        ambiguity_reviewer=get_gemini_ambiguity_reviewer(),
    )


def get_canonical_profile_service() -> CanonicalProfileService:
    return CanonicalProfileService(
        profiles_repo=get_profiles_repo(),
        source_accounts_repo=get_source_accounts_repo(),
        resolution_runs_repo=get_resolution_runs_repo(),
    )




def _first_setting(settings, *names: str, default=None):
    for name in names:
        value = getattr(settings, name, None)
        if value:
            return value
    return default


def get_summary_service() -> SummaryService:
    return SummaryService(
        profiles_repo=get_profiles_repo(),
        summaries_repo=get_summaries_repo(),
        metrics_repo=get_metrics_repo(),
        resolution_runs_repo=get_resolution_runs_repo(),
        gemini_client=get_gemini_client(),
    )


def _first_setting(settings, *names: str, default=None):
    for name in names:
        value = getattr(settings, name, None)
        if value:
            return value
    return default


def get_gemini_ambiguity_reviewer() -> GeminiAmbiguityReviewer:
    settings = get_settings()

    return GeminiAmbiguityReviewer(
        gemini_client=get_gemini_client(),
        metrics_repo=get_metrics_repo(),
        settings=settings,
    )


def _first_setting(settings, *names: str, default=None):
    for name in names:
        value = getattr(settings, name, None)
        if value not in (None, ""):
            return value
    return default


def get_shared_gemini_rate_limiter() -> GeminiRateLimiter:
    global _SHARED_GEMINI_RATE_LIMITER

    if _SHARED_GEMINI_RATE_LIMITER is None:
        settings = get_settings()
        _SHARED_GEMINI_RATE_LIMITER = GeminiRateLimiter(
            GeminiRateLimitConfig.from_settings(settings)
        )

    return _SHARED_GEMINI_RATE_LIMITER


def get_gemini_client() -> GeminiClient:
    global _SHARED_GEMINI_CLIENT

    if _SHARED_GEMINI_CLIENT is None:
        settings = get_settings()
        api_key = _first_setting(
            settings,
            "gemini_api_key",
            "google_gemini_api_key",
            "google_api_key",
            "GEMINI_API_KEY",
        )
        model_name = _first_setting(
            settings,
            "gemini_model",
            "gemini_model_name",
            "GEMINI_MODEL",
            default="gemini-2.5-flash-lite",
        )
        timeout_seconds = float(_first_setting(
            settings,
            "gemini_timeout_seconds",
            "GEMINI_TIMEOUT_SECONDS",
            default=30.0,
        ))

        _SHARED_GEMINI_CLIENT = GeminiClient(
            api_key=api_key,
            model_name=model_name,
            timeout_seconds=timeout_seconds,
            retry_config=GeminiRetryConfig.from_settings(settings),
            rate_limiter=get_shared_gemini_rate_limiter(),
        )

    return _SHARED_GEMINI_CLIENT


def reset_shared_gemini_client_for_tests() -> None:
    """Optional test helper. Do not call from request handlers."""
    global _SHARED_GEMINI_CLIENT, _SHARED_GEMINI_RATE_LIMITER
    _SHARED_GEMINI_CLIENT = None
    _SHARED_GEMINI_RATE_LIMITER = None




def get_profile_read_service() -> ProfileReadService:
    return ProfileReadService(
        profiles_repo=get_profiles_repo(),
        summaries_repo=get_summaries_repo(),
        resolution_runs_repo=get_resolution_runs_repo(),
    )


def get_health_dashboard_service() -> HealthDashboardService:
    return HealthDashboardService(
        supabase_client=get_supabase_client(),
        metrics_repo=get_metrics_repo(),
        resolution_runs_repo=get_resolution_runs_repo(),
        settings=get_settings(),
    )


def get_profile_orchestration_service() -> ProfileOrchestrationService:
    return ProfileOrchestrationService(
        ingestion_service=get_ingestion_service(),
        normalization_service=get_source_account_normalization_service(),
        resolution_service=get_resolution_service(),
        canonical_profile_service=get_canonical_profile_service(),
        summary_service=get_summary_service(),
        profile_read_service=get_profile_read_service(),
        resolution_runs_repo=get_resolution_runs_repo(),
        settings=get_settings(),
    )
