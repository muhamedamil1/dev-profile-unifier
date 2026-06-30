from __future__ import annotations

import logging
from time import perf_counter
from typing import Any
from uuid import UUID

from app.resolution.classifier import DecisionClassifier
from app.resolution.ambiguity_reviewer import GeminiAmbiguityReviewer
from app.resolution.conflict_detector import ConflictDetector
from app.resolution.evidence import EvidenceExtractor
from app.resolution.scorer import ResolutionScorer
from app.schemas.classification import AccountClassification, ClassificationResult
from app.schemas.conflicts import DetectedConflict
from app.schemas.enums import MatchDecision, ResolutionStatus
from app.schemas.evidence import ExtractedEvidence, EvidenceTargetType
from app.schemas.requests import ProfileResolveRequest
from app.schemas.resolution_pipeline import (
    ResolutionPersistenceCounts,
    ResolutionPipelineResult,
)
from app.schemas.source_account import SourceAccount
from app.storage.conflicts_repo import ConflictsRepo
from app.storage.evidence_repo import EvidenceRepo
from app.storage.profiles_repo import ProfilesRepo
from app.storage.resolution_runs_repo import ResolutionRunsRepo
from app.storage.source_accounts_repo import SourceAccountsRepo
from app.utils.errors import ResolutionFailedError


logger = logging.getLogger(__name__)


class ResolutionService:
    """
    Runs the deterministic identity resolution pipeline after source accounts are normalized.

    Phase 7E responsibilities:
    - evidence extraction
    - conflict detection
    - scoring
    - decision classification
    - persistence of evidence/conflicts/classification links
    - resolution run finalization

    It does not:
    - build canonical profile fields
    - extract profile facts
    - let Gemini decide merges before deterministic classification
    - expose API routes
    """

    def __init__(
        self,
        *,
        evidence_extractor: EvidenceExtractor,
        conflict_detector: ConflictDetector,
        scorer: ResolutionScorer,
        classifier: DecisionClassifier,
        evidence_repo: EvidenceRepo,
        conflicts_repo: ConflictsRepo,
        profiles_repo: ProfilesRepo,
        source_accounts_repo: SourceAccountsRepo,
        resolution_runs_repo: ResolutionRunsRepo,
        ambiguity_reviewer: GeminiAmbiguityReviewer | None = None,
    ) -> None:
        self.evidence_extractor = evidence_extractor
        self.conflict_detector = conflict_detector
        self.scorer = scorer
        self.classifier = classifier
        self.ambiguity_reviewer = ambiguity_reviewer

        self.evidence_repo = evidence_repo
        self.conflicts_repo = conflicts_repo
        self.profiles_repo = profiles_repo
        self.source_accounts_repo = source_accounts_repo
        self.resolution_runs_repo = resolution_runs_repo

    def resolve(
        self,
        *,
        resolution_run_id: UUID | str,
        request: ProfileResolveRequest,
        accounts: list[SourceAccount],
        persist: bool = True,
        replace_existing: bool = True,
    ) -> ResolutionPipelineResult:
        started = perf_counter()
        run_uuid = UUID(str(resolution_run_id))

        if not accounts:
            raise ResolutionFailedError(
                "No normalized source accounts were available for resolution."
            )

        try:
            accounts_for_pipeline = (
                self._ensure_persisted_accounts(accounts)
                if persist
                else self._sort_accounts(accounts)
            )
        except Exception as exc:
            if persist:
                self._mark_run_failed_safely(
                    resolution_run_id=run_uuid,
                    request=request,
                    duration_ms=self._duration_ms(started),
                )
            if isinstance(exc, ResolutionFailedError):
                raise
            raise ResolutionFailedError(
                "A normalized account was not persisted before resolution."
            ) from exc

        evidence_result = self.evidence_extractor.extract(
            request=request,
            accounts=accounts_for_pipeline,
        )

        conflict_result = self.conflict_detector.detect(
            accounts=accounts_for_pipeline,
        )

        scoring_result = self.scorer.score(
            accounts=accounts_for_pipeline,
            evidence=evidence_result.evidence,
            conflicts=conflict_result.conflicts,
        )

        classification_result = self.classifier.classify(
            accounts=accounts_for_pipeline,
            scoring_result=scoring_result,
        )

        review_batch = None
        review_outcome_by_key = {}
        if self.ambiguity_reviewer is not None:
            review_batch = self.ambiguity_reviewer.run_reviews(
                accounts=accounts_for_pipeline,
                classification_result=classification_result,
                scoring_result=scoring_result,
                evidence_result=evidence_result,
                conflict_result=conflict_result,
                resolution_run_id=run_uuid,
                persist_metrics=True,
            )
            review_outcome_by_key = review_batch.outcome_by_key

        summary = self._build_summary(
            request=request,
            accounts=accounts_for_pipeline,
            evidence=evidence_result.evidence,
            conflict_count=conflict_result.count,
            classification_result=classification_result,
        )

        persistence_counts = ResolutionPersistenceCounts()
        canonical_profile_id: UUID | None = None

        if persist:
            try:
                persistence_counts = self._persist_resolution(
                    resolution_run_id=run_uuid,
                    request=request,
                    evidence=evidence_result.evidence,
                    conflicts=conflict_result.conflicts,
                    classifications=classification_result.classifications,
                    review_outcome_by_key=review_outcome_by_key,
                    summary=summary,
                    replace_existing=replace_existing,
                )
            except Exception as exc:
                self._mark_run_failed_safely(
                    resolution_run_id=run_uuid,
                    request=request,
                    duration_ms=self._duration_ms(started),
                )
                raise ResolutionFailedError(
                    "The resolution result could not be saved safely.",
                    details={"error_type": type(exc).__name__},
                ) from exc

            self._patch_review_summary_safely(
                resolution_run_id=run_uuid,
                review_batch=review_batch,
            )

            canonical_profile_id = persistence_counts.canonical_profile_id
            summary = {
                **summary,
                "canonical_profile_id": str(canonical_profile_id) if canonical_profile_id else None,
            }

        return ResolutionPipelineResult(
            resolution_run_id=run_uuid,
            canonical_profile_id=canonical_profile_id,
            evidence=evidence_result,
            conflicts=conflict_result,
            scoring=scoring_result,
            classification=classification_result,
            persistence=persistence_counts,
            persisted=persist,
            summary=summary,
        )

    def _ensure_persisted_accounts(
        self,
        accounts: list[SourceAccount],
    ) -> list[SourceAccount]:
        persisted: list[SourceAccount] = []

        for account in accounts:
            if account.id is not None:
                persisted.append(account)
                continue

            expected_key = account.expected_source_account_key()
            row = self.source_accounts_repo.get_by_key(expected_key)

            if not row or not row.get("id"):
                raise ResolutionFailedError(
                    "A normalized account was not persisted before resolution.",
                    details={"source_account_key": expected_key},
                )

            persisted.append(account.model_copy(update={"id": UUID(str(row["id"]))}))

        sorted_accounts = self._sort_accounts(persisted)
        self._validate_source_account_ids_exist(sorted_accounts)
        return sorted_accounts

    def _validate_source_account_ids_exist(self, accounts: list[SourceAccount]) -> None:
        account_ids = [account.id for account in accounts if account.id is not None]
        if not account_ids:
            return

        rows = self.source_accounts_repo.list_by_ids(account_ids)
        found_ids = {str(row["id"]) for row in rows if row.get("id")}
        missing_ids = [str(account_id) for account_id in account_ids if str(account_id) not in found_ids]
        if missing_ids:
            raise ResolutionFailedError(
                "A normalized account was not persisted before resolution.",
                details={"missing_source_account_ids": missing_ids},
            )

    def _persist_resolution(
        self,
        *,
        resolution_run_id: UUID,
        request: ProfileResolveRequest,
        evidence: list[ExtractedEvidence],
        conflicts: list[DetectedConflict],
        classifications: list[AccountClassification],
        review_outcome_by_key: dict[str, Any] | None = None,
        summary: dict[str, Any],
        replace_existing: bool,
    ) -> ResolutionPersistenceCounts:
        usable_classification = self._has_usable_classification(classifications)
        self._validate_persistence_inputs(
            evidence=evidence,
            conflicts=conflicts,
            classifications=classifications if usable_classification else [],
        )

        # Supabase REST writes here are not transactional. A Postgres RPC that
        # validates, replaces, inserts, and finalizes in one DB transaction is
        # the production-hardening path if this workflow grows more complex.
        if not usable_classification:
            if replace_existing:
                self._delete_existing_profile_for_run(resolution_run_id)

            final_summary = {
                **summary,
                "canonical_profile_id": None,
                "canonical_profile_pending": False,
                "no_profile_created_reason": "all_accounts_rejected",
            }
            self.resolution_runs_repo.finalize_resolution(
                resolution_run_id=resolution_run_id,
                status=self._resolution_status_for_summary(final_summary),
                summary=final_summary,
            )
            return ResolutionPersistenceCounts(
                match_evidence_rows=0,
                profile_conflict_rows=0,
                profile_source_link_rows=0,
                canonical_profile_created=False,
                canonical_profile_reused=False,
                canonical_profile_upserted=False,
                canonical_profile_id=None,
            )

        profile_row, created = self.profiles_repo.create_resolution_shell(
            resolution_run_id=resolution_run_id,
            request=request,
            summary=summary,
        )
        canonical_profile_id = UUID(str(profile_row["id"]))

        if replace_existing:
            self.evidence_repo.delete_for_profile(canonical_profile_id)
            self.conflicts_repo.delete_for_profile(canonical_profile_id)
            self.profiles_repo.delete_source_links_for_profile(canonical_profile_id)

        link_rows = self.profiles_repo.insert_source_links_for_classifications(
            profile_id=canonical_profile_id,
            classifications=classifications,
            review_outcome_by_key=review_outcome_by_key,
        )
        link_ids_by_account_id = {
            str(row["source_account_id"]): row["id"]
            for row in link_rows
            if row.get("source_account_id") and row.get("id")
        }

        evidence_rows = self.evidence_repo.insert_many_for_profile_links(
            evidence=evidence,
            source_link_by_account_id=link_ids_by_account_id,
        )

        conflict_rows = self.conflicts_repo.insert_many_for_profile(
            profile_id=canonical_profile_id,
            conflicts=conflicts,
        )

        final_summary = {
            **summary,
            "canonical_profile_id": str(canonical_profile_id),
        }
        self.resolution_runs_repo.finalize_resolution(
            resolution_run_id=resolution_run_id,
            status=self._resolution_status_for_summary(final_summary),
            summary=final_summary,
        )

        return ResolutionPersistenceCounts(
            match_evidence_rows=len(evidence_rows),
            profile_conflict_rows=len(conflict_rows),
            profile_source_link_rows=len(link_rows),
            canonical_profile_created=created,
            canonical_profile_reused=not created,
            canonical_profile_upserted=True,
            canonical_profile_id=canonical_profile_id,
        )

    def _build_summary(
        self,
        *,
        request: ProfileResolveRequest,
        accounts: list[SourceAccount],
        evidence: list[ExtractedEvidence],
        conflict_count: int,
        classification_result: ClassificationResult,
    ) -> dict[str, Any]:
        auto_keys = classification_result.auto_matched_account_keys
        review_keys = classification_result.needs_review_account_keys
        rejected_keys = classification_result.rejected_account_keys
        has_usable_classification = self._has_usable_classification(
            classification_result.classifications
        )

        max_evidence_score = max(
            (item.evidence_confidence_score for item in classification_result.classifications),
            default=0.0,
        )
        max_decision_score = max(
            (item.decision_confidence_score for item in classification_result.classifications),
            default=0.0,
        )

        summary: dict[str, Any] = {
            "phase": "7E",
            "input_name": request.name,
            "source_account_count": len(accounts),
            "sources_evaluated": sorted({account.source.value for account in accounts}),
            "evidence_count": len(evidence),
            "conflict_count": conflict_count,
            "auto_match_count": len(auto_keys),
            "needs_review_count": len(review_keys),
            "reject_count": len(rejected_keys),
            "auto_matched_account_keys": auto_keys,
            "needs_review_account_keys": review_keys,
            "rejected_account_keys": rejected_keys,
            "anchor_account_keys": classification_result.anchor_account_keys,
            "has_review_items": classification_result.has_review_items,
            "has_rejections": classification_result.has_rejections,
            "max_evidence_confidence_score": round(max_evidence_score, 4),
            "max_decision_confidence_score": round(max_decision_score, 4),
            "confidence_level": self._confidence_level(max_evidence_score),
            "confidence_policy": "anchor_floor_separated_from_evidence_score",
            "canonical_profile_pending": has_usable_classification,
        }

        if not has_usable_classification:
            summary["no_profile_created_reason"] = "all_accounts_rejected"

        return summary

    def _patch_review_summary_safely(
        self,
        *,
        resolution_run_id: UUID,
        review_batch: Any,
    ) -> None:
        """Best-effort persistence for optional LLM ambiguity-review metadata.

        The core resolution has already been saved by the time this method is
        called. A transient Supabase/PostgREST issue while merging the optional
        review summary must not flip an otherwise persisted resolution into a
        user-visible resolution_failed response. The detailed per-link
        decision_payload rows are already persisted with the main resolution
        payload, so this run-level summary patch is useful observability data,
        not a correctness requirement.
        """

        try:
            self._patch_review_summary(
                resolution_run_id=resolution_run_id,
                review_batch=review_batch,
            )
        except Exception:  # noqa: BLE001 - optional metadata must not fail the run
            logger.exception(
                "Failed to patch optional ambiguity review summary; continuing with saved resolution.",
                extra={"resolution_run_id": str(resolution_run_id)},
            )

    def _patch_review_summary(
        self,
        *,
        resolution_run_id: UUID,
        review_batch: Any,
    ) -> None:
        if not review_batch or not self.resolution_runs_repo:
            return

        patch = review_batch.result_summary_patch()
        if hasattr(self.resolution_runs_repo, "update_result_summary_patch"):
            self.resolution_runs_repo.update_result_summary_patch(
                resolution_run_id=resolution_run_id,
                patch=patch,
            )
            return

        if hasattr(self.resolution_runs_repo, "merge_result_summary"):
            self.resolution_runs_repo.merge_result_summary(
                resolution_run_id=resolution_run_id,
                patch=patch,
            )

    def _validate_persistence_inputs(
        self,
        *,
        evidence: list[ExtractedEvidence],
        conflicts: list[DetectedConflict],
        classifications: list[AccountClassification],
    ) -> None:
        for item in evidence:
            if item.source_account_id is None:
                raise ResolutionFailedError("Evidence is missing a persisted source account ID.")

            if item.target_type == EvidenceTargetType.ACCOUNT_PAIR and item.target_account_id is None:
                raise ResolutionFailedError("Pair evidence is missing a persisted target account ID.")

        for item in conflicts:
            if item.source_account_id is None or item.target_account_id is None:
                raise ResolutionFailedError("Conflict is missing persisted source account IDs.")

        for item in classifications:
            if item.source_account_id is None:
                raise ResolutionFailedError("Classification is missing a persisted source account ID.")

    def _has_usable_classification(self, classifications: list[AccountClassification]) -> bool:
        return any(
            item.decision in {MatchDecision.AUTO_MATCH, MatchDecision.NEEDS_REVIEW}
            for item in classifications
        )

    def _confidence_level(self, score: float) -> str:
        if score >= 0.85:
            return "high"

        if score >= 0.60:
            return "medium"

        if score > 0:
            return "low"

        return "uncertain"

    def _resolution_status_for_summary(self, summary: dict[str, Any]) -> ResolutionStatus:
        if summary.get("has_review_items") or summary.get("has_rejections"):
            return ResolutionStatus.PARTIAL

        return ResolutionStatus.RESOLVED

    def _mark_run_failed_safely(
        self,
        *,
        resolution_run_id: UUID,
        request: ProfileResolveRequest,
        duration_ms: int,
    ) -> None:
        try:
            self.resolution_runs_repo.mark_failed(
                run_id=resolution_run_id,
                duration_ms=duration_ms,
                sources_attempted=[source.value for source in request.provided_sources],
                sources_failed=[source.value for source in request.provided_sources],
                source_errors=[{"reason": "resolution_persistence_failed"}],
                error_message="Resolution persistence failed.",
            )
        except Exception:
            return

    def _delete_existing_profile_for_run(self, resolution_run_id: UUID) -> None:
        existing = self.profiles_repo.get_by_resolution_run_id(resolution_run_id)
        if not existing or not existing.get("id"):
            return

        profile_id = existing["id"]
        self.evidence_repo.delete_for_profile(profile_id)
        self.conflicts_repo.delete_for_profile(profile_id)
        self.profiles_repo.delete_source_links_for_profile(profile_id)
        if hasattr(self.profiles_repo, "delete_by_id"):
            self.profiles_repo.delete_by_id(profile_id)

    def _sort_accounts(self, accounts: list[SourceAccount]) -> list[SourceAccount]:
        return sorted(accounts, key=lambda item: item.expected_source_account_key())

    def _duration_ms(self, started: float) -> int:
        return max(0, int((perf_counter() - started) * 1000))
