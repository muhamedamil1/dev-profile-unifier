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
    html_to_text,
    safe_int,
    top_values,
)
from app.utils.urls import extract_urls_from_text, normalize_profile_url, normalize_url, normalize_url_list


class DevToAccountNormalizer:
    source = PlatformSource.DEVTO
    normalization_version = "devto_normalizer_v1"

    def normalize(self, records: list[dict[str, Any]]) -> NormalizedAccountResult | None:
        user_record = self._record_by_type(records, "devto/user")
        articles_record = self._record_by_type(records, "devto/articles")

        if user_record is None:
            return None

        user = user_record.get("raw_payload") or {}
        articles = articles_record.get("raw_payload") if articles_record else []

        if not isinstance(user, dict):
            return None

        if not isinstance(articles, list):
            articles = []

        username = clean_optional_str(user.get("username") or user_record.get("handle"))
        source_user_id = clean_optional_str(user.get("id") or username)

        if username is None and source_user_id is None:
            return None

        profile_url = normalize_profile_url(
            user.get("url")
            or user_record.get("profile_url")
            or (f"https://dev.to/{username}" if username else None)
        )

        website_url = normalize_url(user.get("website_url"))

        article_tags: list[str] = []
        recent_articles: list[dict[str, Any]] = []

        for article in articles:
            if not isinstance(article, dict):
                continue

            article_tags.extend(self._article_tags(article))

            recent_articles.append(
                compact_dict(
                    {
                        "title": clean_optional_str(article.get("title")),
                        "url": normalize_url(article.get("url")),
                        "canonical_url": normalize_url(article.get("canonical_url")),
                        "published_at": clean_optional_str(article.get("published_at")),
                        "positive_reactions_count": safe_int(
                            article.get("positive_reactions_count")
                        ),
                        "comments_count": safe_int(article.get("comments_count")),
                    }
                )
            )

        topics = dedupe_preserve_order(
            [str(tag).lower() for tag in article_tags],
            lowercase=True,
        )[:30]

        github_username = clean_optional_str(user.get("github_username"))
        twitter_username = clean_optional_str(user.get("twitter_username"))

        linked_profile_urls: list[str] = []

        if github_username:
            linked_profile_urls.append(f"https://github.com/{github_username}")

        raw_record_ids = self._raw_record_ids(records)
        warnings: list[NormalizationWarning] = []

        if articles_record is None:
            warnings.append(
                NormalizationWarning(
                    source=PlatformSource.DEVTO,
                    raw_record_id=self._record_id(user_record),
                    source_record_type="devto/articles",
                    message="dev.to profile normalized without articles raw record.",
                )
            )

        source_account = SourceAccount(
            source=PlatformSource.DEVTO,
            source_user_id=source_user_id,
            handle=username,
            display_name=clean_optional_str(user.get("name")) or username,
            bio=html_to_text(user.get("summary")),
            location=clean_optional_str(user.get("location")),
            website_url=website_url,
            profile_url=profile_url,
            avatar_url=normalize_url(
                user.get("profile_image")
                or user.get("profile_image_90")
            ),
            topics=topics,
            outbound_links=normalize_url_list(
                [
                    website_url,
                    *linked_profile_urls,
                    *extract_urls_from_text(user.get("summary"), limit=5),
                ],
                limit=20,
            ),
            raw_source_record_id=self._record_id(user_record),
            activity_payload=compact_dict(
                {
                    "normalization_version": self.normalization_version,
                    "raw_source_record_ids": [str(item) for item in raw_record_ids],
                    "articles_count_fetched": len(articles),
                    "top_tags": top_values(article_tags, limit=10),
                    "recent_articles": recent_articles[:10],
                    "linked_usernames": compact_dict(
                        {
                            "github": github_username,
                            "twitter": twitter_username,
                        }
                    ),
                }
            ),
        )

        return NormalizedAccountResult(
            source_account=source_account,
            raw_record_ids=raw_record_ids,
            warnings=warnings,
        )

    def _article_tags(self, article: dict[str, Any]) -> list[str]:
        tag_list = article.get("tag_list")
        tags = article.get("tags")

        output: list[str] = []

        if isinstance(tag_list, list):
            output.extend(str(item) for item in tag_list)

        elif isinstance(tag_list, str):
            output.extend(
                item.strip()
                for item in tag_list.split(",")
                if item.strip()
            )

        if isinstance(tags, list):
            output.extend(str(item) for item in tags)

        elif isinstance(tags, str):
            output.extend(
                item.strip()
                for item in tags.split(",")
                if item.strip()
            )

        return output

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