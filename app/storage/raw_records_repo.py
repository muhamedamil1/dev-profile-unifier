from __future__ import annotations

from uuid import UUID

from app.schemas.source_account import RawSourceRecord
from app.storage.base import BaseRepository


class RawRecordsRepo(BaseRepository):
    table_name = "raw_source_records"

    def insert_record(self, record: RawSourceRecord) -> dict:
        return self._insert_one(record.to_db_payload())

    def insert_many_records(self, records: list[RawSourceRecord]) -> list[dict]:
        if not records:
            return []

        return self._insert_many([record.to_db_payload() for record in records])

    def list_by_run(self, resolution_run_id: str | UUID) -> list[dict]:
        data = self._execute(
            self.client.table(self.table_name)
            .select("*")
            .eq("resolution_run_id", str(resolution_run_id))
            .order("fetched_at", desc=True),
            operation="list_by_run",
        )
        return data if isinstance(data, list) else []

    def list_by_source(
        self,
        *,
        resolution_run_id: str | UUID,
        source: str,
    ) -> list[dict]:
        data = self._execute(
            self.client.table(self.table_name)
            .select("*")
            .eq("resolution_run_id", str(resolution_run_id))
            .eq("source", source)
            .order("fetched_at", desc=True),
            operation="list_by_source",
        )
        return data if isinstance(data, list) else []
