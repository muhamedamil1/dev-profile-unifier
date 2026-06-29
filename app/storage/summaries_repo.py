from __future__ import annotations

from typing import Any
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

    def list_by_profile(self, profile_id: str | UUID) -> list[dict]:
        data = self._execute(
            self.client.table(self.table_name)
            .select("*")
            .eq("profile_id", str(profile_id))
            .order("created_at", desc=True),
            operation="list_by_profile",
        )

        return data if isinstance(data, list) else []

    def get_latest_for_profile(self, profile_id: UUID | str) -> dict | None:
        data = self._execute(
            self.client.table(self.table_name)
            .select("*")
            .eq("profile_id", str(profile_id))
            .order("created_at", desc=True)
            .limit(1),
            operation="get_latest_llm_summary_for_profile",
        )
        return self._first_or_none(data)

    def get_latest_by_profile_id(self, *, profile_id: UUID | str) -> dict | None:
        """
        Phase 10 fallback reader.

        GET /profiles/{id} primarily reads the AI summary from
        canonical_profiles.profile_payload.phase_9_summary. This method is a
        DB fallback for the latest saved llm_summaries row.
        """
        return self.get_latest_for_profile(profile_id)

    def delete_by_profile_and_prompt_version(
        self,
        *,
        profile_id: UUID | str,
        prompt_version: str,
        model: str | None = None,
    ) -> int:
        query = (
            self.client.table(self.table_name)
            .delete()
            .eq("profile_id", str(profile_id))
            .eq("prompt_version", prompt_version)
        )

        if model:
            query = query.eq("model", model)

        data = self._execute(
            query,
            operation="delete_llm_summaries_by_profile_prompt_version",
        )
        return len(data or [])

    def create_summary(
        self,
        *,
        profile_id: UUID | str,
        model: str,
        prompt_version: str,
        prompt_text: str,
        summary: str,
        input_tokens: int,
        output_tokens: int,
        estimated_cost_usd: float,
    ) -> dict:
        payload = {
            "profile_id": str(profile_id),
            "model": model,
            "prompt_version": prompt_version,
            "prompt_text": prompt_text,
            "summary": summary,
            "input_tokens": int(input_tokens or 0),
            "output_tokens": int(output_tokens or 0),
            "estimated_cost_usd": float(estimated_cost_usd or 0.0),
        }
        return self._insert_one(payload)