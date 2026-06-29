from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.classification import ClassificationResult
from app.schemas.conflicts import ConflictDetectionResult
from app.schemas.evidence import EvidenceExtractionResult
from app.schemas.scoring import ScoringResult


class ResolutionPersistenceCounts(BaseModel):
    match_evidence_rows: int = 0
    profile_conflict_rows: int = 0
    profile_source_link_rows: int = 0
    canonical_profile_created: bool = False
    canonical_profile_reused: bool = False
    canonical_profile_upserted: bool = False
    canonical_profile_id: UUID | None = None


class ResolutionPipelineResult(BaseModel):
    resolution_run_id: UUID
    canonical_profile_id: UUID | None = None

    evidence: EvidenceExtractionResult
    conflicts: ConflictDetectionResult
    scoring: ScoringResult
    classification: ClassificationResult

    persistence: ResolutionPersistenceCounts = Field(default_factory=ResolutionPersistenceCounts)
    persisted: bool = False

    summary: dict[str, Any] = Field(default_factory=dict)
