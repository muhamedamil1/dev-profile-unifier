from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any
from uuid import UUID

from supabase import Client

from app.utils.errors import StorageError


class BaseRepository:
    """
    Base class for Supabase repositories.

    Repositories are intentionally thin:
    - serialize Python/Pydantic values into JSON-safe payloads
    - execute Supabase queries
    - wrap database failures in StorageError
    """

    table_name: str

    def __init__(self, client: Client) -> None:
        self.client = client

    def _serialize_value(self, value: Any) -> Any:
        if isinstance(value, Enum):
            return value.value

        if isinstance(value, UUID):
            return str(value)

        if isinstance(value, (datetime, date)):
            return value.isoformat()

        if isinstance(value, list):
            return [self._serialize_value(item) for item in value]

        if isinstance(value, dict):
            return {
                str(key): self._serialize_value(item)
                for key, item in value.items()
            }

        return value

    def _serialize_payload(
        self,
        payload: dict[str, Any],
        *,
        strip_none: bool = True,
    ) -> dict[str, Any]:
        serialized = {
            key: self._serialize_value(value)
            for key, value in payload.items()
        }

        if strip_none:
            serialized = {
                key: value
                for key, value in serialized.items()
                if value is not None
            }

        return serialized

    def _execute(self, query: Any, *, operation: str) -> Any:
        try:
            response = query.execute()
        except Exception as exc:
            raise StorageError(
                "Database operation failed.",
                details={
                    "table": self.table_name,
                    "operation": operation,
                },
                internal_details={
                    "table": self.table_name,
                    "operation": operation,
                    "error": str(exc),
                },
            ) from exc

        return getattr(response, "data", None)

    def _first_or_none(self, data: Any) -> dict[str, Any] | None:
        if data is None:
            return None

        if isinstance(data, list):
            if not data:
                return None
            first = data[0]
            if isinstance(first, dict):
                return first
            return None

        if isinstance(data, dict):
            return data

        return None

    def _require_one(self, data: Any, *, operation: str) -> dict[str, Any]:
        row = self._first_or_none(data)
        if row is None:
            raise StorageError(
                f"Database operation returned no rows: {operation}",
                details={
                    "table": self.table_name,
                    "operation": operation,
                },
            )
        return row

    def _insert_one(self, payload: dict[str, Any]) -> dict[str, Any]:
        clean_payload = self._serialize_payload(payload)
        data = self._execute(
            self.client.table(self.table_name).insert(clean_payload),
            operation="insert_one",
        )
        return self._require_one(data, operation="insert_one")

    def _insert_many(self, payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not payloads:
            return []

        clean_payloads = [
            self._serialize_payload(payload)
            for payload in payloads
        ]

        data = self._execute(
            self.client.table(self.table_name).insert(clean_payloads),
            operation="insert_many",
        )

        if isinstance(data, list):
            return data

        return []

    def _update_by_id(
        self,
        row_id: str | UUID,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        clean_payload = self._serialize_payload(payload)
        data = self._execute(
            self.client.table(self.table_name)
            .update(clean_payload)
            .eq("id", str(row_id)),
            operation="update_by_id",
        )
        return self._require_one(data, operation="update_by_id")

    def _get_by_id(self, row_id: str | UUID) -> dict[str, Any] | None:
        data = self._execute(
            self.client.table(self.table_name)
            .select("*")
            .eq("id", str(row_id))
            .limit(1),
            operation="get_by_id",
        )
        return self._first_or_none(data)
