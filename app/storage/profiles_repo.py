from __future__ import annotations

from typing import Any
from uuid import UUID

from app.schemas.classification import AccountClassification
from app.schemas.enums import (
    MatchDecision,
    ProfileConfidenceLevel,
    SourceRelationshipType,
    VerificationStatus,
)
from app.schemas.requests import ProfileResolveRequest
from app.storage.base import BaseRepository
from app.utils.errors import ProfileNotFoundError


class ProfilesRepo(BaseRepository):
    table_name = "canonical_profiles"

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
            "confidence_level": summary.get("confidence_level", ProfileConfidenceLevel.UNCERTAIN.value),
            "profile_payload": {
                "profile_stage": "resolution_shell",
                "phase": "7E",
                "canonical_fields_pending": True,
                "resolution_summary": summary,
            },
        }

        if existing:
            return self._update_by_id(existing["id"], payload), False

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
        canonical_profile_id: UUID | str,
        classifications: list[AccountClassification],
    ) -> list[dict[str, Any]]:
        if not classifications:
            return []

        payloads = [
            {
                "profile_id": str(canonical_profile_id),
                "source_account_id": str(item.source_account_id),
                "confidence_score": item.decision_confidence_score,
                "decision": item.decision.value,
                "relationship_type": self._relationship_type_for_decision(item),
                "verification_status": self._verification_status_for_decision(item),
                "positive_signal_count": max(
                    len(item.independent_positive_groups),
                    2 if item.decision == MatchDecision.AUTO_MATCH else 0,
                ),
                "negative_signal_count": len(item.conflict_types),
                "has_high_conflict": bool(item.blocking_conflict_types),
            }
            for item in classifications
            if item.source_account_id is not None
        ]

        if not payloads:
            return []

        clean_payloads = [self._serialize_payload(payload) for payload in payloads]
        data = self._execute(
            self.client.table("profile_source_links").insert(clean_payloads),
            operation="insert_profile_source_links_for_classifications",
        )
        return data if isinstance(data, list) else []

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
            return VerificationStatus.LIKELY_SAME_PERSON.value

        if item.decision == MatchDecision.NEEDS_REVIEW:
            return VerificationStatus.NEEDS_REVIEW.value

        return VerificationStatus.REJECTED.value
