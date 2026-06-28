from __future__ import annotations

from typing import Any
from uuid import UUID

from app.schemas.enums import PlatformSource
from app.schemas.normalization import NormalizationWarning, NormalizedAccountResult
from app.schemas.source_account import SourceAccount
from app.utils.normalization import (
    clean_optional_str,
    compact_dict,
    dedupe_preserve_order,
    safe_int,
    top_values,
)
from app.utils.urls import extract_urls_from_text, normalize_profile_url, normalize_url, normalize_url_list


class GitHubAccountNormalizer:
    source = PlatformSource.GITHUB
    normalization_version = "github_normalizer_v1"

    def normalize(self, records: list[dict[str, Any]]) -> NormalizedAccountResult | None:
        profile_record = self._record_by_type(records, "github/profile")
        repos_record = self._record_by_type(records, "github/repos")

        if profile_record is None:
            return None

        profile = profile_record.get("raw_payload") or {}
        repos = repos_record.get("raw_payload") if repos_record else []

        if not isinstance(profile, dict):
            return None

        if not isinstance(repos, list):
            repos = []

        handle = clean_optional_str(profile.get("login") or profile_record.get("handle"))
        source_user_id = clean_optional_str(profile.get("id") or profile_record.get("source_user_id"))

        if handle is None and source_user_id is None:
            return None

        profile_url = normalize_profile_url(
            profile.get("html_url")
            or profile_record.get("profile_url")
            or (f"https://github.com/{handle}" if handle else None)
        )

        website_url = normalize_url(profile.get("blog"))

        repo_languages = [
            repo.get("language")
            for repo in repos
            if isinstance(repo, dict) and repo.get("language")
        ]

        repo_topics: list[str] = []
        repo_homepages: list[str] = []
        top_repositories: list[dict[str, Any]] = []

        for repo in repos:
            if not isinstance(repo, dict):
                continue

            topics = repo.get("topics")
            if isinstance(topics, list):
                repo_topics.extend(topics)

            homepage = normalize_url(repo.get("homepage"))
            if homepage:
                repo_homepages.append(homepage)

            top_repositories.append(
                compact_dict(
                    {
                        "name": clean_optional_str(repo.get("name")),
                        "full_name": clean_optional_str(repo.get("full_name")),
                        "html_url": normalize_url(repo.get("html_url")),
                        "language": clean_optional_str(repo.get("language")),
                        "stars": safe_int(repo.get("stargazers_count")),
                        "fork": repo.get("fork") if isinstance(repo.get("fork"), bool) else None,
                        "updated_at": clean_optional_str(repo.get("updated_at")),
                    }
                )
            )

        top_repositories = sorted(
            top_repositories,
            key=lambda item: item.get("stars", 0) or 0,
            reverse=True,
        )[:10]

        topics = dedupe_preserve_order(
            [
                *[str(language).lower() for language in repo_languages if language],
                *[str(topic).lower() for topic in repo_topics if topic],
            ],
            lowercase=True,
        )[:30]

        raw_record_ids = self._raw_record_ids(records)
        warnings: list[NormalizationWarning] = []

        if repos_record is None:
            warnings.append(
                NormalizationWarning(
                    source=PlatformSource.GITHUB,
                    raw_record_id=self._record_id(profile_record),
                    source_record_type="github/repos",
                    message="GitHub profile normalized without repos raw record.",
                )
            )

        outbound_links = normalize_url_list(
            [
                website_url,
                *repo_homepages,
                *extract_urls_from_text(profile.get("bio"), limit=5),
            ],
            limit=20,
        )

        source_account = SourceAccount(
            source=PlatformSource.GITHUB,
            source_user_id=source_user_id,
            handle=handle,
            display_name=clean_optional_str(profile.get("name")) or handle,
            bio=clean_optional_str(profile.get("bio")),
            location=clean_optional_str(profile.get("location")),
            company=clean_optional_str(profile.get("company")),
            website_url=website_url,
            profile_url=profile_url,
            avatar_url=normalize_url(profile.get("avatar_url")),
            topics=topics,
            outbound_links=outbound_links,
            raw_source_record_id=self._record_id(profile_record),
            activity_payload=compact_dict(
                {
                    "normalization_version": self.normalization_version,
                    "raw_source_record_ids": [str(item) for item in raw_record_ids],
                    "public_repos": safe_int(profile.get("public_repos")),
                    "followers": safe_int(profile.get("followers")),
                    "following": safe_int(profile.get("following")),
                    "top_languages": top_values(repo_languages, limit=10),
                    "repo_count_fetched": len(repos),
                    "top_repositories": top_repositories,
                }
            ),
        )

        return NormalizedAccountResult(
            source_account=source_account,
            raw_record_ids=raw_record_ids,
            warnings=warnings,
        )

    def _record_by_type(
        self,
        records: list[dict[str, Any]],
        record_type: str,
    ) -> dict[str, Any] | None:
        matching = [
            record
            for record in records
            if record.get("source_record_type") == record_type
        ]

        if not matching:
            return None

        return sorted(
            matching,
            key=lambda record: str(record.get("fetched_at") or record.get("created_at") or ""),
        )[-1]

    def _record_id(self, record: dict[str, Any]) -> UUID | None:
        value = record.get("id")
        return UUID(str(value)) if value else None

    def _raw_record_ids(self, records: list[dict[str, Any]]) -> list[UUID]:
        ids: list[UUID] = []

        for record in records:
            record_id = self._record_id(record)
            if record_id is not None:
                ids.append(record_id)

        return ids