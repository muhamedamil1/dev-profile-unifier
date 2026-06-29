from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from app.schemas.evidence import ExtractedEvidence
from app.schemas.resolution import MatchEvidence
from app.storage.base import BaseRepository


class EvidenceRepo(BaseRepository):
    table_name = "match_evidence"

    def insert_evidence(
        self,
        *,
        profile_source_link_id: str | UUID,
        evidence: MatchEvidence,
    ) -> dict:
        return self._insert_one(
            evidence.to_db_payload(profile_source_link_id=UUID(str(profile_source_link_id)))
        )

    def insert_many(
        self,
        *,
        profile_source_link_id: str | UUID,
        evidence_items: list[MatchEvidence],
    ) -> list[dict]:
        if not evidence_items:
            return []

        link_uuid = UUID(str(profile_source_link_id))
        payloads = [
            item.to_db_payload(profile_source_link_id=link_uuid)
            for item in evidence_items
        ]

        return self._insert_many(payloads)

    def list_by_profile_source_link(
        self,
        profile_source_link_id: str | UUID,
    ) -> list[dict]:
        data = self._execute(
            self.client.table(self.table_name)
            .select("*")
            .eq("profile_source_link_id", str(profile_source_link_id))
            .order("created_at", desc=False),
            operation="list_by_profile_source_link",
        )

        return data if isinstance(data, list) else []

    def list_by_link_ids(self, link_ids: list[str | UUID]) -> list[dict]:
        if not link_ids:
            return []

        data = self._execute(
            self.client.table(self.table_name)
            .select("*")
            .in_("profile_source_link_id", [str(item) for item in link_ids])
            .order("created_at", desc=False),
            operation="list_by_link_ids",
        )

        return data if isinstance(data, list) else []

    def delete_for_profile(self, profile_id: UUID | str) -> int:
        link_data = self._execute(
            self.client.table("profile_source_links")
            .select("id")
            .eq("profile_id", str(profile_id)),
            operation="list_profile_links_for_evidence_delete",
        )
        link_ids = [row["id"] for row in link_data or [] if row.get("id")]
        if not link_ids:
            return 0

        data = self._execute(
            self.client.table(self.table_name)
            .delete()
            .in_("profile_source_link_id", link_ids),
            operation="delete_match_evidence_for_profile",
        )
        return len(data or [])

    def insert_many_for_profile_links(
        self,
        *,
        evidence: list[ExtractedEvidence],
        source_link_by_account_id: dict[str, str | UUID],
    ) -> list[dict]:
        if not evidence:
            return []

        payloads: list[dict[str, Any]] = []
        for item in evidence:
            if item.source_account_id is None:
                continue

            source_account_id = str(item.source_account_id)
            profile_source_link_id = source_link_by_account_id.get(source_account_id)
            if profile_source_link_id is None:
                continue

            payloads.append(
                {
                    "profile_source_link_id": str(profile_source_link_id),
                    "source_account_a_id": source_account_id,
                    "source_account_b_id": str(item.target_account_id) if item.target_account_id else None,
                    "signal_type": item.evidence_type.value,
                    "direction": item.direction.value,
                    "signal_weight": self._signed_weight(item),
                    "source_a": item.source.value,
                    "source_b": item.target_source.value if item.target_source else None,
                    "field_name": self._field_name(item),
                    "field_value_a": self._field_value_a(item),
                    "field_value_b": self._field_value_b(item),
                    "explanation": item.reason,
                }
            )

        if not payloads:
            return []

        return self._insert_many(payloads)

    def _signed_weight(self, item: ExtractedEvidence) -> float:
        if item.direction.value == "negative":
            return -abs(item.weight)
        if item.direction.value == "neutral":
            return 0.0
        return abs(item.weight)

    def _field_name(self, item: ExtractedEvidence) -> str:
        return str(
            item.metadata.get("field_name")
            or item.metadata.get("independence_group")
            or item.evidence_type.value
        )

    def _field_value_a(self, item: ExtractedEvidence) -> str | None:
        value = (
            item.metadata.get("source_value")
            or item.metadata.get("request_value")
            or item.metadata.get("field_value_a")
            or item.source_account_key
        )
        return self._stringify_field_value(value)

    def _field_value_b(self, item: ExtractedEvidence) -> str | None:
        value = (
            item.metadata.get("target_value")
            or item.metadata.get("field_value_b")
            or item.target_account_key
        )
        return self._stringify_field_value(value)

    def _stringify_field_value(self, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, (dict, list)):
            return json.dumps(value, sort_keys=True, default=str)
        return str(value)
