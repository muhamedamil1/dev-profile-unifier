from __future__ import annotations

import html
import re
from collections import Counter
from typing import Any


_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")

_COMMON_TOPIC_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "how",
    "i",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "with",
    "you",
    "your",
}


def clean_optional_str(value: Any) -> str | None:
    if value is None:
        return None

    cleaned = str(value).strip()
    if not cleaned:
        return None

    cleaned = html.unescape(cleaned)
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip()

    return cleaned or None


def html_to_text(value: Any) -> str | None:
    cleaned = clean_optional_str(value)
    if cleaned is None:
        return None

    without_tags = _TAG_RE.sub(" ", cleaned)
    without_tags = html.unescape(without_tags)
    without_tags = _WHITESPACE_RE.sub(" ", without_tags).strip()

    return without_tags or None


def safe_int(value: Any) -> int | None:
    if value is None:
        return None

    if isinstance(value, bool):
        return None

    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def dedupe_preserve_order(values: list[Any], *, lowercase: bool = False) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []

    for value in values:
        cleaned = clean_optional_str(value)
        if cleaned is None:
            continue

        normalized = cleaned.lower() if lowercase else cleaned
        key = normalized.lower()

        if key in seen:
            continue

        seen.add(key)
        output.append(normalized)

    return output


def top_values(values: list[Any], *, limit: int = 10) -> list[str]:
    cleaned_values = [
        cleaned.lower()
        for item in values
        if (cleaned := clean_optional_str(item))
    ]

    counter = Counter(cleaned_values)

    return [
        value
        for value, _count in counter.most_common(limit)
    ]


def keyword_topics_from_texts(texts: list[Any], *, limit: int = 20) -> list[str]:
    words: list[str] = []

    for text in texts:
        cleaned = html_to_text(text)
        if cleaned is None:
            continue

        for word in re.findall(r"[a-zA-Z][a-zA-Z0-9+#.-]{1,30}", cleaned.lower()):
            if word in _COMMON_TOPIC_STOPWORDS:
                continue

            if len(word) < 2:
                continue

            words.append(word)

    return top_values(words, limit=limit)


def compact_dict(data: dict[str, Any]) -> dict[str, Any]:
    """
    Remove None values and empty lists/dicts from a metadata payload.
    """
    output: dict[str, Any] = {}

    for key, value in data.items():
        if value is None:
            continue

        if value == [] or value == {}:
            continue

        output[key] = value

    return output