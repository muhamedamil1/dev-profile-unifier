from __future__ import annotations

from pydantic import SecretStr

from app.config import Settings, get_settings
from app.integrations import (
    DevToClient,
    GitHubClient,
    HackerNewsClient,
    StackOverflowClient,
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