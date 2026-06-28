from __future__ import annotations

from typing import Any
from uuid import UUID

from app.schemas.enums import PlatformSource
from app.schemas.normalization import NormalizationWarning, NormalizedAccountResult
from app.schemas.source_account import SourceAccount
from app.utils.normalization import (
    clean_optional_str,
    compact_dict,
    html_to_text,
    keyword_topics_from_texts,
    safe_int,
)
from app.utils.urls import extract_urls_from_text, normalize_profile_url, normalize_url, normalize_url_list


class HackerNewsAccountNormalizer:
    source = PlatformSource.HACKERNEWS
    normalization_version = "hackernews_normalizer_v1"

    def normalize(self, records: list[dict[str, Any]]) -> NormalizedAccountResult | None:
        user_record = self._record_by_type(records, "hackernews/user")
        activity_record = self._record_by_type(records, "hackernews/activity")

        if user_record is None:
            return None

        user = user_record.get("raw_payload") or {}
        activity = activity_record.get("raw_payload") if activity_record else {"hits": []}

        if not isinstance(user, dict):
            return None

        if not isinstance(activity, dict):
            activity = {"hits": []}

        username = clean_optional_str(user.get("username") or user_record.get("handle"))
        if username is None:
            return None

        hits = activity.get("hits")
        if not isinstance(hits, list):
            hits = []

        title_texts: list[str] = []
        comments = 0
        stories = 0

        recent_activity: list[dict[str, Any]] = []

        for hit in hits:
            if not isinstance(hit, dict):
                continue

            tags = hit.get("_tags")
            tags = tags if isinstance(tags, list) else []

            if "comment" in tags:
                comments += 1

            if "story" in tags:
                stories += 1

            title = (
                clean_optional_str(hit.get("title"))
                or clean_optional_str(hit.get("story_title"))
            )

            if title:
                title_texts.append(title)

            recent_activity.append(
                compact_dict(
                    {
                        "object_id": clean_optional_str(hit.get("objectID")),
                        "title": title,
                        "url": normalize_url(hit.get("url")),
                        "created_at": clean_optional_str(hit.get("created_at")),
                        "points": safe_int(hit.get("points")),
                        "type": "comment" if "comment" in tags else "story" if "story" in tags else None,
                    }
                )
            )

        about_text = html_to_text(user.get("about"))
        about_links = extract_urls_from_text(about_text, limit=5)
        website_url = about_links[0] if about_links else None

        profile_url = normalize_profile_url(
            user_record.get("profile_url")
            or f"https://news.ycombinator.com/user?id={username}"
        )

        raw_record_ids = self._raw_record_ids(records)
        warnings: list[NormalizationWarning] = []

        if activity_record is None:
            warnings.append(
                NormalizationWarning(
                    source=PlatformSource.HACKERNEWS,
                    raw_record_id=self._record_id(user_record),
                    source_record_type="hackernews/activity",
                    message="Hacker News profile normalized without activity raw record.",
                )
            )

        source_account = SourceAccount(
            source=PlatformSource.HACKERNEWS,
            source_user_id=username,
            handle=username,
            display_name=username,
            bio=about_text,
            location=None,
            website_url=website_url,
            profile_url=profile_url,
            avatar_url=None,
            topics=keyword_topics_from_texts(title_texts, limit=20),
            outbound_links=normalize_url_list(
                [
                    website_url,
                    *about_links,
                ],
                limit=20,
            ),
            raw_source_record_id=self._record_id(user_record),
            activity_payload=compact_dict(
                {
                    "normalization_version": self.normalization_version,
                    "raw_source_record_ids": [str(item) for item in raw_record_ids],
                    "karma": safe_int(user.get("karma")),
                    "created_at": clean_optional_str(user.get("created_at")),
                    "activity_count_fetched": len(hits),
                    "comments_count_fetched": comments,
                    "stories_count_fetched": stories,
                    "recent_activity": recent_activity[:10],
                    "weak_identity_source": True,
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