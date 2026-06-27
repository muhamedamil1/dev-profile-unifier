from __future__ import annotations

from functools import lru_cache

from supabase import Client, create_client

from app.config import get_settings
from app.utils.errors import StorageError


@lru_cache
def get_supabase_client() -> Client:
    """
    Create a cached Supabase client using the service role key.

    This backend is server-only. The service role key must never be exposed to
    frontend code or returned through API responses.
    """
    settings = get_settings()

    supabase_url = settings.supabase_url.strip()
    service_role_key = settings.supabase_service_role_key.get_secret_value().strip()

    if not supabase_url:
        raise StorageError(
            "SUPABASE_URL is not configured.",
            details={"setting": "SUPABASE_URL"},
        )

    if not service_role_key:
        raise StorageError(
            "SUPABASE_SERVICE_ROLE_KEY is not configured.",
            details={"setting": "SUPABASE_SERVICE_ROLE_KEY"},
        )

    return create_client(supabase_url, service_role_key)
