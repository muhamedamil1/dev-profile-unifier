from __future__ import annotations

from uuid import UUID

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
