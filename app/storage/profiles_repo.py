from __future__ import annotations

import re
from typing import Any
from uuid import UUID
from datetime import UTC, datetime

from app.schemas.classification import AccountClassification
from app.resolution.ambiguity_reviewer import (
    final_link_fields_after_review,
    merge_llm_review_into_decision_payload,
)
from app.schemas.enums import (
    MatchDecision,
    ProfileConfidenceLevel,
    SourceRelationshipType,
    VerificationStatus,
)
from app.schemas.requests import ProfileResolveRequest
from app.storage.base import BaseRepository
from app.utils.errors import ProfileNotFoundError, StorageError


class ProfilesRepo(BaseRepository):
    table_name = "canonical_profiles"
    _PROFILE_SOURCE_LINK_FALLBACK_COLUMNS = {
        "decision_payload",
        "positive_signal_count",
        "negative_signal_count",
        "has_high_conflict",
        "verification_status",
    }

    def create_profile(
        self,
        *,
        resolution_run_id: str | UUID | None = None,
        display_name: str | None = None,
        headline: str | None = None,
        location: str | None = None,
        bio: str | None = None,
        primary_avatar_url: str | None = None,
        primary_website_url: str | None = None,
        inferred_skills: list[str] | None = None,
        confidence_level: ProfileConfidenceLevel = ProfileConfidenceLevel.UNCERTAIN,
        profile_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._insert_one(
            {
                "resolution_run_id": resolution_run_id,
                "display_name": display_name,
                "headline": headline,
                "location": location,
                "bio": bio,
                "primary_avatar_url": primary_avatar_url,
                "primary_website_url": primary_website_url,
                "inferred_skills": inferred_skills or [],
                "confidence_level": confidence_level.value,
                "profile_payload": profile_payload or {},
            }
        )

    def update_profile(
        self,
        profile_id: str | UUID,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return self._update_by_id(profile_id, payload)

    def get_by_id(self, profile_id: str | UUID) -> dict[str, Any] | None:
        return self._get_by_id(profile_id)

    def delete_by_id(self, profile_id: str | UUID) -> int:
        data = self._execute(
            self.client.table(self.table_name)
            .delete()
            .eq("id", str(profile_id)),
            operation="delete_profile_by_id",
        )
        return len(data or [])

    def create_profile_source_link(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        clean_payload = self._serialize_payload(payload)

        data = self._execute(
            self.client.table("profile_source_links").insert(clean_payload),
            operation="create_profile_source_link",
        )

        return self._require_one(data, operation="create_profile_source_link")

    def get_profile_source_links(
        self,
        profile_id: str | UUID,
    ) -> list[dict[str, Any]]:
        data = self._execute(
            self.client.table("profile_source_links")
            .select("*")
            .eq("profile_id", str(profile_id))
            .order("confidence_score", desc=True),
            operation="get_profile_source_links",
        )

        return data if isinstance(data, list) else []

    def get_full_profile_data(self, profile_id: str | UUID) -> dict[str, Any]:
        """
        Read all data needed to build GET /profiles/{id}.

        The API response conversion will happen in the service/API layer later.
        This repository returns raw database rows grouped in a useful shape.
        """
        profile = self.get_by_id(profile_id)
        if profile is None:
            raise ProfileNotFoundError(str(profile_id))

        source_links = self.get_profile_source_links(profile_id)

        source_account_ids = [
            row["source_account_id"]
            for row in source_links
            if row.get("source_account_id")
        ]

        link_ids = [
            row["id"]
            for row in source_links
            if row.get("id")
        ]

        source_accounts: list[dict[str, Any]] = []
        if source_account_ids:
            data = self._execute(
                self.client.table("source_accounts")
                .select("*")
                .in_("id", source_account_ids),
                operation="get_full_profile_source_accounts",
            )
            source_accounts = data if isinstance(data, list) else []

        evidence: list[dict[str, Any]] = []
        if link_ids:
            data = self._execute(
                self.client.table("match_evidence")
                .select("*")
                .in_("profile_source_link_id", link_ids)
                .order("created_at", desc=False),
                operation="get_full_profile_evidence",
            )
            evidence = data if isinstance(data, list) else []

        conflicts_data = self._execute(
            self.client.table("profile_conflicts")
            .select("*")
            .eq("profile_id", str(profile_id))
            .order("created_at", desc=False),
            operation="get_full_profile_conflicts",
        )
        conflicts = conflicts_data if isinstance(conflicts_data, list) else []

        facts_data = self._execute(
            self.client.table("profile_facts")
            .select("*")
            .eq("profile_id", str(profile_id))
            .order("created_at", desc=False),
            operation="get_full_profile_facts",
        )
        facts = facts_data if isinstance(facts_data, list) else []

        summary_data = self._execute(
            self.client.table("llm_summaries")
            .select("*")
            .eq("profile_id", str(profile_id))
            .order("created_at", desc=True)
            .limit(1),
            operation="get_full_profile_latest_summary",
        )
        latest_summary = self._first_or_none(summary_data)

        evidence_by_link_id: dict[str, list[dict[str, Any]]] = {}
        for item in evidence:
            link_id = str(item.get("profile_source_link_id"))
            evidence_by_link_id.setdefault(link_id, []).append(item)

        source_accounts_by_id = {
            str(item["id"]): item
            for item in source_accounts
            if item.get("id")
        }

        return {
            "profile": profile,
            "source_links": source_links,
            "source_accounts": source_accounts,
            "source_accounts_by_id": source_accounts_by_id,
            "evidence": evidence,
            "evidence_by_link_id": evidence_by_link_id,
            "conflicts": conflicts,
            "facts": facts,
            "latest_summary": latest_summary,
        }

    def get_by_resolution_run_id(self, resolution_run_id: UUID | str) -> dict[str, Any] | None:
        data = self._execute(
            self.client.table(self.table_name)
            .select("*")
            .eq("resolution_run_id", str(resolution_run_id))
            .limit(1),
            operation="get_profile_by_resolution_run_id",
        )

        return self._first_or_none(data)

    def create_resolution_shell(
        self,
        *,
        resolution_run_id: UUID | str,
        request: ProfileResolveRequest,
        summary: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        existing = self.get_by_resolution_run_id(resolution_run_id)
        payload = {
            "resolution_run_id": str(resolution_run_id),
            "display_name": request.name,
            "headline": None,
            "location": None,
            "bio": None,
            "primary_avatar_url": None,
            "primary_website_url": None,
            "inferred_skills": [],
            "confidence_level": summary.get("confidence_level", ProfileConfidenceLevel.UNCERTAIN.value),
            "profile_payload": {
                "profile_stage": "resolution_shell",
                "phase": "7E",
                "canonical_fields_pending": True,
                "resolution_summary": summary,
                "max_evidence_confidence_score": summary.get("max_evidence_confidence_score"),
                "max_decision_confidence_score": summary.get("max_decision_confidence_score"),
                "created_by": "resolution_service",
            },
        }

        if existing:
            clean_payload = self._serialize_payload(payload, strip_none=False)
            data = self._execute(
                self.client.table(self.table_name)
                .update(clean_payload)
                .eq("id", str(existing["id"])),
                operation="update_resolution_shell",
            )
            return self._require_one(data, operation="update_resolution_shell"), False

        return self._insert_one(payload), True

    def delete_source_links_for_profile(self, canonical_profile_id: UUID | str) -> int:
        data = self._execute(
            self.client.table("profile_source_links")
            .delete()
            .eq("profile_id", str(canonical_profile_id)),
            operation="delete_profile_source_links",
        )

        return len(data or [])

    def insert_source_links_for_classifications(
        self,
        *,
        canonical_profile_id: UUID | str | None = None,
        profile_id: UUID | str | None = None,
        classifications: list[AccountClassification],
        review_outcome_by_key: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if not classifications:
            return []

        target_profile_id = profile_id if profile_id is not None else canonical_profile_id
        if target_profile_id is None:
            return []

        payloads = [
            self._source_link_payload(
                target_profile_id=target_profile_id,
                item=item,
                review_outcome_by_key=review_outcome_by_key or {},
            )
            for item in classifications
            if item.source_account_id is not None
        ]

        if not payloads:
            return []

        clean_payloads = [self._serialize_payload(payload) for payload in payloads]
        data = self._insert_profile_source_links_with_fallback(clean_payloads)
        return data if isinstance(data, list) else []

    def _insert_profile_source_links_with_fallback(
        self,
        payloads: list[dict[str, Any]],
    ) -> Any:
        current_payloads = payloads
        removed_columns: set[str] = set()

        while True:
            try:
                return self._execute(
                    self.client.table("profile_source_links").insert(current_payloads),
                    operation="insert_profile_source_links_for_classifications",
                )
            except StorageError as exc:
                missing_column = self._missing_profile_source_links_column(exc)
                if missing_column is None or missing_column in removed_columns:
                    raise

                removed_columns.add(missing_column)
                current_payloads = [
                    {
                        key: value
                        for key, value in payload.items()
                        if key != missing_column
                    }
                    for payload in current_payloads
                ]


    def _source_link_payload(
        self,
        *,
        target_profile_id: UUID | str,
        item: AccountClassification,
        review_outcome_by_key: dict[str, Any],
    ) -> dict[str, Any]:
        review_outcome = review_outcome_by_key.get(item.source_account_key)
        original_relationship_type = self._relationship_type_for_decision(item)
        original_verification_status = self._verification_status_for_decision(item)
        link_fields = final_link_fields_after_review(
            original_decision=item.decision.value,
            original_relationship_type=original_relationship_type,
            original_verification_status=original_verification_status,
            original_confidence_score=self._deterministic_link_confidence(item),
            outcome=review_outcome,
        )
        decision_payload = merge_llm_review_into_decision_payload(
            self._decision_payload(item),
            review_outcome,
        )

        return {
            "profile_id": str(target_profile_id),
            "source_account_id": str(item.source_account_id),
            "confidence_score": link_fields["confidence_score"],
            "decision": link_fields["decision"],
            "relationship_type": link_fields["relationship_type"],
            "verification_status": link_fields["verification_status"],
            "positive_signal_count": len(item.independent_positive_groups),
            "negative_signal_count": len(item.conflict_types),
            "has_high_conflict": bool(item.blocking_conflict_types),
            "decision_payload": decision_payload,
        }

    def _deterministic_link_confidence(self, item: AccountClassification) -> float:
        return float(item.decision_confidence_score)

    def _relationship_type_for_decision(self, item: AccountClassification) -> str:
        if item.decision == MatchDecision.AUTO_MATCH and item.is_anchor:
            return SourceRelationshipType.PRIMARY.value

        if item.decision == MatchDecision.AUTO_MATCH:
            return SourceRelationshipType.SECONDARY.value

        if item.decision == MatchDecision.NEEDS_REVIEW:
            return SourceRelationshipType.POSSIBLE_ALIAS.value

        return SourceRelationshipType.REJECTED.value

    def _verification_status_for_decision(self, item: AccountClassification) -> str:
        if item.decision == MatchDecision.AUTO_MATCH and item.is_anchor:
            return VerificationStatus.CLAIMED_BY_INPUT.value

        if item.decision == MatchDecision.AUTO_MATCH:
            return VerificationStatus.EVIDENCE_MATCHED.value

        if item.decision == MatchDecision.NEEDS_REVIEW:
            return VerificationStatus.NEEDS_REVIEW.value

        return VerificationStatus.REJECTED.value

    def _decision_payload(self, item: AccountClassification) -> dict[str, Any]:
        return {
            "decision_basis": item.decision_basis.value,
            "risk_level": item.risk_level.value,
            "rationale": item.rationale,
            "evidence_confidence_score": item.evidence_confidence_score,
            "decision_confidence_score": item.decision_confidence_score,
            "account_score": item.account_score,
            "best_pair_score": item.best_pair_score,
            "is_anchor": item.is_anchor,
            "accepted_as_anchor": item.accepted_as_anchor,
            "best_anchor_account_key": item.best_anchor_account_key,
            "best_pair_key": item.best_pair_key,
            "independent_positive_groups": item.independent_positive_groups,
            "strong_positive_groups": item.strong_positive_groups,
            "weak_positive_groups": item.weak_positive_groups,
            "weak_signal_only": item.weak_signal_only,
            "hn_conservative": item.hn_conservative,
            "hn_requires_strong_evidence": item.hn_requires_strong_evidence,
            "conflict_types": item.conflict_types,
            "blocking_conflict_types": item.blocking_conflict_types,
            "metadata": item.metadata,
        }

    def get_by_id(self, profile_id: UUID | str) -> dict | None:
        data = self._execute(
            self.client.table(self.table_name)
            .select("*")
            .eq("id", str(profile_id))
            .limit(1),
            operation="get_canonical_profile_by_id",
        )

        return self._first_or_none(data)

    def get_by_resolution_run_id(self, resolution_run_id: UUID | str) -> dict | None:
        data = self._execute(
            self.client.table(self.table_name)
            .select("*")
            .eq("resolution_run_id", str(resolution_run_id))
            .limit(1),
            operation="get_canonical_profile_by_resolution_run_id",
        )

        return self._first_or_none(data)

    def list_source_links_for_profile(self, profile_id: UUID | str) -> list[dict]:
        return self._execute(
            self.client.table("profile_source_links")
            .select("*")
            .eq("profile_id", str(profile_id))
            .order("created_at"),
            operation="list_profile_source_links_for_profile",
        )

    def update_canonical_profile_fields(
        self,
        *,
        profile_id: UUID | str,
        display_name: str | None,
        headline: str | None,
        location: str | None,
        bio: str | None,
        primary_avatar_url: str | None,
        primary_website_url: str | None,
        inferred_skills: list[str],
        confidence_level: str,
        profile_payload: dict[str, Any],
    ) -> dict:
        payload = {
            "display_name": display_name,
            "headline": headline,
            "location": location,
            "bio": bio,
            "primary_avatar_url": primary_avatar_url,
            "primary_website_url": primary_website_url,
            "inferred_skills": inferred_skills,
            "confidence_level": confidence_level,
            "profile_payload": profile_payload,
            "updated_at": datetime.now(UTC).isoformat(),
        }

        clean_payload = self._serialize_payload(payload, strip_none=False)
        data = self._execute(
            self.client.table(self.table_name)
            .update(clean_payload)
            .eq("id", str(profile_id)),
            operation="update_canonical_profile_fields",
        )
        return self._require_one(data, operation="update_canonical_profile_fields")
    def _missing_profile_source_links_column(self, exc: StorageError) -> str | None:
        message = str(exc.internal_details.get("error", "")).lower()
        for column in sorted(self._PROFILE_SOURCE_LINK_FALLBACK_COLUMNS):
            if re.search(rf"\b{re.escape(column)}\b", message):
                return column
        return None

