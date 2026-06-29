from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from app.schemas.enums import ResolutionStatus
from app.storage.base import BaseRepository


class ResolutionRunsRepo(BaseRepository):
    table_name = "resolution_runs"

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
        run_id: str | UUID,
        duration_ms: int,
        sources_attempted: list[str],
        sources_failed: list[str],
        source_errors: list[dict[str, Any]] | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        return self.complete_run(
            run_id=run_id,
            status=ResolutionStatus.FAILED,
            duration_ms=duration_ms,
            sources_attempted=sources_attempted,
            sources_succeeded=[],
            sources_failed=sources_failed,
            source_errors=source_errors or [],
            error_message=error_message or "Resolution failed.",
        )

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
        payload = {
            "status": status.value,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "result_summary": summary,
        }

        return self._update_by_id(str(resolution_run_id), payload)
