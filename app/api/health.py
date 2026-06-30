from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse

from app.dependencies import get_health_dashboard_service
from app.schemas.observability import HealthDashboardResponse
from app.services.health_dashboard_service import HealthDashboardService
from app.config import get_settings
from app.ui.pages import render_dashboard_page


router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthDashboardResponse)
def health(
    include_raw: bool = Query(default=False, description="Include raw health_* view rows. Intended for local debugging."),
    service: HealthDashboardService = Depends(get_health_dashboard_service),
) -> HealthDashboardResponse:
    """Return production-useful observability metrics as JSON."""

    return service.get_health(include_raw=include_raw)


@router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
def dashboard(
    request: Request,
    include_raw: bool = Query(default=False),
    health_dashboard_service=Depends(get_health_dashboard_service),
) -> HTMLResponse:
    _validate_dashboard_token(request, health_dashboard_service)

    snapshot = _get_health_snapshot(health_dashboard_service, include_raw=include_raw)
    return HTMLResponse(
        render_dashboard_page(
            snapshot,
            include_raw=include_raw,
            token_required=_dashboard_token_configured(health_dashboard_service),
        )
    )


def _validate_dashboard_token(request: Request, health_dashboard_service: Any) -> None:
    validate = getattr(health_dashboard_service, "validate_dashboard_token", None)
    if validate is None:
        return

    if validate(
        token=request.query_params.get("token"),
        authorization=request.headers.get("authorization"),
    ):
        return

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Dashboard token required.")


def _dashboard_token_configured(health_dashboard_service: Any) -> bool:
    configured = getattr(health_dashboard_service, "dashboard_token_configured", None)
    if configured is not None:
        return bool(configured())

    settings = get_settings()
    return bool(getattr(settings, "dashboard_token", None) or getattr(settings, "DASHBOARD_TOKEN", None))


def _get_health_snapshot(health_dashboard_service: Any, *, include_raw: bool) -> Any:
    if hasattr(health_dashboard_service, "get_health_snapshot"):
        return health_dashboard_service.get_health_snapshot(include_raw=include_raw)
    return health_dashboard_service.get_health(include_raw=include_raw)
