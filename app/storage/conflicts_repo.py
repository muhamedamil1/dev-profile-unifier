from __future__ import annotations

from typing import Any
from uuid import UUID

from app.schemas.conflicts import DetectedConflict
from app.schemas.resolution import ConflictRecord
from app.storage.base import BaseRepository


class ConflictsRepo(BaseRepository):
    table_name = "profile_conflicts"

    def insert_conflict(
        self,
        *,
        profile_id: str | UUID,
        conflict: ConflictRecord,
    ) -> dict:
        return self._insert_one(
            conflict.to_db_payload(profile_id=UUID(str(profile_id)))
        )

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
            item.to_db_payload(profile_id=profile_uuid)
            for item in conflicts
        ]

        return self._insert_many(payloads)

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

            payloads.append(
                {
                    "profile_id": str(profile_id),
                    "field_name": item.metadata.get("conflict_basis") or item.conflict_type.value,
                    "severity": item.severity.value,
                    "impact": item.penalty,
                    "source_values": {
                        "conflict_type": item.conflict_type.value,
                        "source_account_key": item.source_account_key,
                        "target_account_key": item.target_account_key,
                        "source": item.source.value,
                        "target_source": item.target_source.value,
                        "source_account_id": str(item.source_account_id),
                        "target_account_id": str(item.target_account_id),
                        "metadata": item.metadata,
                    },
                    "explanation": item.description,
                }
            )

        return self._insert_many(payloads)
