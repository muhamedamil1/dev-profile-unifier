from __future__ import annotations

from uuid import UUID

from app.schemas.source_account import SourceAccount
from app.storage.base import BaseRepository
from app.utils.errors import StorageError


class SourceAccountsRepo(BaseRepository):
    table_name = "source_accounts"

    def upsert_account(self, account: SourceAccount) -> dict:
        payload = self._serialize_payload(account.to_db_payload())

        data = self._execute(
            self.client.table(self.table_name).upsert(
                payload,
                on_conflict="source_account_key",
            ),
            operation="upsert_account",
        )

        row = self._first_or_none(data)
        if row is not None:
            return row

        recovered = self.get_by_key(str(payload["source_account_key"]))
        if recovered is not None:
            return recovered

        raise StorageError(
            "Database operation returned no rows: upsert_account",
            details={
                "table": self.table_name,
                "operation": "upsert_account",
                "source_account_key": payload.get("source_account_key"),
            },
        )

    def get_by_id(self, account_id: str | UUID) -> dict | None:
        return self._get_by_id(account_id)

    def get_by_key(self, source_account_key: str) -> dict | None:
        data = self._execute(
            self.client.table(self.table_name)
            .select("*")
            .eq("source_account_key", source_account_key.lower())
            .limit(1),
            operation="get_by_key",
        )
        return self._first_or_none(data)

    def list_by_ids(self, account_ids: list[str | UUID]) -> list[dict]:
        if not account_ids:
            return []

        data = self._execute(
            self.client.table(self.table_name)
            .select("*")
            .in_("id", [str(item) for item in account_ids]),
            operation="list_by_ids",
        )

        return data if isinstance(data, list) else []

    def list_by_source_and_handle(
        self,
        *,
        source: str,
        handle: str,
    ) -> list[dict]:
        data = self._execute(
            self.client.table(self.table_name)
            .select("*")
            .eq("source", source)
            .eq("handle", handle),
            operation="list_by_source_and_handle",
        )
        return data if isinstance(data, list) else []
    def list_by_ids(self, source_account_ids: list[UUID | str]) -> list[dict]:
        ids = [str(item) for item in source_account_ids if item]

        if not ids:
            return []

        return self._execute(
            self.client.table(self.table_name)
            .select("*")
            .in_("id", ids),
            operation="list_source_accounts_by_ids",
        )