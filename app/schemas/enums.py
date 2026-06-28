from __future__ import annotations

from enum import Enum


class PlatformSource(str, Enum):
    GITHUB = "github"
    STACKOVERFLOW = "stackoverflow"
    DEVTO = "devto"
    HACKERNEWS = "hackernews"


class MetricSource(str, Enum):
    GITHUB = "github"
    STACKOVERFLOW = "stackoverflow"
    DEVTO = "devto"
    HACKERNEWS = "hackernews"
    GEMINI = "gemini"
    SUPABASE = "supabase"


class ResolutionStatus(str, Enum):
    RUNNING = "running"
    RESOLVED = "resolved"
    PARTIAL = "partial"
    FAILED = "failed"


class ProfileConfidenceLevel(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNCERTAIN = "uncertain"


class MatchDecision(str, Enum):
    AUTO_MATCH = "auto_match"
    NEEDS_REVIEW = "needs_review"
    REJECT = "reject"


class SourceRelationshipType(str, Enum):
    PRIMARY = "primary"
    SECONDARY = "secondary"
    ALIAS = "alias"
    POSSIBLE_ALIAS = "possible_alias"
    REJECTED = "rejected"


class VerificationStatus(str, Enum):
    CLAIMED_BY_INPUT = "claimed_by_input"
    EVIDENCE_MATCHED = "evidence_matched"
    RECIPROCAL_LINK_VERIFIED = "reciprocal_link_verified"
    LIKELY_SAME_PERSON = "likely_same_person"
    NEEDS_REVIEW = "needs_review"
    REJECTED = "rejected"


class EvidenceDirection(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


class ConflictSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class HttpMethod(str, Enum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"