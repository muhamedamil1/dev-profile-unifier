from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse, urlunparse

from app.utils.normalization import clean_optional_str, dedupe_preserve_order


_URL_RE = re.compile(
    r"(?P<url>(?:https?://)?(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?:/[^\s<>\"]*)?)"
)


def _strip_url_trailing_punctuation(value: str) -> str:
    return value.rstrip('.,;:!?)\\\"]}\'')


def normalize_url(value: Any) -> str | None:
    cleaned = clean_optional_str(value)

    if cleaned is None:
        return None

    if " " in cleaned:
        return None

    if cleaned.startswith("//"):
        cleaned = f"https:{cleaned}"

    if not cleaned.startswith(("http://", "https://")):
        if "." not in cleaned:
            return None
        cleaned = f"https://{cleaned}"

    parsed = urlparse(cleaned)

    if parsed.scheme not in {"http", "https"}:
        return None

    if not parsed.netloc:
        return None

    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/")

    return urlunparse(
        (
            parsed.scheme.lower(),
            netloc,
            path,
            "",
            parsed.query,
            "",
        )
    )


def normalize_profile_url(value: Any) -> str | None:
    return normalize_url(value)


def extract_urls_from_text(value: Any, *, limit: int = 10) -> list[str]:
    cleaned = clean_optional_str(value)
    if cleaned is None:
        return []

    urls: list[str] = []

    for match in _URL_RE.finditer(cleaned):
        normalized = normalize_url(_strip_url_trailing_punctuation(match.group("url")))
        if normalized:
            urls.append(normalized)

    return dedupe_preserve_order(urls)[:limit]


def normalize_url_list(values: list[Any], *, limit: int = 20) -> list[str]:
    normalized = [
        url
        for value in values
        if (url := normalize_url(value))
    ]

    return dedupe_preserve_order(normalized)[:limit]
