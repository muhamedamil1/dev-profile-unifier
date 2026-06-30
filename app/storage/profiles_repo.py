from __future__ import annotations

import logging
import re
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from app.resolution.ambiguity_reviewer import (
    final_link_fields_after_review,
    merge_llm_review_into_decision_payload,
)
from app.schemas.classification import AccountClassification
from app.schemas.enums import (
    MatchDecision,
    ProfileConfidenceLevel,
    SourceRelationshipType,
    VerificationStatus,
)
from app.schemas.requests import ProfileResolveRequest
from app.storage.base import BaseRepository
from app.utils.errors import ProfileNotFoundError, StorageError

logger = logging.getLogger(__name__)

VALID_PROFILE_SOURCE_RELATIONSHIP_TYPES = {
    "primary",
    "secondary",
    "alias",
    "possible_alias",
    "rejected",
}

VALID_PROFILE_SOURCE_VERIFICATION_STATUSES = {
    "claimed_by_input",
    "evidence_matched",
    "reciprocal_link_verified",
    "likely_same_person",
    "needs_review",
    "rejected",
}

VALID_MATCH_DECISIONS = {
    "auto_match",
    "needs_review",
    "reject",
}

VALID_CONFLICT_SEVERITIES = {
    "low",
    "medium",
    "high",
}


class ProfilesRepo(BaseRepository):
    table_name = "canonical_profiles"

    _PROFILE_SOURCE_LINK_FALLBACK_COLUMNS = {
        "decision_payload",
        "positive_signal_count",
        "negative_signal_count",
        "has_high_conflict",
        "verification_status",
    }

    def create_profile(
        self,
        *,
        resolution_run_id: str | UUID | None = None,
        display_name: str | None = None,
        headline: str | None = None,
        location: str | None = None,
        bio: str | None = None,
        primary_avatar_url: str | None = None,
        primary_website_url: str | None = None,
        inferred_skills: list[str] | None = None,
        confidence_level: ProfileConfidenceLevel | str = ProfileConfidenceLevel.UNCERTAIN,
        profile_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._insert_one(
            {
                "id": str(uuid4()),
                "resolution_run_id": resolution_run_id,
                "display_name": display_name,
                "headline": headline,
                "location": location,
                "bio": bio,
                "primary_avatar_url": primary_avatar_url,
                "primary_website_url": primary_website_url,
                "inferred_skills": inferred_skills or [],
                "confidence_level": self._enum_or_string_value(confidence_level),
                "profile_payload": profile_payload or {},
            }
        )

    def update_profile(
        self,
        profile_id: str | UUID,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return self._update_by_id(profile_id, payload)

    def get_by_id(self, profile_id: UUID | str) -> dict[str, Any] | None:
        data = self._execute(
            self.client.table(self.table_name)
            .select("*")
            .eq("id", str(profile_id))
            .limit(1),
            operation="get_canonical_profile_by_id",
        )
        return self._first_or_none(data)

    def get_by_resolution_run_id(self, resolution_run_id: UUID | str) -> dict[str, Any] | None:
        data = self._execute(
            self.client.table(self.table_name)
            .select("*")
            .eq("resolution_run_id", str(resolution_run_id))
            .limit(1),
            operation="get_canonical_profile_by_resolution_run_id",
        )
        return self._first_or_none(data)

    def delete_by_id(self, profile_id: str | UUID) -> int:
        data = self._execute(
            self.client.table(self.table_name)
            .delete()
            .eq("id", str(profile_id)),
            operation="delete_profile_by_id",
        )
        return len(data or [])

    def create_profile_source_link(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        clean_payload = self._normalize_profile_source_link_payload(payload)
        clean_payload = self._serialize_payload(clean_payload)

        data = self._execute(
            self.client.table("profile_source_links").insert(clean_payload),
            operation="create_profile_source_link",
        )
        row = self._first_or_none(data)
        if row is not None:
            return row

        recovered = self._read_profile_source_links_after_uncertain_insert([clean_payload])
        if self._has_all_profile_source_links([clean_payload], recovered):
            return recovered[0]

        raise StorageError(
            "Database operation returned no rows: create_profile_source_link",
            details={
                "table": "profile_source_links",
                "operation": "create_profile_source_link",
                "profile_id": clean_payload.get("profile_id"),
                "source_account_id": clean_payload.get("source_account_id"),
            },
        )

    def get_profile_source_links(
        self,
        profile_id: str | UUID,
    ) -> list[dict[str, Any]]:
        data = self._execute(
            self.client.table("profile_source_links")
            .select("*")
            .eq("profile_id", str(profile_id))
            .order("confidence_score", desc=True),
            operation="get_profile_source_links",
        )
        return data if isinstance(data, list) else []

    def get_full_profile_data(self, profile_id: str | UUID) -> dict[str, Any]:
        """
        Read all data needed to build GET /profiles/{id}.

        The API response conversion happens in the service/API layer.
        This repository returns raw database rows grouped in a useful shape.
        """
        profile = self.get_by_id(profile_id)
        if profile is None:
            raise ProfileNotFoundError(str(profile_id))

        source_links = self.get_profile_source_links(profile_id)

        source_account_ids = [
            row["source_account_id"]
            for row in source_links
            if row.get("source_account_id")
        ]
        link_ids = [
            row["id"]
            for row in source_links
            if row.get("id")
        ]

        source_accounts: list[dict[str, Any]] = []
        if source_account_ids:
            data = self._execute(
                self.client.table("source_accounts")
                .select("*")
                .in_("id", source_account_ids),
                operation="get_full_profile_source_accounts",
            )
            source_accounts = data if isinstance(data, list) else []

        evidence: list[dict[str, Any]] = []
        if link_ids:
            data = self._execute(
                self.client.table("match_evidence")
                .select("*")
                .in_("profile_source_link_id", link_ids)
                .order("created_at", desc=False),
                operation="get_full_profile_evidence",
            )
            evidence = data if isinstance(data, list) else []

        conflicts_data = self._execute(
            self.client.table("profile_conflicts")
            .select("*")
            .eq("profile_id", str(profile_id))
            .order("created_at", desc=False),
            operation="get_full_profile_conflicts",
        )
        conflicts = conflicts_data if isinstance(conflicts_data, list) else []

        facts_data = self._execute(
            self.client.table("profile_facts")
            .select("*")
            .eq("profile_id", str(profile_id))
            .order("created_at", desc=False),
            operation="get_full_profile_facts",
        )
        facts = facts_data if isinstance(facts_data, list) else []

        summary_data = self._execute(
            self.client.table("llm_summaries")
            .select("*")
            .eq("profile_id", str(profile_id))
            .order("created_at", desc=True)
            .limit(1),
            operation="get_full_profile_latest_summary",
        )
        latest_summary = self._first_or_none(summary_data)

        evidence_by_link_id: dict[str, list[dict[str, Any]]] = {}
        for item in evidence:
            link_id = str(item.get("profile_source_link_id"))
            evidence_by_link_id.setdefault(link_id, []).append(item)

        source_accounts_by_id = {
            str(item["id"]): item
            for item in source_accounts
            if item.get("id")
        }

        return {
            "profile": profile,
            "source_links": source_links,
            "source_accounts": source_accounts,
            "source_accounts_by_id": source_accounts_by_id,
            "evidence": evidence,
            "evidence_by_link_id": evidence_by_link_id,
            "conflicts": conflicts,
            "facts": facts,
            "latest_summary": latest_summary,
        }

    def create_resolution_shell(
        self,
        *,
        resolution_run_id: UUID | str,
        request: ProfileResolveRequest,
        summary: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        existing = self.get_by_resolution_run_id(resolution_run_id)

        payload = {
            "resolution_run_id": str(resolution_run_id),
            "display_name": request.name,
            "headline": None,
            "location": None,
            "bio": None,
            "primary_avatar_url": None,
            "primary_website_url": None,
            "inferred_skills": [],
            "confidence_level": summary.get(
                "confidence_level",
                ProfileConfidenceLevel.UNCERTAIN.value,
            ),
            "profile_payload": {
                "profile_stage": "resolution_shell",
                "phase": "7E",
                "canonical_fields_pending": True,
                "resolution_summary": summary,
                "max_evidence_confidence_score": summary.get("max_evidence_confidence_score"),
                "max_decision_confidence_score": summary.get("max_decision_confidence_score"),
                "created_by": "resolution_service",
            },
        }

        if existing:
            return self._update_by_id(
                existing["id"],
                payload,
                strip_none=False,
            ), False

        return self._insert_one({"id": str(uuid4()), **payload}), True

    def delete_source_links_for_profile(self, canonical_profile_id: UUID | str) -> int:
        data = self._execute(
            self.client.table("profile_source_links")
            .delete()
            .eq("profile_id", str(canonical_profile_id)),
            operation="delete_profile_source_links",
        )
        return len(data or [])

    def insert_source_links_for_classifications(
        self,
        *,
        canonical_profile_id: UUID | str | None = None,
        profile_id: UUID | str | None = None,
        classifications: list[AccountClassification],
        review_outcome_by_key: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if not classifications:
            return []

        target_profile_id = profile_id if profile_id is not None else canonical_profile_id
        if target_profile_id is None:
            return []

        payloads = [
            self._source_link_payload(
                target_profile_id=target_profile_id,
                item=item,
                review_outcome_by_key=review_outcome_by_key or {},
            )
            for item in classifications
            if item.source_account_id is not None
        ]

        if not payloads:
            return []

        clean_payloads = [
            self._serialize_payload(
                self._normalize_profile_source_link_payload(payload)
            )
            for payload in payloads
        ]

        data = self._insert_profile_source_links_with_fallback(clean_payloads)
        return data if isinstance(data, list) else []

    def list_source_links_for_profile(self, profile_id: UUID | str) -> list[dict[str, Any]]:
        data = self._execute(
            self.client.table("profile_source_links")
            .select("*")
            .eq("profile_id", str(profile_id))
            .order("created_at"),
            operation="list_profile_source_links_for_profile",
        )
        return data if isinstance(data, list) else []

    def update_canonical_profile_fields(
        self,
        *,
        profile_id: UUID | str,
        display_name: str | None,
        headline: str | None,
        location: str | None,
        bio: str | None,
        primary_avatar_url: str | None,
        primary_website_url: str | None,
        inferred_skills: list[str],
        confidence_level: str,
        profile_payload: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {
            "display_name": display_name,
            "headline": headline,
            "location": location,
            "bio": bio,
            "primary_avatar_url": primary_avatar_url,
            "primary_website_url": primary_website_url,
            "inferred_skills": inferred_skills,
            "confidence_level": confidence_level,
            "profile_payload": profile_payload,
            "updated_at": datetime.now(UTC).isoformat(),
        }

        return self._update_by_id(
            profile_id,
            payload,
            strip_none=False,
        )

    def update_profile_payload_patch(
        self,
        *,
        profile_id: UUID | str,
        patch: dict[str, Any],
    ) -> dict[str, Any]:
        existing = self.get_by_id(profile_id)
        if not existing:
            raise ProfileNotFoundError(str(profile_id))

        payload = existing.get("profile_payload")
        if not isinstance(payload, dict):
            payload = {}

        merged_payload = {
            **payload,
            **patch,
        }

        return self._update_by_id(
            profile_id,
            {
                "profile_payload": merged_payload,
                "updated_at": datetime.now(UTC).isoformat(),
            },
            strip_none=False,
        )

    def create_profile_conflict(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Insert a single normalized profile conflict row.

        This method intentionally sanitizes values at the repository boundary because
        Supabase/PostgREST rejects nullable, invalid UUID, invalid enum, invalid
        numeric, or non-JSON-safe values for the current profile_conflicts schema.
        """
        clean_payload = self._normalize_profile_conflict_payload(payload)
        clean_payload = self._serialize_payload(clean_payload, strip_none=False)

        self._log_profile_conflict_rows(
            [clean_payload],
            operation="create_profile_conflict",
        )

        try:
            data = self._execute(
                self.client.table("profile_conflicts").insert(clean_payload),
                operation="create_profile_conflict",
            )
        except StorageError as exc:
            logger.exception(
                "profile_conflicts single insert failed after normalization. "
                "Sanitized row attempted: %s | storage_details=%s",
                clean_payload,
                getattr(exc, "internal_details", None),
            )
            raise

        return self._require_one(data, operation="create_profile_conflict")

    def insert_profile_conflicts(
        self,
        *,
        profile_id: UUID | str | None = None,
        canonical_profile_id: UUID | str | None = None,
        conflicts: list[Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Insert normalized conflict rows for a canonical profile.

        Accepts both dict-like conflicts and schema/model objects. The method is
        deliberately defensive: DB-required fields are never sent as null, enums
        are converted to DB-safe strings, numeric impact is coerced safely, and
        source_values is converted into a JSON object.
        """
        target_profile_id = profile_id if profile_id is not None else canonical_profile_id
        if target_profile_id is None or not conflicts:
            return []

        rows = [
            self._profile_conflict_payload(
                profile_id=target_profile_id,
                conflict=conflict,
            )
            for conflict in conflicts
        ]
        rows = [row for row in rows if row]
        if not rows:
            return []

        clean_rows = [
            self._serialize_payload(
                self._normalize_profile_conflict_payload(row),
                strip_none=False,
            )
            for row in rows
        ]

        self._log_profile_conflict_rows(
            clean_rows,
            operation="insert_profile_conflicts",
        )

        try:
            data = self._execute(
                self.client.table("profile_conflicts").insert(clean_rows),
                operation="insert_profile_conflicts",
            )
        except StorageError as exc:
            logger.exception(
                "profile_conflicts batch insert failed after normalization. "
                "Sanitized rows attempted: %s | storage_details=%s",
                clean_rows,
                getattr(exc, "internal_details", None),
            )
            raise

        return data if isinstance(data, list) else []

    def insert_conflicts_for_profile(
        self,
        *,
        profile_id: UUID | str | None = None,
        canonical_profile_id: UUID | str | None = None,
        conflicts: list[Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Compatibility alias for older resolution-service calls."""
        return self.insert_profile_conflicts(
            profile_id=profile_id,
            canonical_profile_id=canonical_profile_id,
            conflicts=conflicts,
        )

    def insert_profile_conflicts_for_profile(
        self,
        *,
        profile_id: UUID | str | None = None,
        canonical_profile_id: UUID | str | None = None,
        conflicts: list[Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Compatibility alias for phase-specific call sites."""
        return self.insert_profile_conflicts(
            profile_id=profile_id,
            canonical_profile_id=canonical_profile_id,
            conflicts=conflicts,
        )

    def delete_conflicts_for_profile(self, profile_id: UUID | str) -> int:
        data = self._execute(
            self.client.table("profile_conflicts")
            .delete()
            .eq("profile_id", str(profile_id)),
            operation="delete_conflicts_for_profile",
        )
        return len(data or [])

    def _profile_conflict_payload(
        self,
        *,
        profile_id: UUID | str,
        conflict: Any,
    ) -> dict[str, Any]:
        source_values = self._conflict_value(conflict, "source_values")
        if source_values is None:
            source_values = self._fallback_conflict_source_values(conflict)

        return {
            "profile_id": str(profile_id),
            "field_name": self._safe_text(
                self._conflict_value(conflict, "field_name"),
                default="unknown",
            ),
            "severity": self._safe_conflict_severity(
                self._conflict_value(conflict, "severity"),
            ),
            "impact": self._safe_float(
                self._conflict_value(conflict, "impact"),
                default=0.0,
            ),
            "source_values": self._safe_json_object(source_values),
            "explanation": self._safe_text(
                self._conflict_value(conflict, "explanation"),
                default="Conflict detected during resolution.",
            ),
        }

    def _normalize_profile_conflict_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        clean_payload = dict(payload)
        profile_id = self._profile_conflict_profile_id(clean_payload)

        if not profile_id:
            logger.warning(
                "profile_conflict payload did not include profile_id/canonical_profile_id. "
                "Payload keys: %s",
                sorted(clean_payload.keys()),
            )

        return {
            "profile_id": profile_id,
            "field_name": self._safe_text(clean_payload.get("field_name"), default="unknown"),
            "severity": self._safe_conflict_severity(clean_payload.get("severity")),
            "impact": self._safe_float(clean_payload.get("impact"), default=0.0),
            "source_values": self._safe_json_object(clean_payload.get("source_values")),
            "explanation": self._safe_text(
                clean_payload.get("explanation"),
                default="Conflict detected during resolution.",
            ),
        }

    def _profile_conflict_profile_id(self, payload: dict[str, Any]) -> str:
        """Return the canonical profile UUID from known payload aliases.

        Older call sites may pass canonical_profile_id instead of profile_id. Sending
        an empty string to a uuid column produces a PostgREST 400, so this method
        makes the mapping explicit and logs missing IDs before insert.
        """
        value = (
            payload.get("profile_id")
            or payload.get("canonical_profile_id")
            or payload.get("canonical_profile_uuid")
        )

        if value is None:
            return ""

        return self._safe_text(value, default="")

    def _log_profile_conflict_rows(
        self,
        rows: list[dict[str, Any]],
        *,
        operation: str,
    ) -> None:
        logger.info(
            "Inserting profile_conflicts rows via %s: %s",
            operation,
            [
                {
                    "profile_id": row.get("profile_id"),
                    "field_name": row.get("field_name"),
                    "severity": row.get("severity"),
                    "impact": row.get("impact"),
                    "has_source_values": bool(row.get("source_values")),
                    "has_explanation": bool(row.get("explanation")),
                }
                for row in rows
            ],
        )

    def _fallback_conflict_source_values(self, conflict: Any) -> dict[str, Any]:
        values = {
            "source_a": self._conflict_value(conflict, "source_a"),
            "source_b": self._conflict_value(conflict, "source_b"),
            "field_value_a": self._conflict_value(conflict, "field_value_a"),
            "field_value_b": self._conflict_value(conflict, "field_value_b"),
            "value_a": self._conflict_value(conflict, "value_a"),
            "value_b": self._conflict_value(conflict, "value_b"),
        }
        return {
            key: self._json_safe(value)
            for key, value in values.items()
            if value is not None
        }

    def _conflict_value(self, conflict: Any, field_name: str) -> Any:
        if conflict is None:
            return None

        if isinstance(conflict, dict):
            return conflict.get(field_name)

        if hasattr(conflict, "model_dump"):
            try:
                dumped = conflict.model_dump()
            except TypeError:
                dumped = conflict.model_dump(mode="python")
            if isinstance(dumped, dict) and field_name in dumped:
                return dumped.get(field_name)

        return getattr(conflict, field_name, None)

    def _safe_conflict_severity(self, value: Any) -> str:
        normalized = self._enum_or_string_value(value).strip().lower()

        if normalized in VALID_CONFLICT_SEVERITIES:
            return normalized

        if normalized in {"critical", "blocking", "severe"}:
            return "high"

        if normalized in {"moderate", "review", "warning"}:
            return "medium"

        if normalized in {"minor", "informational", "info", "none", ""}:
            return "low"

        return "low"

    def _safe_json_object(self, value: Any) -> dict[str, Any]:
        safe_value = self._json_safe(value)

        if isinstance(safe_value, dict):
            return safe_value

        if safe_value is None:
            return {}

        if isinstance(safe_value, list):
            return {"values": safe_value}

        return {"value": safe_value}

    def _safe_text(self, value: Any, *, default: str) -> str:
        text = self._enum_or_string_value(value).strip()
        return text or default

    def _insert_profile_source_links_with_fallback(
        self,
        payloads: list[dict[str, Any]],
    ) -> Any:
        current_payloads = payloads
        removed_columns: set[str] = set()
        retried_uncertain_insert = False

        while True:
            logger.info(
                "Inserting profile_source_links rows: %s",
                [
                    {
                        "decision": row.get("decision"),
                        "relationship_type": row.get("relationship_type"),
                        "verification_status": row.get("verification_status"),
                        "confidence_score": row.get("confidence_score"),
                        "has_decision_payload": "decision_payload" in row,
                    }
                    for row in current_payloads
                ],
            )

            try:
                data = self._execute(
                    self.client.table("profile_source_links").insert(current_payloads),
                    operation="insert_profile_source_links_for_classifications",
                )
                if isinstance(data, list) and data:
                    return data

                recovered = self._read_profile_source_links_after_uncertain_insert(current_payloads)
                if self._has_all_profile_source_links(current_payloads, recovered):
                    logger.warning(
                        "profile_source_links insert returned no rows, but rows were found on readback."
                    )
                    return recovered

                raise StorageError(
                    "Database operation returned no rows: insert_profile_source_links_for_classifications",
                    details={
                        "table": "profile_source_links",
                        "operation": "insert_profile_source_links_for_classifications",
                    },
                )
            except StorageError as exc:
                if (
                    not retried_uncertain_insert
                    and self._is_uncertain_profile_source_links_insert_error(exc)
                ):
                    recovered = self._read_profile_source_links_after_uncertain_insert(current_payloads)
                    if self._has_all_profile_source_links(current_payloads, recovered):
                        logger.warning(
                            "profile_source_links insert response was lost, but rows were found on readback."
                        )
                        return recovered

                    retried_uncertain_insert = True
                    logger.warning(
                        "profile_source_links insert disconnected before response; retrying once after empty readback."
                    )
                    continue

                missing_column = self._missing_profile_source_links_column(exc)
                if missing_column is None or missing_column in removed_columns:
                    logger.exception(
                        "profile_source_links insert failed after normalization. "
                        "Rows attempted: %s",
                        [
                            {
                                "decision": row.get("decision"),
                                "relationship_type": row.get("relationship_type"),
                                "verification_status": row.get("verification_status"),
                                "confidence_score": row.get("confidence_score"),
                                "has_decision_payload": "decision_payload" in row,
                            }
                            for row in current_payloads
                        ],
                    )
                    raise

                removed_columns.add(missing_column)
                logger.warning(
                    "profile_source_links column %s appears unavailable; retrying insert without it.",
                    missing_column,
                )
                current_payloads = [
                    {
                        key: value
                        for key, value in payload.items()
                        if key != missing_column
                    }
                    for payload in current_payloads
                ]

    def _is_uncertain_profile_source_links_insert_error(self, exc: StorageError) -> bool:
        error = str(exc.internal_details.get("error", "")).lower()
        return any(
            marker in error
            for marker in (
                "server disconnected",
                "remoteprotocolerror",
                "connection reset",
                "connection aborted",
                "networkerror",
            )
        )

    def _read_profile_source_links_after_uncertain_insert(
        self,
        payloads: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        profile_ids = {str(row.get("profile_id")) for row in payloads if row.get("profile_id")}
        if len(profile_ids) != 1:
            return []

        profile_id = next(iter(profile_ids))
        expected_source_ids = {
            str(row.get("source_account_id"))
            for row in payloads
            if row.get("source_account_id")
        }
        if not expected_source_ids:
            return []

        try:
            data = self._execute(
                self.client.table("profile_source_links")
                .select("*")
                .eq("profile_id", profile_id),
                operation="read_profile_source_links_after_uncertain_insert",
            )
        except StorageError:
            logger.exception(
                "Failed to read back profile_source_links after uncertain insert."
            )
            return []

        if not isinstance(data, list):
            return []

        return [
            row
            for row in data
            if str(row.get("source_account_id")) in expected_source_ids
        ]

    def _has_all_profile_source_links(
        self,
        payloads: list[dict[str, Any]],
        rows: list[dict[str, Any]],
    ) -> bool:
        expected = {
            str(row.get("source_account_id"))
            for row in payloads
            if row.get("source_account_id")
        }
        found = {
            str(row.get("source_account_id"))
            for row in rows
            if row.get("source_account_id")
        }
        return bool(expected) and expected <= found
    def _source_link_payload(
        self,
        *,
        target_profile_id: UUID | str,
        item: AccountClassification,
        review_outcome_by_key: dict[str, Any],
    ) -> dict[str, Any]:
        review_outcome = review_outcome_by_key.get(item.source_account_key)
        original_decision = self._decision_value(item)
        original_relationship_type = self._relationship_type_for_decision(item)
        original_verification_status = self._verification_status_for_decision(item)

        link_fields = final_link_fields_after_review(
            original_decision=original_decision,
            original_relationship_type=original_relationship_type,
            original_verification_status=original_verification_status,
            original_confidence_score=self._deterministic_link_confidence(item),
            outcome=review_outcome,
        )

        decision_payload = merge_llm_review_into_decision_payload(
            self._decision_payload(item),
            review_outcome,
        )
        if not isinstance(decision_payload, dict):
            decision_payload = {}

        decision_payload = self._json_safe(decision_payload)

        decision = self._normalize_match_decision(
            link_fields.get("decision") or original_decision
        )
        relationship_type = self._normalize_relationship_type(
            link_fields.get("relationship_type") or original_relationship_type,
            decision,
        )
        verification_status = self._normalize_verification_status(
            link_fields.get("verification_status") or original_verification_status,
            decision,
            decision_payload=decision_payload,
            is_anchor=self._is_anchor_classification(item),
        )

        return {
            "profile_id": str(target_profile_id),
            "source_account_id": str(item.source_account_id),
            "confidence_score": self._safe_float(
                link_fields.get("confidence_score"),
                default=self._deterministic_link_confidence(item),
            ),
            "decision": decision,
            "relationship_type": relationship_type,
            "verification_status": verification_status,
            "positive_signal_count": self._safe_len(item.independent_positive_groups),
            "negative_signal_count": self._safe_len(item.conflict_types),
            "has_high_conflict": bool(item.blocking_conflict_types),
            "decision_payload": decision_payload,
        }

    def _deterministic_link_confidence(self, item: AccountClassification) -> float:
        return self._safe_float(getattr(item, "decision_confidence_score", 0.0), default=0.0)

    def _relationship_type_for_decision(self, item: AccountClassification) -> str:
        decision = self._decision_value(item)
        decision_payload = self._decision_payload(item)

        raw_value = (
            getattr(item, "relationship_type", None)
            or decision_payload.get("relationship_type")
            or decision_payload.get("source_relationship_type")
        )

        return self._normalize_relationship_type(
            raw_value,
            decision,
            is_anchor=self._is_anchor_classification(item),
        )

    def _verification_status_for_decision(self, item: AccountClassification) -> str:
        decision = self._decision_value(item)
        decision_payload = self._decision_payload(item)

        raw_value = (
            getattr(item, "verification_status", None)
            or decision_payload.get("verification_status")
        )

        return self._normalize_verification_status(
            raw_value,
            decision,
            decision_payload=decision_payload,
            is_anchor=self._is_anchor_classification(item),
        )

    def _decision_payload(self, item: AccountClassification) -> dict[str, Any]:
        return self._json_safe(
            {
                "decision_basis": self._enum_or_string_value(item.decision_basis),
                "risk_level": self._enum_or_string_value(item.risk_level),
                "rationale": item.rationale,
                "evidence_confidence_score": item.evidence_confidence_score,
                "decision_confidence_score": item.decision_confidence_score,
                "account_score": item.account_score,
                "best_pair_score": item.best_pair_score,
                "is_anchor": item.is_anchor,
                "accepted_as_anchor": item.accepted_as_anchor,
                "best_anchor_account_key": item.best_anchor_account_key,
                "best_pair_key": item.best_pair_key,
                "independent_positive_groups": item.independent_positive_groups,
                "strong_positive_groups": item.strong_positive_groups,
                "weak_positive_groups": item.weak_positive_groups,
                "weak_signal_only": item.weak_signal_only,
                "hn_conservative": item.hn_conservative,
                "hn_requires_strong_evidence": item.hn_requires_strong_evidence,
                "conflict_types": item.conflict_types,
                "blocking_conflict_types": item.blocking_conflict_types,
                "metadata": item.metadata,
            }
        )

    def _normalize_profile_source_link_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        clean_payload = dict(payload)
        decision_payload = clean_payload.get("decision_payload")
        if not isinstance(decision_payload, dict):
            decision_payload = {}

        decision_payload = self._json_safe(decision_payload)

        decision = self._normalize_match_decision(clean_payload.get("decision"))
        is_anchor = bool(
            decision_payload.get("is_anchor")
            or decision_payload.get("accepted_as_anchor")
            or decision_payload.get("decision_basis") == "anchor_input"
        )

        clean_payload["decision"] = decision
        clean_payload["relationship_type"] = self._normalize_relationship_type(
            clean_payload.get("relationship_type"),
            decision,
            is_anchor=is_anchor,
        )
        clean_payload["verification_status"] = self._normalize_verification_status(
            clean_payload.get("verification_status"),
            decision,
            decision_payload=decision_payload,
            is_anchor=is_anchor,
        )
        clean_payload["decision_payload"] = decision_payload
        clean_payload["confidence_score"] = self._safe_float(
            clean_payload.get("confidence_score"),
            default=0.0,
        )
        clean_payload["positive_signal_count"] = int(clean_payload.get("positive_signal_count") or 0)
        clean_payload["negative_signal_count"] = int(clean_payload.get("negative_signal_count") or 0)
        clean_payload["has_high_conflict"] = bool(clean_payload.get("has_high_conflict"))

        return clean_payload

    def _normalize_match_decision(self, value: Any) -> str:
        normalized = self._enum_or_string_value(value).strip().lower()

        if normalized in VALID_MATCH_DECISIONS:
            return normalized

        if normalized in {"rejected", "likely_different_person", "different_person"}:
            return MatchDecision.REJECT.value

        if normalized in {"accepted", "match", "matched", "likely_same_person"}:
            return MatchDecision.AUTO_MATCH.value

        return MatchDecision.NEEDS_REVIEW.value

    def _normalize_relationship_type(
        self,
        value: Any,
        decision: str | None = None,
        *,
        is_anchor: bool = False,
    ) -> str:
        normalized = self._enum_or_string_value(value).strip().lower()
        decision_value = self._normalize_match_decision(decision)

        if normalized in VALID_PROFILE_SOURCE_RELATIONSHIP_TYPES:
            return normalized

        if normalized in {"supporting", "support", "matched", "match"}:
            return SourceRelationshipType.SECONDARY.value

        if normalized in {"ambiguous", "review", "needs_review", "possible", "uncertain"}:
            return SourceRelationshipType.POSSIBLE_ALIAS.value

        if decision_value == MatchDecision.REJECT.value:
            return SourceRelationshipType.REJECTED.value

        if decision_value == MatchDecision.NEEDS_REVIEW.value:
            return SourceRelationshipType.POSSIBLE_ALIAS.value

        if is_anchor:
            return SourceRelationshipType.PRIMARY.value

        return SourceRelationshipType.SECONDARY.value

    def _normalize_verification_status(
        self,
        value: Any,
        decision: str | None = None,
        *,
        decision_payload: dict[str, Any] | None = None,
        is_anchor: bool = False,
    ) -> str:
        normalized = self._enum_or_string_value(value).strip().lower()
        decision_value = self._normalize_match_decision(decision)
        payload = decision_payload if isinstance(decision_payload, dict) else {}

        if normalized in VALID_PROFILE_SOURCE_VERIFICATION_STATUSES:
            return normalized

        if decision_value == MatchDecision.REJECT.value:
            return VerificationStatus.REJECTED.value

        if decision_value == MatchDecision.NEEDS_REVIEW.value:
            return VerificationStatus.NEEDS_REVIEW.value

        if self._has_reciprocal_profile_link(payload):
            return VerificationStatus.RECIPROCAL_LINK_VERIFIED.value

        if is_anchor:
            return VerificationStatus.CLAIMED_BY_INPUT.value

        if normalized in {"unverified", "unknown", "none", ""}:
            return VerificationStatus.LIKELY_SAME_PERSON.value

        return VerificationStatus.EVIDENCE_MATCHED.value

    def _decision_value(self, item: AccountClassification) -> str:
        return self._normalize_match_decision(getattr(item, "decision", None))

    def _is_anchor_classification(self, item: AccountClassification) -> bool:
        if bool(getattr(item, "is_anchor", False)):
            return True

        decision_payload = self._decision_payload(item)
        return bool(
            decision_payload.get("is_anchor")
            or decision_payload.get("accepted_as_anchor")
            or decision_payload.get("decision_basis") == "anchor_input"
        )

    def _has_reciprocal_profile_link(self, decision_payload: dict[str, Any]) -> bool:
        signal_groups = set()

        for field_name in ("strong_positive_groups", "used_evidence_types", "independent_positive_groups"):
            value = decision_payload.get(field_name)
            if isinstance(value, list):
                signal_groups.update(str(item) for item in value)

        return "reciprocal_profile_link" in signal_groups

    def _missing_profile_source_links_column(self, exc: StorageError) -> str | None:
        details = getattr(exc, "internal_details", None)
        if isinstance(details, dict):
            message = str(
                details.get("error")
                or details.get("message")
                or details.get("details")
                or ""
            ).lower()
        else:
            message = str(exc).lower()

        for column in sorted(self._PROFILE_SOURCE_LINK_FALLBACK_COLUMNS):
            if re.search(rf"\b{re.escape(column)}\b", message):
                return column
        return None

    def _enum_or_string_value(self, value: Any) -> str:
        if value is None:
            return ""

        if isinstance(value, Enum):
            return str(value.value)

        if hasattr(value, "value"):
            return str(value.value)

        return str(value)

    def _json_safe(self, value: Any) -> Any:
        if value is None:
            return None

        if isinstance(value, Enum):
            return value.value

        if hasattr(value, "value") and not isinstance(value, (str, bytes, int, float, bool)):
            return str(value.value)

        if isinstance(value, UUID):
            return str(value)

        if isinstance(value, datetime):
            return value.isoformat()

        if isinstance(value, date):
            return value.isoformat()

        if isinstance(value, Decimal):
            return float(value)

        if isinstance(value, dict):
            return {
                str(self._json_safe(key)): self._json_safe(item)
                for key, item in value.items()
            }

        if isinstance(value, (list, tuple, set)):
            return [self._json_safe(item) for item in value]

        if isinstance(value, (str, int, float, bool)):
            return value

        return str(value)

    def _safe_float(self, value: Any, *, default: float = 0.0) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return default

        if number != number or number in (float("inf"), float("-inf")):
            return default

        return number

    def _safe_len(self, value: Any) -> int:
        if isinstance(value, (list, tuple, set)):
            return len(value)
        return 0