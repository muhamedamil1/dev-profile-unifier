from __future__ import annotations

import logging
import time
from datetime import date, datetime
from enum import Enum
from typing import Any
from uuid import UUID

from supabase import Client

from app.utils.errors import StorageError


logger = logging.getLogger(__name__)

TRANSIENT_STORAGE_ERROR_MARKERS = (
    "server disconnected",
    "remoteprotocolerror",
    "connection reset",
    "connection aborted",
    "connection closed",
    "connection refused",
    "connection timed out",
    "temporarily unavailable",
    "network is unreachable",
    "read timeout",
    "write timeout",
    "pool timeout",
    "eof",
)

TRANSIENT_STORAGE_ERROR_TYPES = {
    "connecterror",
    "connecttimeout",
    "networkerror",
    "pooltimeout",
    "readerror",
    "readtimeout",
    "remoteprotocolerror",
    "timeout",
    "timeoutexception",
    "writeerror",
    "writetimeout",
}

MAX_TRANSIENT_STORAGE_ATTEMPTS = 3
TRANSIENT_STORAGE_RETRY_DELAYS_SECONDS = (0.15, 0.45)


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
        max_attempts = (
            MAX_TRANSIENT_STORAGE_ATTEMPTS
            if self._operation_allows_transient_retry(operation)
            else 1
        )
        attempt = 1

        while True:
            try:
                response = query.execute()
                return getattr(response, "data", None)
            except Exception as exc:
                if attempt < max_attempts and self._is_transient_storage_exception(exc):
                    delay = TRANSIENT_STORAGE_RETRY_DELAYS_SECONDS[
                        min(attempt - 1, len(TRANSIENT_STORAGE_RETRY_DELAYS_SECONDS) - 1)
                    ]
                    logger.warning(
                        "Transient Supabase storage error; retrying operation.",
                        extra={
                            "table": self.table_name,
                            "operation": operation,
                            "attempt": attempt,
                            "max_attempts": max_attempts,
                            "error_type": type(exc).__name__,
                        },
                    )
                    time.sleep(delay)
                    attempt += 1
                    continue

                raise StorageError(
                    "Database operation failed.",
                    details={
                        "table": self.table_name,
                        "operation": operation,
                    },
                    internal_details={
                        "table": self.table_name,
                        "operation": operation,
                        "attempts": attempt,
                        "retryable_operation": self._operation_allows_transient_retry(operation),
                        "transient_error": self._is_transient_storage_exception(exc),
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    },
                ) from exc

    @staticmethod
    def _operation_allows_transient_retry(operation: str) -> bool:
        normalized = str(operation or "").strip().lower()
        if not normalized:
            return False

        if "insert" in normalized and "upsert" not in normalized:
            return False

        retryable_prefixes = (
            "delete",
            "get",
            "list",
            "merge",
            "read",
            "select",
            "update",
            "upsert",
        )
        return normalized.startswith(retryable_prefixes) or any(
            token in normalized
            for token in (
                "_delete",
                "_get",
                "_list",
                "_read",
                "_update",
                "_upsert",
            )
        )

    @staticmethod
    def _is_transient_storage_exception(exc: Exception) -> bool:
        exc_type = type(exc).__name__.lower()
        if exc_type in TRANSIENT_STORAGE_ERROR_TYPES:
            return True

        text_parts = [str(exc).lower()]
        cause = getattr(exc, "__cause__", None)
        if cause is not None:
            text_parts.append(type(cause).__name__.lower())
            text_parts.append(str(cause).lower())

        context = getattr(exc, "__context__", None)
        if context is not None:
            text_parts.append(type(context).__name__.lower())
            text_parts.append(str(context).lower())

        text = " ".join(part for part in text_parts if part)
        return any(marker in text for marker in TRANSIENT_STORAGE_ERROR_MARKERS)

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
        *,
        strip_none: bool = True,
    ) -> dict[str, Any]:
        clean_payload = self._serialize_payload(payload, strip_none=strip_none)
        data = self._execute(
            self.client.table(self.table_name)
            .update(clean_payload)
            .eq("id", str(row_id)),
            operation="update_by_id",
        )

        row = self._first_or_none(data)
        if row is not None:
            return row

        # Some PostgREST/Supabase deployments can apply an UPDATE successfully
        # but return an empty response body when the query does not explicitly
        # request representation. Treat that as a read-after-write situation,
        # not as a failed persistence operation. This is important for Render
        # production, where resolution_runs PATCH returned HTTP 200 but empty
        # data, causing the resolver to mark a successfully saved run as failed.
        refreshed = self._get_by_id(row_id)
        if refreshed is not None:
            return refreshed

        raise StorageError(
            "Database operation returned no rows: update_by_id",
            details={
                "table": self.table_name,
                "operation": "update_by_id",
                "row_id": str(row_id),
            },
        )

    def _get_by_id(self, row_id: str | UUID) -> dict[str, Any] | None:
        data = self._execute(
            self.client.table(self.table_name)
            .select("*")
            .eq("id", str(row_id))
            .limit(1),
            operation="get_by_id",
        )
        return self._first_or_none(data)
