from __future__ import annotations

import logging
import math
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from app.schemas.conflicts import DetectedConflict
from app.schemas.resolution import ConflictRecord
from app.storage.base import BaseRepository
from app.utils.errors import StorageError

logger = logging.getLogger(__name__)

VALID_CONFLICT_SEVERITIES = {"low", "medium", "high"}


class ConflictsRepo(BaseRepository):
    table_name = "profile_conflicts"

    def insert_conflict(
        self,
        *,
        profile_id: str | UUID,
        conflict: ConflictRecord,
    ) -> dict:
        raw_payload = conflict.to_db_payload(profile_id=UUID(str(profile_id)))
        payload = self._normalize_conflict_payload(raw_payload, profile_id=profile_id)

        logger.info(
            "Inserting profile_conflicts rows via insert_conflict: %s",
            [self._loggable_conflict_row(payload)],
        )

        try:
            data = self._execute(
                self.client.table(self.table_name).insert(payload),
                operation="insert_conflict",
            )
        except StorageError as exc:
            logger.exception(
                "profile_conflicts insert failed via insert_conflict. "
                "Rows attempted: %s | storage_details=%s",
                [self._loggable_conflict_row(payload)],
                getattr(exc, "internal_details", None),
            )
            raise

        return self._require_one(data, operation="insert_conflict")

    def insert_many(
        self,
        *,
        profile_id: str | UUID,
        conflicts: list[ConflictRecord],
    ) -> list[dict]:
        if not conflicts:
            return []

        profile_uuid = UUID(str(profile_id))
        payloads = [
            self._normalize_conflict_payload(
                item.to_db_payload(profile_id=profile_uuid),
                profile_id=profile_uuid,
            )
            for item in conflicts
        ]

        return self._insert_conflict_payloads(
            payloads,
            operation="insert_many_profile_conflicts",
        )

    def list_by_profile(self, profile_id: str | UUID) -> list[dict]:
        data = self._execute(
            self.client.table(self.table_name)
            .select("*")
            .eq("profile_id", str(profile_id))
            .order("created_at", desc=False),
            operation="list_by_profile",
        )

        return data if isinstance(data, list) else []

    def delete_for_profile(self, profile_id: UUID | str) -> int:
        data = self._execute(
            self.client.table(self.table_name)
            .delete()
            .eq("profile_id", str(profile_id)),
            operation="delete_profile_conflicts_for_profile",
        )
        return len(data or [])

    def insert_many_for_profile(
        self,
        *,
        profile_id: UUID | str,
        conflicts: list[DetectedConflict],
    ) -> list[dict]:
        if not conflicts:
            return []

        payloads: list[dict[str, Any]] = []
        for item in conflicts:
            if item.source_account_id is None or item.target_account_id is None:
                raise ValueError("Conflict is missing persisted source account IDs.")

            metadata = self._json_object(getattr(item, "metadata", None))
            conflict_type = self._enum_or_string_value(getattr(item, "conflict_type", None))
            source = self._enum_or_string_value(getattr(item, "source", None))
            target_source = self._enum_or_string_value(getattr(item, "target_source", None))

            raw_payload = {
                "profile_id": str(profile_id),
                "field_name": metadata.get("conflict_basis") or conflict_type,
                "severity": getattr(item, "severity", None),
                "impact": getattr(item, "penalty", None),
                "source_values": self._source_values_for_detected_conflict(
                    item=item,
                    metadata=metadata,
                    conflict_type=conflict_type,
                    source=source,
                    target_source=target_source,
                ),
                "explanation": getattr(item, "description", None),
            }
            payloads.append(
                self._normalize_conflict_payload(raw_payload, profile_id=profile_id)
            )

        return self._insert_conflict_payloads(
            payloads,
            operation="insert_many_for_profile",
        )

    def _insert_conflict_payloads(
        self,
        payloads: list[dict[str, Any]],
        *,
        operation: str,
    ) -> list[dict]:
        if not payloads:
            return []

        clean_payloads = [
            self._normalize_conflict_payload(payload)
            for payload in payloads
        ]

        logger.info(
            "Inserting profile_conflicts rows via %s: %s",
            operation,
            [self._loggable_conflict_row(row) for row in clean_payloads],
        )

        try:
            data = self._execute(
                self.client.table(self.table_name).insert(clean_payloads),
                operation=operation,
            )
        except StorageError as exc:
            logger.exception(
                "profile_conflicts insert failed via %s. "
                "Rows attempted: %s | storage_details=%s",
                operation,
                [self._loggable_conflict_row(row) for row in clean_payloads],
                getattr(exc, "internal_details", None),
            )
            raise

        return data if isinstance(data, list) else []

    def _normalize_conflict_payload(
        self,
        payload: dict[str, Any],
        *,
        profile_id: str | UUID | None = None,
    ) -> dict[str, Any]:
        """
        Convert conflict payloads into the exact profile_conflicts DB shape.

        This emits only the columns that exist in the Supabase table:
        profile_id, field_name, severity, impact, source_values, explanation.

        Important:
        The live DB rejected a JSON object for source_values with the
        profile_conflicts_source_values_check constraint, so this repository always
        stores source_values as a JSON array. Each array element represents one
        side of the conflict or one preserved conflict value.
        """
        source = payload if isinstance(payload, dict) else {}

        resolved_profile_id = (
            profile_id
            or source.get("profile_id")
            or source.get("canonical_profile_id")
        )

        if resolved_profile_id is None:
            raise ValueError("profile_conflicts payload is missing profile_id.")

        raw_source_values = source.get("source_values")
        if raw_source_values is None:
            raw_source_values = source.get("source_value")
        if raw_source_values is None:
            raw_source_values = source.get("values")

        return {
            "profile_id": self._safe_uuid_string(resolved_profile_id),
            "field_name": self._safe_text(
                source.get("field_name")
                or source.get("conflict_basis")
                or source.get("conflict_type"),
                default="unknown",
            ),
            "severity": self._safe_conflict_severity(source.get("severity")),
            "impact": self._safe_conflict_impact(
                source.get("impact", source.get("penalty"))
            ),
            "source_values": self._json_array(raw_source_values),
            "explanation": self._safe_text(
                source.get("explanation") or source.get("description"),
                default="Conflict detected during profile resolution.",
            ),
        }

    def _source_values_for_detected_conflict(
        self,
        *,
        item: DetectedConflict,
        metadata: dict[str, Any],
        conflict_type: str,
        source: str,
        target_source: str,
    ) -> list[dict[str, Any]]:
        """
        Store conflict sides as an array to satisfy the profile_conflicts.source_values
        database check constraint and preserve the two-account comparison shape.
        """
        source_value = self._first_metadata_value(
            metadata,
            [
                "source_value",
                "left_value",
                "value_a",
                "field_value_a",
                "source_field_value",
                "left_location",
                "left_topic",
                "left_topics",
                "left_website",
                "left_name",
                "left_email",
            ],
        )
        target_value = self._first_metadata_value(
            metadata,
            [
                "target_value",
                "right_value",
                "value_b",
                "field_value_b",
                "target_field_value",
                "right_location",
                "right_topic",
                "right_topics",
                "right_website",
                "right_name",
                "right_email",
            ],
        )

        return [
            {
                "side": "source",
                "source": source,
                "source_account_key": getattr(item, "source_account_key", None),
                "source_account_id": str(item.source_account_id),
                "conflict_type": conflict_type,
                "value": self._json_safe(source_value),
                "metadata": metadata,
            },
            {
                "side": "target",
                "source": target_source,
                "source_account_key": getattr(item, "target_account_key", None),
                "source_account_id": str(item.target_account_id),
                "conflict_type": conflict_type,
                "value": self._json_safe(target_value),
                "metadata": metadata,
            },
        ]

    def _first_metadata_value(
        self,
        metadata: dict[str, Any],
        keys: list[str],
    ) -> Any:
        for key in keys:
            if key in metadata and metadata[key] not in (None, ""):
                return metadata[key]
        return None

    def _safe_uuid_string(self, value: str | UUID) -> str:
        return str(UUID(str(value)))

    def _safe_conflict_severity(self, value: Any) -> str:
        raw = self._enum_or_string_value(value).strip().lower()

        if raw in VALID_CONFLICT_SEVERITIES:
            return raw

        if raw in {"critical", "blocking", "severe", "strong", "hard"}:
            return "high"

        if raw in {"moderate", "medium_conflict", "review", "needs_review"}:
            return "medium"

        return "low"

    def _safe_conflict_impact(self, value: Any) -> float:
        try:
            if value is None:
                return 0.0

            number = float(value)
            if not math.isfinite(number):
                return 0.0

            return number
        except (TypeError, ValueError):
            return 0.0

    def _json_array(self, value: Any) -> list[Any]:
        safe_value = self._json_safe(value)

        if isinstance(safe_value, list):
            return safe_value

        if safe_value is None:
            return []

        return [safe_value]

    def _json_object(self, value: Any) -> dict[str, Any]:
        safe_value = self._json_safe(value)

        if isinstance(safe_value, dict):
            return safe_value

        if safe_value is None:
            return {}

        return {"value": safe_value}

    def _safe_text(self, value: Any, *, default: str) -> str:
        text = self._enum_or_string_value(value).strip()
        return text or default

    def _enum_or_string_value(self, value: Any) -> str:
        if value is None:
            return ""

        if isinstance(value, Enum):
            return str(value.value)

        if hasattr(value, "value") and not isinstance(value, (str, bytes, int, float, bool)):
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

    def _loggable_conflict_row(self, row: dict[str, Any]) -> dict[str, Any]:
        source_values = row.get("source_values")
        source_value_keys: list[str] = []
        if isinstance(source_values, list):
            for item in source_values:
                if isinstance(item, dict):
                    source_value_keys.extend(str(key) for key in item.keys())

        return {
            "profile_id": row.get("profile_id"),
            "field_name": row.get("field_name"),
            "severity": row.get("severity"),
            "impact": row.get("impact"),
            "source_values_type": type(source_values).__name__,
            "source_values_count": len(source_values) if isinstance(source_values, list) else 0,
            "source_value_keys": sorted(set(source_value_keys)),
            "has_explanation": bool(row.get("explanation")),
        }
