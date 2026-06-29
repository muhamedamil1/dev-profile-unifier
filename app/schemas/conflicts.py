from __future__ import annotations

from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.enums import ConflictSeverity, PlatformSource


class ConflictType(str, Enum):
    NAME_CONFLICT = "name_conflict"
    WEBSITE_CONFLICT = "website_conflict"
    LOCATION_CONFLICT = "location_conflict"
    EMAIL_CONFLICT = "email_conflict"
    TOPIC_MISMATCH = "topic_mismatch"


CONFLICT_PENALTIES: dict[ConflictType, float] = {
    ConflictType.NAME_CONFLICT: -0.25,
    ConflictType.WEBSITE_CONFLICT: -0.20,
    ConflictType.LOCATION_CONFLICT: -0.10,
    ConflictType.EMAIL_CONFLICT: -0.35,
    ConflictType.TOPIC_MISMATCH: -0.05,
}


class DetectedConflict(BaseModel):
    """
    Deterministic contradiction detected between two normalized source accounts.

    This is not a final rejection decision. Scoring and classification happen later.
    """

    model_config = ConfigDict(validate_assignment=True)

    conflict_type: ConflictType
    severity: ConflictSeverity
    penalty: float = Field(..., ge=-1.0, le=0.0)

    source_account_id: UUID | None = None
    source_account_key: str
    source: PlatformSource

    target_account_id: UUID | None = None
    target_account_key: str
    target_source: PlatformSource

    description: str = Field(..., min_length=1, max_length=1000)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("source_account_key", "target_account_key", mode="before")
    @classmethod
    def clean_keys(cls, value: Any) -> str:
        cleaned = str(value).strip().lower()
        if not cleaned:
            raise ValueError("account key cannot be empty")
        return cleaned

    @property
    def dedupe_key(self) -> str:
        normalized_value = str(self.metadata.get("normalized_value") or "").lower()

        return "|".join(
            [
                self.conflict_type.value,
                self.source_account_key,
                self.target_account_key,
                normalized_value,
            ]
        )


class ConflictDetectionResult(BaseModel):
    conflicts: list[DetectedConflict] = Field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.conflicts)

    @property
    def total_penalty(self) -> float:
        """
        Debug-only aggregate. Phase 7C must score conflicts per account/pair
        with caps instead of treating this global sum as a decision score.
        """
        return round(sum(item.penalty for item in self.conflicts), 4)

    @property
    def by_type(self) -> dict[str, int]:
        counts: dict[str, int] = {}

        for item in self.conflicts:
            counts[item.conflict_type.value] = counts.get(item.conflict_type.value, 0) + 1

        return counts

    @property
    def by_severity(self) -> dict[str, int]:
        counts: dict[str, int] = {}

        for item in self.conflicts:
            counts[item.severity.value] = counts.get(item.severity.value, 0) + 1

        return counts