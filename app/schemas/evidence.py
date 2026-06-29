from __future__ import annotations

from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.enums import EvidenceDirection, PlatformSource


class EvidenceType(str, Enum):
    INPUT_HANDLE_MATCH = "input_handle_match"
    EXACT_NAME_MATCH = "exact_name_match"
    PARTIAL_NAME_MATCH = "partial_name_match"
    SAME_WEBSITE = "same_website"
    DIRECT_PROFILE_LINK = "direct_profile_link"
    RECIPROCAL_PROFILE_LINK = "reciprocal_profile_link"
    SIMILAR_HANDLE = "similar_handle"
    EMAIL_HINT_MATCH = "email_hint_match"
    EMAIL_DOMAIN_MATCH = "email_domain_match"
    SAME_LOCATION = "same_location"
    LOCATION_OVERLAP = "location_overlap"
    BIO_KEYWORD_OVERLAP = "bio_keyword_overlap"
    TOPIC_OVERLAP = "topic_overlap"


class EvidenceTargetType(str, Enum):
    REQUEST = "request"
    ACCOUNT_PAIR = "account_pair"


class EvidenceIndependenceGroup(str, Enum):
    INPUT_IDENTIFIER = "input_identifier"
    NAME = "name"
    WEBSITE = "website"
    PROFILE_LINK = "profile_link"
    HANDLE = "handle"
    EMAIL = "email"
    LOCATION = "location"
    BIO = "bio"
    TOPICS = "topics"


EVIDENCE_WEIGHTS: dict[EvidenceType, float] = {
    EvidenceType.INPUT_HANDLE_MATCH: 0.25,
    EvidenceType.EXACT_NAME_MATCH: 0.20,
    EvidenceType.PARTIAL_NAME_MATCH: 0.10,
    EvidenceType.SAME_WEBSITE: 0.30,
    EvidenceType.DIRECT_PROFILE_LINK: 0.40,
    EvidenceType.RECIPROCAL_PROFILE_LINK: 0.45,
    EvidenceType.SIMILAR_HANDLE: 0.12,
    EvidenceType.EMAIL_HINT_MATCH: 0.35,
    EvidenceType.EMAIL_DOMAIN_MATCH: 0.12,
    EvidenceType.SAME_LOCATION: 0.08,
    EvidenceType.LOCATION_OVERLAP: 0.05,
    EvidenceType.BIO_KEYWORD_OVERLAP: 0.08,
    EvidenceType.TOPIC_OVERLAP: 0.06,
}


EVIDENCE_INDEPENDENCE_GROUPS: dict[EvidenceType, EvidenceIndependenceGroup] = {
    EvidenceType.INPUT_HANDLE_MATCH: EvidenceIndependenceGroup.INPUT_IDENTIFIER,
    EvidenceType.EXACT_NAME_MATCH: EvidenceIndependenceGroup.NAME,
    EvidenceType.PARTIAL_NAME_MATCH: EvidenceIndependenceGroup.NAME,
    EvidenceType.SAME_WEBSITE: EvidenceIndependenceGroup.WEBSITE,
    EvidenceType.DIRECT_PROFILE_LINK: EvidenceIndependenceGroup.PROFILE_LINK,
    EvidenceType.RECIPROCAL_PROFILE_LINK: EvidenceIndependenceGroup.PROFILE_LINK,
    EvidenceType.SIMILAR_HANDLE: EvidenceIndependenceGroup.HANDLE,
    EvidenceType.EMAIL_HINT_MATCH: EvidenceIndependenceGroup.EMAIL,
    EvidenceType.EMAIL_DOMAIN_MATCH: EvidenceIndependenceGroup.EMAIL,
    EvidenceType.SAME_LOCATION: EvidenceIndependenceGroup.LOCATION,
    EvidenceType.LOCATION_OVERLAP: EvidenceIndependenceGroup.LOCATION,
    EvidenceType.BIO_KEYWORD_OVERLAP: EvidenceIndependenceGroup.BIO,
    EvidenceType.TOPIC_OVERLAP: EvidenceIndependenceGroup.TOPICS,
}


class ExtractedEvidence(BaseModel):
    """
    Deterministic evidence signal extracted from request/account or account/account comparison.

    This is not a final decision. Scoring and classification happen later.
    """

    model_config = ConfigDict(validate_assignment=True)

    evidence_type: EvidenceType
    direction: EvidenceDirection = EvidenceDirection.POSITIVE

    target_type: EvidenceTargetType

    source_account_id: UUID | None = None
    source_account_key: str
    source: PlatformSource

    target_account_id: UUID | None = None
    target_account_key: str | None = None
    target_source: PlatformSource | None = None

    weight: float = Field(..., ge=0.0, le=1.0)
    independence_group: EvidenceIndependenceGroup

    reason: str = Field(..., min_length=1, max_length=1000)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("source_account_key", "target_account_key", mode="before")
    @classmethod
    def clean_keys(cls, value: Any) -> str | None:
        if value is None:
            return None

        cleaned = str(value).strip().lower()
        return cleaned or None

    @property
    def dedupe_key(self) -> str:
        normalized_value = str(self.metadata.get("normalized_value") or "").lower()

        return "|".join(
            [
                self.evidence_type.value,
                self.target_type.value,
                self.source_account_key,
                self.target_account_key or "",
                normalized_value,
            ]
        )


class EvidenceExtractionResult(BaseModel):
    evidence: list[ExtractedEvidence] = Field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.evidence)

    @property
    def positive_count(self) -> int:
        return sum(1 for item in self.evidence if item.direction == EvidenceDirection.POSITIVE)

    @property
    def by_type(self) -> dict[str, int]:
        counts: dict[str, int] = {}

        for item in self.evidence:
            counts[item.evidence_type.value] = counts.get(item.evidence_type.value, 0) + 1

        return counts