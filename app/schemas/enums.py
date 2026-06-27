from __future__ import annotations

from enum import StrEnum


class PlatformSource(StrEnum):
    GITHUB = "github"
    STACKOVERFLOW = "stackoverflow"
    DEVTO = "devto"
    HACKERNEWS = "hackernews"


class MetricSource(StrEnum):
    GITHUB = "github"
    STACKOVERFLOW = "stackoverflow"
    DEVTO = "devto"
    HACKERNEWS = "hackernews"
    GEMINI = "gemini"
    SUPABASE = "supabase"


class ResolutionStatus(StrEnum):
    RUNNING = "running"
    RESOLVED = "resolved"
    PARTIAL = "partial"
    FAILED = "failed"


class ProfileConfidenceLevel(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNCERTAIN = "uncertain"


class MatchDecision(StrEnum):
    AUTO_MATCH = "auto_match"
    NEEDS_REVIEW = "needs_review"
    REJECT = "reject"


class SourceRelationshipType(StrEnum):
    PRIMARY = "primary"
    SECONDARY = "secondary"
    ALIAS = "alias"
    POSSIBLE_ALIAS = "possible_alias"
    REJECTED = "rejected"


class VerificationStatus(StrEnum):
    CLAIMED_BY_INPUT = "claimed_by_input"
    EVIDENCE_MATCHED = "evidence_matched"
    RECIPROCAL_LINK_VERIFIED = "reciprocal_link_verified"
    LIKELY_SAME_PERSON = "likely_same_person"
    NEEDS_REVIEW = "needs_review"
    REJECTED = "rejected"


class EvidenceDirection(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


class ConflictSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class HttpMethod(StrEnum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"