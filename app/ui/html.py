from __future__ import annotations

import json
from html import escape
from typing import Any
from urllib.parse import urlparse


def to_plain(value: Any) -> Any:
    """Convert Pydantic models / objects into plain Python values for rendering."""
    if value is None:
        return None
    if isinstance(value, dict):
        return {str(k): to_plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set):
        return [to_plain(item) for item in value]
    if hasattr(value, "model_dump"):
        return to_plain(value.model_dump(mode="json"))
    if hasattr(value, "__dict__") and not isinstance(value, type):
        return to_plain(vars(value))
    return value


def field(data: Any, *names: str, default: Any = None) -> Any:
    plain = to_plain(data)
    if isinstance(plain, dict):
        for name in names:
            if name in plain and plain[name] is not None:
                return plain[name]
    return default


def h(value: Any, *, default: str = "") -> str:
    if value is None:
        return escape(default)
    return escape(str(value))


def json_dumps_safe(value: Any) -> str:
    return json.dumps(to_plain(value), indent=2, sort_keys=True, default=str)


def safe_url(value: Any) -> str | None:
    if not value:
        return None
    raw = str(value).strip()
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        return None
    if not parsed.netloc:
        return None
    return raw


def safe_link(url: Any, label: Any | None = None, *, class_name: str = "link") -> str:
    clean_url = safe_url(url)
    if not clean_url:
        return h(label or "—")
    label_text = label or clean_url
    return f'<a class="{h(class_name)}" href="{h(clean_url)}" target="_blank" rel="noreferrer noopener">{h(label_text)}</a>'


def css_class(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in value.lower())
