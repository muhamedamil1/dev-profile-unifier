from __future__ import annotations

from uuid import UUID

from app.schemas.metrics import LLMUsageMetric
from app.storage.base import BaseRepository


class SummariesRepo(BaseRepository):
    table_name = "llm_summaries"

    def insert_summary(
        self,
        *,
        profile_id: str | UUID,
        summary: LLMUsageMetric,
    ) -> dict:
        return self._insert_one(
            summary.to_db_payload(profile_id=UUID(str(profile_id)))
        )

    def get_latest_for_profile(self, profile_id: str | UUID) -> dict | None:
        data = self._execute(
            self.client.table(self.table_name)
            .select("*")
            .eq("profile_id", str(profile_id))
            .order("created_at", desc=True)
            .limit(1),
            operation="get_latest_for_profile",
        )

        return self._first_or_none(data)

    def list_by_profile(self, profile_id: str | UUID) -> list[dict]:
        data = self._execute(
            self.client.table(self.table_name)
            .select("*")
            .eq("profile_id", str(profile_id))
            .order("created_at", desc=True),
            operation="list_by_profile",
        )

        return data if isinstance(data, list) else []
