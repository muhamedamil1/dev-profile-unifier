from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from app.schemas.enums import ResolutionStatus
from app.storage.base import BaseRepository
from app.utils.errors import StorageError


class ResolutionRunsRepo(BaseRepository):
    table_name = "resolution_runs"
    _RUN_UPDATE_FALLBACK_COLUMNS = {
        "result_summary",
    }

    def create_run(
        self,
        *,
        input_name: str,
        input_payload: dict[str, Any],
        sources_attempted: list[str] | None = None,
    ) -> dict[str, Any]:
        return self._insert_one(
            {
                "input_name": input_name,
                "input_payload": input_payload,
                "status": ResolutionStatus.RUNNING.value,
                "sources_attempted": sources_attempted or [],
                "sources_succeeded": [],
                "sources_failed": [],
                "source_errors": [],
            }
        )

    def get_by_id(self, run_id: str | UUID) -> dict[str, Any] | None:
        return self._get_by_id(run_id)

    def complete_run(
        self,
        *,
        run_id: str | UUID,
        status: ResolutionStatus | str,
        duration_ms: int,
        sources_attempted: list[str],
        sources_succeeded: list[str],
        sources_failed: list[str],
        source_errors: list[dict[str, Any]] | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        if isinstance(status, str):
            status = ResolutionStatus(status)

        if status == ResolutionStatus.RUNNING:
            raise ValueError("complete_run cannot set status to running")

        return self._update_by_id(
            run_id,
            {
                "status": status.value,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "duration_ms": duration_ms,
                "sources_attempted": sources_attempted,
                "sources_succeeded": sources_succeeded,
                "sources_failed": sources_failed,
                "source_errors": source_errors or [],
                "error_message": error_message,
            },
        )

    def mark_failed(
        self,
        *,
        run_id: str | UUID | None = None,
        resolution_run_id: str | UUID | None = None,
        duration_ms: int | None = None,
        sources_attempted: list[str] | None = None,
        sources_failed: list[str] | None = None,
        source_errors: list[dict[str, Any]] | None = None,
        error_message: str | None = None,
        error_details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        target_run_id = resolution_run_id if resolution_run_id is not None else run_id
        if target_run_id is None:
            raise ValueError("mark_failed requires run_id or resolution_run_id")

        existing = self.get_by_id(target_run_id) or {}
        existing_errors = existing.get("source_errors") if isinstance(existing.get("source_errors"), list) else []
        merged_errors = [*existing_errors, *(source_errors or [])]
        if error_details:
            merged_errors.append(error_details)

        summary = existing.get("result_summary") if isinstance(existing.get("result_summary"), dict) else {}
        failed_summary = {
            **summary,
            "phase": "7E",
            "failed": True,
        }

        payload = {
            "status": ResolutionStatus.FAILED.value,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "duration_ms": duration_ms if duration_ms is not None else self._duration_ms_from_started_at(existing),
            "sources_attempted": sources_attempted if sources_attempted is not None else existing.get("sources_attempted", []),
            "sources_succeeded": [],
            "sources_failed": sources_failed if sources_failed is not None else existing.get("sources_failed", []),
            "source_errors": merged_errors,
            "error_message": error_message or "Resolution failed.",
            "result_summary": failed_summary,
        }

        return self._update_run_with_fallback(target_run_id, payload)

    def delete_by_id(self, resolution_run_id: UUID | str) -> int:
        data = self._execute(
            self.client.table(self.table_name)
            .delete()
            .eq("id", str(resolution_run_id)),
            operation="delete_resolution_run",
        )

        return len(data or [])

    def delete_finished_before(
        self,
        *,
        cutoff: datetime,
        statuses: list[ResolutionStatus | str] | None = None,
    ) -> int:
        if statuses is None:
            statuses = [
                ResolutionStatus.RESOLVED.value,
                ResolutionStatus.FAILED.value,
                ResolutionStatus.PARTIAL.value,
            ]
        else:
            statuses = [ResolutionStatus(s).value if isinstance(s, str) else s.value for s in statuses]

        data = self._execute(
            self.client.table(self.table_name)
            .delete()
            .in_("status", statuses)
            .lt("completed_at", cutoff.isoformat()),
            operation="delete_finished_resolution_runs",
        )

        return len(data or [])

    def finalize_resolution(
        self,
        *,
        resolution_run_id: UUID | str,
        status: ResolutionStatus,
        summary: dict[str, Any],
    ) -> dict:
        existing = self.get_by_id(resolution_run_id) or {}
        payload = {
            "status": status.value,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "result_summary": summary,
        }
        duration_ms = self._duration_ms_from_started_at(existing)
        if duration_ms is not None:
            payload["duration_ms"] = duration_ms

        return self._update_run_with_fallback(str(resolution_run_id), payload)


    def merge_result_summary(
        self,
        *,
        resolution_run_id: UUID | str,
        patch: dict[str, Any],
    ) -> dict[str, Any]:
        existing = self.get_by_id(resolution_run_id) or {}
        summary = existing.get("result_summary") if isinstance(existing.get("result_summary"), dict) else {}
        merged_summary = {**summary, **patch}
        return self._update_run_with_fallback(str(resolution_run_id), {"result_summary": merged_summary})

    def _update_run_with_fallback(
        self,
        row_id: str | UUID,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        current_payload = dict(payload)
        removed_columns: set[str] = set()

        while True:
            try:
                return self._update_by_id(row_id, current_payload)
            except StorageError as exc:
                missing_column = self._missing_run_update_column(exc)
                if missing_column is None or missing_column in removed_columns:
                    raise

                removed_columns.add(missing_column)
                current_payload.pop(missing_column, None)

    def _missing_run_update_column(self, exc: StorageError) -> str | None:
        message = str(exc.internal_details.get("error", "")).lower()
        for column in sorted(self._RUN_UPDATE_FALLBACK_COLUMNS):
            if re.search(rf"\b{re.escape(column)}\b", message):
                return column
        return None


    def _duration_ms_from_started_at(self, row: dict[str, Any]) -> int | None:
        started_at = row.get("started_at")
        if not started_at:
            return None

        try:
            started = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
        except ValueError:
            return None

        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)

        return max(0, int((datetime.now(timezone.utc) - started).total_seconds() * 1000))
