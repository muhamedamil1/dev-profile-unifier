from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.responses import HTMLResponse

from app.dependencies import get_health_dashboard_service
from app.schemas.observability import HealthDashboardResponse
from app.services.health_dashboard_service import HealthDashboardService

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthDashboardResponse)
def health(
    include_raw: bool = Query(default=False, description="Include raw health_* view rows. Intended for local debugging."),
    service: HealthDashboardService = Depends(get_health_dashboard_service),
) -> HealthDashboardResponse:
    """Return production-useful observability metrics as JSON."""

    return service.get_health(include_raw=include_raw)


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(
    token: str | None = Query(default=None, description="Optional dashboard token when DASHBOARD_TOKEN is configured."),
    authorization: str | None = Header(default=None),
    service: HealthDashboardService = Depends(get_health_dashboard_service),
) -> HTMLResponse:
    """Simple HTML observability dashboard for manual checks."""

    if not service.validate_dashboard_token(token=token, authorization=authorization):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Dashboard authentication failed.",
        )

    return HTMLResponse(service.render_html_dashboard())
