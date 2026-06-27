from __future__ import annotations

from app.config import Settings, get_settings
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
