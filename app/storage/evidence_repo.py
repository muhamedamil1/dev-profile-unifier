from __future__ import annotations

from uuid import UUID

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
