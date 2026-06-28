from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.enums import PlatformSource
from app.schemas.source_account import SourceAccount


class NormalizationWarning(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    source: PlatformSource | None = None
    raw_record_id: UUID | None = None
    source_record_type: str | None = None
    message: str = Field(..., min_length=1)
    details: dict[str, Any] = Field(default_factory=dict)


class NormalizedAccountResult(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    source_account: SourceAccount
    raw_record_ids: list[UUID] = Field(default_factory=list)
    warnings: list[NormalizationWarning] = Field(default_factory=list)

    persisted_row: dict[str, Any] | None = None


class SourceAccountNormalizationResult(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    resolution_run_id: UUID
    accounts: list[NormalizedAccountResult] = Field(default_factory=list)
    warnings: list[NormalizationWarning] = Field(default_factory=list)

    @property
    def normalized_count(self) -> int:
        return len(self.accounts)

    @property
    def sources_normalized(self) -> list[str]:
        return sorted(
            {
                result.source_account.source.value
                for result in self.accounts
            }
        )

    @property
    def has_warnings(self) -> bool:
        return bool(self.warnings) or any(result.warnings for result in self.accounts)