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
from app.utils.urls import normalize_profile_url, normalize_url, normalize_url_list


class StackOverflowAccountNormalizer:
    source = PlatformSource.STACKOVERFLOW
    normalization_version = "stackoverflow_normalizer_v1"

    def normalize(self, records: list[dict[str, Any]]) -> NormalizedAccountResult | None:
        user_record = self._record_by_type(records, "stackoverflow/user")
        answers_record = self._record_by_type(records, "stackoverflow/answers")
        questions_record = self._record_by_type(records, "stackoverflow/questions")

        if user_record is None:
            return None

        user_wrapper = user_record.get("raw_payload") or {}
        answers_wrapper = answers_record.get("raw_payload") if answers_record else {"items": []}
        questions_wrapper = questions_record.get("raw_payload") if questions_record else {"items": []}

        user = self._first_item(user_wrapper)
        if not user:
            return None

        source_user_id = clean_optional_str(user.get("user_id") or user_record.get("source_user_id"))
        display_name = html_to_text(user.get("display_name") or user_record.get("handle"))

        if source_user_id is None:
            return None

        answer_items = self._items(answers_wrapper)
        question_items = self._items(questions_wrapper)

        question_tags: list[str] = []
        answer_tags: list[str] = []

        for question in question_items:
            tags = question.get("tags")
            if isinstance(tags, list):
                question_tags.extend(tags)

        for answer in answer_items:
            tags = answer.get("tags")
            if isinstance(tags, list):
                answer_tags.extend(tags)

        topics = dedupe_preserve_order(
            [
                *[str(tag).lower() for tag in question_tags],
                *[str(tag).lower() for tag in answer_tags],
            ],
            lowercase=True,
        )[:30]

        profile_url = normalize_profile_url(
            user.get("link")
            or user_record.get("profile_url")
            or f"https://stackoverflow.com/users/{source_user_id}"
        )

        website_url = normalize_url(user.get("website_url"))

        raw_record_ids = self._raw_record_ids(records)
        warnings: list[NormalizationWarning] = []

        if answers_record is None:
            warnings.append(
                NormalizationWarning(
                    source=PlatformSource.STACKOVERFLOW,
                    raw_record_id=self._record_id(user_record),
                    source_record_type="stackoverflow/answers",
                    message="Stack Overflow profile normalized without answers raw record.",
                )
            )

        if questions_record is None:
            warnings.append(
                NormalizationWarning(
                    source=PlatformSource.STACKOVERFLOW,
                    raw_record_id=self._record_id(user_record),
                    source_record_type="stackoverflow/questions",
                    message="Stack Overflow profile normalized without questions raw record.",
                )
            )

        badge_counts = user.get("badge_counts")
        if not isinstance(badge_counts, dict):
            badge_counts = {}

        source_account = SourceAccount(
            source=PlatformSource.STACKOVERFLOW,
            source_user_id=source_user_id,
            handle=display_name,
            display_name=display_name,
            bio=None,
            location=clean_optional_str(user.get("location")),
            website_url=website_url,
            profile_url=profile_url,
            avatar_url=normalize_url(user.get("profile_image")),
            topics=topics,
            outbound_links=normalize_url_list([website_url], limit=10),
            raw_source_record_id=self._record_id(user_record),
            activity_payload=compact_dict(
                {
                    "normalization_version": self.normalization_version,
                    "raw_source_record_ids": [str(item) for item in raw_record_ids],
                    "reputation": safe_int(user.get("reputation")),
                    "badge_counts": badge_counts,
                    "answers_count_fetched": len(answer_items),
                    "questions_count_fetched": len(question_items),
                    "top_question_tags": top_values(question_tags, limit=10),
                    "top_answer_tags": top_values(answer_tags, limit=10),
                }
            ),
        )

        return NormalizedAccountResult(
            source_account=source_account,
            raw_record_ids=raw_record_ids,
            warnings=warnings,
        )

    def _items(self, wrapper: Any) -> list[dict[str, Any]]:
        if isinstance(wrapper, dict) and isinstance(wrapper.get("items"), list):
            return [
                item
                for item in wrapper["items"]
                if isinstance(item, dict)
            ]

        return []

    def _first_item(self, wrapper: Any) -> dict[str, Any] | None:
        items = self._items(wrapper)
        return items[0] if items else None

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