from __future__ import annotations

from typing import Any
from uuid import UUID

from app.schemas.enums import PlatformSource
from app.storage.base import BaseRepository
from app.utils.errors import StorageError


class FactsRepo(BaseRepository):
    table_name = "profile_facts"

    def upsert_fact(
        self,
        *,
        profile_id: str | UUID,
        source: PlatformSource | str,
        fact_type: str,
        value: str,
        source_account_id: str | UUID | None = None,
        raw_source_record_id: str | UUID | None = None,
        confidence: float = 1.0,
        metadata: dict[str, Any] | None = None,
    ) -> dict:
        payload = self._serialize_payload(
            {
                "profile_id": profile_id,
                "source_account_id": source_account_id,
                "raw_source_record_id": raw_source_record_id,
                "source": source,
                "fact_type": fact_type,
                "value": value,
                "confidence": confidence,
                "metadata": metadata or {},
            }
        )

        data = self._execute(
            self.client.table(self.table_name).upsert(
                payload,
                on_conflict="profile_id,source,fact_type,value",
            ),
            operation="upsert_fact",
        )

        row = self._first_or_none(data)
        if row is not None:
            return row

        recovered = self.get_by_unique_fact(
            profile_id=profile_id,
            source=source,
            fact_type=fact_type,
            value=value,
        )
        if recovered is not None:
            return recovered

        raise StorageError(
            "Database operation returned no rows: upsert_fact",
            details={
                "table": self.table_name,
                "operation": "upsert_fact",
                "profile_id": str(profile_id),
                "source": str(source.value if hasattr(source, "value") else source),
                "fact_type": fact_type,
                "value": value,
            },
        )

    def upsert_many(self, facts: list[dict[str, Any]]) -> list[dict]:
        if not facts:
            return []

        payloads = [
            self._serialize_payload(
                {
                    **fact,
                    "metadata": fact.get("metadata") or {},
                }
            )
            for fact in facts
        ]

        data = self._execute(
            self.client.table(self.table_name).upsert(
                payloads,
                on_conflict="profile_id,source,fact_type,value",
            ),
            operation="upsert_many",
        )

        return data if isinstance(data, list) else []


    def get_by_unique_fact(
        self,
        *,
        profile_id: str | UUID,
        source: PlatformSource | str,
        fact_type: str,
        value: str,
    ) -> dict | None:
        source_value = source.value if isinstance(source, PlatformSource) else str(source)
        data = self._execute(
            self.client.table(self.table_name)
            .select("*")
            .eq("profile_id", str(profile_id))
            .eq("source", source_value)
            .eq("fact_type", fact_type)
            .eq("value", value)
            .limit(1),
            operation="get_by_unique_fact",
        )
        return self._first_or_none(data)
    def list_by_profile(self, profile_id: str | UUID) -> list[dict]:
        data = self._execute(
            self.client.table(self.table_name)
            .select("*")
            .eq("profile_id", str(profile_id))
            .order("fact_type", desc=False)
            .order("value", desc=False),
            operation="list_by_profile",
        )

        return data if isinstance(data, list) else []
