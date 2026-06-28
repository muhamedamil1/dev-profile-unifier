from __future__ import annotations

from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.enums import PlatformSource


class CandidateType(str, Enum):
    PROVIDED_HANDLE = "provided_handle"
    PROVIDED_URL = "provided_url"
    PROVIDED_ID = "provided_id"
    NAME_VARIANT = "name_variant"


class CandidateConfidenceHint(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class CandidateFetchStatus(str, Enum):
    SUCCEEDED = "succeeded"
    NOT_FOUND = "not_found"
    RATE_LIMITED = "rate_limited"
    TIMED_OUT = "timed_out"
    FAILED = "failed"
    SKIPPED = "skipped"


class CandidateDiscoveryWarning(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    source: PlatformSource | None = None
    field: str | None = None
    message: str = Field(..., min_length=1)
    value: str | None = None


class CandidateIdentity(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    source: PlatformSource
    identifier: str = Field(..., min_length=1, max_length=200)

    candidate_type: CandidateType
    confidence_hint: CandidateConfidenceHint

    reason: str = Field(..., min_length=1, max_length=500)
    rank: int = Field(..., ge=0, le=100)

    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("identifier")
    @classmethod
    def clean_identifier(cls, value: str) -> str:
        cleaned = value.strip().lstrip("@")
        if not cleaned:
            raise ValueError("candidate identifier must not be empty")
        return cleaned

    @property
    def dedupe_key(self) -> str:
        return f"{self.source.value}:{self.identifier.lower()}"

    @property
    def is_user_provided(self) -> bool:
        return self.candidate_type in {
            CandidateType.PROVIDED_HANDLE,
            CandidateType.PROVIDED_URL,
            CandidateType.PROVIDED_ID,
        }


class CandidateDiscoveryResult(BaseModel):
    candidates: list[CandidateIdentity] = Field(default_factory=list)
    warnings: list[CandidateDiscoveryWarning] = Field(default_factory=list)

    @property
    def has_candidates(self) -> bool:
        return bool(self.candidates)

    @property
    def sources(self) -> list[PlatformSource]:
        return sorted(
            {candidate.source for candidate in self.candidates},
            key=lambda item: item.value,
        )

    def by_source(self, source: PlatformSource) -> list[CandidateIdentity]:
        return [
            candidate
            for candidate in self.candidates
            if candidate.source == source
        ]


class RawRecordInsertSummary(BaseModel):
    id: UUID
    source: PlatformSource
    source_record_type: str
    source_user_id: str | None = None
    handle: str | None = None


class CandidateIngestionResult(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    candidate: CandidateIdentity
    status: CandidateFetchStatus

    raw_records: list[RawRecordInsertSummary] = Field(default_factory=list)

    error_code: str | None = None
    error_message: str | None = None

    raw_bundle: dict[str, Any] | None = Field(default=None, exclude=True)

    @property
    def succeeded(self) -> bool:
        return self.status == CandidateFetchStatus.SUCCEEDED


class IngestionResult(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    resolution_run_id: UUID
    discovery: CandidateDiscoveryResult
    results: list[CandidateIngestionResult] = Field(default_factory=list)

    @property
    def succeeded(self) -> list[CandidateIngestionResult]:
        return [item for item in self.results if item.succeeded]

    @property
    def failed(self) -> list[CandidateIngestionResult]:
        return [item for item in self.results if not item.succeeded]

    @property
    def sources_attempted(self) -> list[str]:
        return sorted({item.candidate.source.value for item in self.results})

    @property
    def sources_succeeded(self) -> list[str]:
        return sorted({item.candidate.source.value for item in self.succeeded})

    @property
    def sources_failed(self) -> list[str]:
        succeeded_sources = set(self.sources_succeeded)
        return sorted(
            {
                item.candidate.source.value
                for item in self.failed
                if item.candidate.source.value not in succeeded_sources
            }
        )

    @property
    def has_successes(self) -> bool:
        return bool(self.succeeded)

    @property
    def has_failures(self) -> bool:
        return bool(self.failed)
