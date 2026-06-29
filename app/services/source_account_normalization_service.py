from __future__ import annotations

import re
from collections import defaultdict
from typing import Any
from uuid import UUID

from app.schemas.enums import PlatformSource
from app.schemas.normalization import (
    NormalizationWarning,
    NormalizedAccountResult,
    SourceAccountNormalizationResult,
)
from app.services.devto_normalizer import DevToAccountNormalizer
from app.services.github_normalizer import GitHubAccountNormalizer
from app.services.hackernews_normalizer import HackerNewsAccountNormalizer
from app.services.stackoverflow_normalizer import StackOverflowAccountNormalizer
from app.storage.raw_records_repo import RawRecordsRepo
from app.storage.source_accounts_repo import SourceAccountsRepo


class SourceAccountNormalizationService:
    """
    Loads raw source records for a run and normalizes them into SourceAccount rows.
    """

    def __init__(
        self,
        *,
        raw_records_repo: RawRecordsRepo,
        source_accounts_repo: SourceAccountsRepo,
        github_normalizer: GitHubAccountNormalizer | None = None,
        stackoverflow_normalizer: StackOverflowAccountNormalizer | None = None,
        devto_normalizer: DevToAccountNormalizer | None = None,
        hackernews_normalizer: HackerNewsAccountNormalizer | None = None,
    ) -> None:
        self.raw_records_repo = raw_records_repo
        self.source_accounts_repo = source_accounts_repo

        self.normalizers = {
            PlatformSource.GITHUB: github_normalizer or GitHubAccountNormalizer(),
            PlatformSource.STACKOVERFLOW: stackoverflow_normalizer or StackOverflowAccountNormalizer(),
            PlatformSource.DEVTO: devto_normalizer or DevToAccountNormalizer(),
            PlatformSource.HACKERNEWS: hackernews_normalizer or HackerNewsAccountNormalizer(),
        }

    def normalize_run(
        self,
        *,
        resolution_run_id: str | UUID,
        persist: bool = True,
    ) -> SourceAccountNormalizationResult:
        run_uuid = UUID(str(resolution_run_id))
        raw_records = self.raw_records_repo.list_by_run(run_uuid)

        warnings: list[NormalizationWarning] = []

        if not raw_records:
            return SourceAccountNormalizationResult(
                resolution_run_id=run_uuid,
                accounts=[],
                warnings=[
                    NormalizationWarning(
                        message="No raw source records found for resolution run.",
                        details={"resolution_run_id": str(run_uuid)},
                    )
                ],
            )

        grouped_records, grouping_warnings = self._group_records(raw_records)
        warnings.extend(grouping_warnings)

        normalized_accounts: list[NormalizedAccountResult] = []

        for source in sorted(grouped_records, key=lambda item: item.value):
            groups = grouped_records[source]
            normalizer = self.normalizers.get(source)

            if normalizer is None:
                warnings.append(
                    NormalizationWarning(
                        source=source,
                        message="No normalizer registered for source.",
                    )
                )
                continue

            for group_key in sorted(groups):
                records = groups[group_key]

                try:
                    normalized = normalizer.normalize(records)
                except Exception as exc:
                    warnings.append(
                        self._exception_warning(
                            source=source,
                            group_key=group_key,
                            records=records,
                            message="Raw record group normalization failed.",
                            exc=exc,
                        )
                    )
                    continue

                if normalized is None:
                    warnings.append(
                        NormalizationWarning(
                            source=source,
                            message=(
                                "Raw record group could not be normalized because "
                                "the primary profile/user record was missing or invalid."
                            ),
                            details={
                                "group_key": group_key,
                                "record_count": len(records),
                                "record_types": self._record_types(records),
                            },
                        )
                    )
                    continue

                if persist:
                    try:
                        persisted_row = self.source_accounts_repo.upsert_account(
                            normalized.source_account
                        )
                    except Exception as exc:
                        normalized.warnings.append(
                            self._exception_warning(
                                source=source,
                                group_key=group_key,
                                records=records,
                                message="Source account persistence failed.",
                                exc=exc,
                            )
                        )
                    else:
                        normalized.persisted_row = persisted_row
                        self._copy_persisted_id(normalized.source_account, persisted_row)

                normalized_accounts.append(normalized)

        return SourceAccountNormalizationResult(
            resolution_run_id=run_uuid,
            accounts=normalized_accounts,
            warnings=warnings,
        )

    def _copy_persisted_id(self, account: Any, persisted_row: dict[str, Any] | None) -> None:
        if not persisted_row:
            return

        persisted_id = persisted_row.get("id")
        if not persisted_id:
            return

        try:
            account.id = UUID(str(persisted_id))
        except (TypeError, ValueError):
            return

    def _group_records(
        self,
        raw_records: list[dict[str, Any]],
    ) -> tuple[
        dict[PlatformSource, dict[str, list[dict[str, Any]]]],
        list[NormalizationWarning],
    ]:
        grouped: dict[PlatformSource, dict[str, list[dict[str, Any]]]] = defaultdict(
            lambda: defaultdict(list)
        )
        warnings: list[NormalizationWarning] = []
        handle_aliases = self._handle_aliases(raw_records)

        for record in sorted(raw_records, key=self._record_sort_key):
            source_value = record.get("source")

            try:
                source = PlatformSource(source_value)
            except ValueError:
                warnings.append(
                    NormalizationWarning(
                        message="Raw record has unsupported source and was skipped.",
                        raw_record_id=self._record_id_or_none(record),
                        details={"source": source_value},
                    )
                )
                continue

            group_key = self._group_key(record, source=source, handle_aliases=handle_aliases)

            if group_key is None:
                warnings.append(
                    NormalizationWarning(
                        source=source,
                        raw_record_id=self._record_id_or_none(record),
                        source_record_type=record.get("source_record_type"),
                        message=(
                            "Raw record had no source_user_id or handle; grouped by "
                            "raw record ID only."
                        ),
                    )
                )
                group_key = f"raw:{record.get('id')}"

            grouped[source][group_key].append(record)

        return grouped, warnings

    def _handle_aliases(self, raw_records: list[dict[str, Any]]) -> dict[tuple[PlatformSource, str], str]:
        aliases: dict[tuple[PlatformSource, str], str] = {}

        for record in raw_records:
            try:
                source = PlatformSource(record.get("source"))
            except ValueError:
                continue

            source_user_id = self._clean_identity(record.get("source_user_id"))
            handle = self._clean_identity(record.get("handle"))

            if source_user_id and handle:
                aliases[(source, handle)] = f"id:{source_user_id}"

        return aliases

    def _group_key(
        self,
        record: dict[str, Any],
        *,
        source: PlatformSource,
        handle_aliases: dict[tuple[PlatformSource, str], str],
    ) -> str | None:
        source_user_id = self._clean_identity(record.get("source_user_id"))
        handle = self._clean_identity(record.get("handle"))

        if source_user_id:
            return f"id:{source_user_id}"

        if handle:
            return handle_aliases.get((source, handle), f"handle:{handle}")

        return None

    def _clean_identity(self, value: Any) -> str | None:
        if value is None:
            return None

        cleaned = str(value).strip().lower()
        return cleaned or None

    def _record_sort_key(self, record: dict[str, Any]) -> tuple[str, str, str, str, str]:
        return (
            str(record.get("source") or ""),
            str(record.get("source_user_id") or ""),
            str(record.get("handle") or "").lower(),
            str(record.get("source_record_type") or ""),
            str(record.get("id") or ""),
        )

    def _record_types(self, records: list[dict[str, Any]]) -> list[str]:
        return sorted(
            {
                str(record.get("source_record_type"))
                for record in records
                if record.get("source_record_type") is not None
            }
        )

    def _exception_warning(
        self,
        *,
        source: PlatformSource,
        group_key: str,
        records: list[dict[str, Any]],
        message: str,
        exc: Exception,
    ) -> NormalizationWarning:
        return NormalizationWarning(
            source=source,
            message=message,
            details={
                "group_key": group_key,
                "record_count": len(records),
                "record_types": self._record_types(records),
                "error_type": type(exc).__name__,
                "error_message": self._safe_error_message(exc),
            },
        )

    def _safe_error_message(self, exc: Exception) -> str:
        message = str(exc).strip() or type(exc).__name__
        sensitive_pattern = re.compile(
            r"raw_payload|access[_-]?token|api[_-]?key|authorization|secret|password|email",
            re.IGNORECASE,
        )

        if sensitive_pattern.search(message):
            return "Exception message omitted because it may contain sensitive data."

        return message[:300]

    def _record_id_or_none(self, record: dict[str, Any]) -> UUID | None:
        value = record.get("id")

        if not value:
            return None

        try:
            return UUID(str(value))
        except ValueError:
            return None
