from __future__ import annotations

from uuid import UUID

from app.schemas.source_account import SourceAccount
from app.storage.base import BaseRepository


class SourceAccountsRepo(BaseRepository):
    table_name = "source_accounts"

    def upsert_account(self, account: SourceAccount) -> dict:
        payload = account.to_db_payload()

        data = self._execute(
            self.client.table(self.table_name).upsert(
                payload,
                on_conflict="source_account_key",
            ),
            operation="upsert_account",
        )

        return self._require_one(data, operation="upsert_account")

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
