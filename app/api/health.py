from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends

from app.config import Settings
from app.dependencies import get_app_settings

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(settings: Settings = Depends(get_app_settings)) -> dict[str, Any]:
    """
    Liveness/configuration health endpoint.

    This is intentionally real and useful in Phase 1:
    - confirms the API process is alive
    - confirms config loaded successfully
    - exposes missing required settings without leaking secret values

    In the observability phase, this same endpoint will be expanded with database-
    aggregated API calls, GitHub rate limits, profile counts, and LLM token usage.
    """
    missing = settings.missing_required_settings()
    status = "ok" if not missing else "degraded"

    return {
        "status": status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "service": settings.app_name,
        "version": settings.app_version,
        "environment": settings.app_env,
        "checks": {
            "api": "ok",
            "config": "ok" if not missing else "missing_required_settings",
        },
        "config": settings.safe_runtime_config(),
    }