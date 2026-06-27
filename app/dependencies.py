from __future__ import annotations

from app.config import Settings, get_settings


def get_app_settings() -> Settings:
    """
    FastAPI dependency for accessing application settings.

    This wrapper keeps API routes independent from the concrete settings loader.
    """
    return get_settings()