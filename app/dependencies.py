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
    )
