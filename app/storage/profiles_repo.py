from __future__ import annotations

from typing import Any
from uuid import UUID

from app.schemas.enums import ProfileConfidenceLevel
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
