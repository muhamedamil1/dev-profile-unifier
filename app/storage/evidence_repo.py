from __future__ import annotations

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

    def delete_by_run(self, resolution_run_id: UUID | str) -> int:
        data = self._execute(
            self.client.table(self.table_name)
            .delete()
            .eq("resolution_run_id", str(resolution_run_id)),
            operation="delete_match_evidence_by_run",
        )

        return len(data or [])

    def insert_many_for_run(
        self,
        *,
        resolution_run_id: UUID | str,
        evidence: list[ExtractedEvidence],
        profile_source_link_ids_by_account_id: dict[str, str | UUID] | None = None,
    ) -> list[dict]:
        if not evidence:
            return []

        link_ids_by_account = profile_source_link_ids_by_account_id or {}
        payloads = []
        for item in evidence:
            source_account_id = str(item.source_account_id) if item.source_account_id else None
            direction = item.direction.value
            signal_weight = item.weight
            if direction == "negative":
                signal_weight = -abs(signal_weight)
            elif direction == "neutral":
                signal_weight = 0.0

            payloads.append(
                {
                    "resolution_run_id": str(resolution_run_id),
                    "profile_source_link_id": (
                        str(link_ids_by_account[source_account_id])
                        if source_account_id and source_account_id in link_ids_by_account
                        else None
                    ),
                    "source_account_a_id": source_account_id,
                    "source_account_b_id": str(item.target_account_id) if item.target_account_id else None,
                    "signal_type": item.evidence_type.value,
                    "direction": direction,
                    "signal_weight": signal_weight,
                    "source_a": item.source.value,
                    "source_b": item.target_source.value if item.target_source else None,
                    "field_name": item.independence_group.value,
                    "field_value_a": item.source_account_key,
                    "field_value_b": item.target_account_key,
                    "explanation": item.reason,
                }
            )

        return self._insert_many(payloads)
