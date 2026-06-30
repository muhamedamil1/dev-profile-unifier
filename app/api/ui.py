from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from app.dependencies import get_health_dashboard_service, get_profile_read_service
from app.ui.pages import render_error_page, render_not_found_page, render_profile_page, render_resolve_page
from app.utils.errors import AppError

router = APIRouter(tags=["ui"])


@router.get("/", include_in_schema=False)
def root_redirect() -> RedirectResponse:
    return RedirectResponse(url="/app", status_code=307)


@router.get("/app", response_class=HTMLResponse, include_in_schema=False)
def app_home(health_dashboard_service=Depends(get_health_dashboard_service)) -> HTMLResponse:
    recent_health = None
    try:
        if hasattr(health_dashboard_service, "get_health_snapshot"):
            recent_health = health_dashboard_service.get_health_snapshot(include_raw=False)
        elif hasattr(health_dashboard_service, "get_snapshot"):
            recent_health = health_dashboard_service.get_snapshot(include_raw=False)
    except Exception:
        recent_health = {"status": "degraded", "warnings": ["Health snapshot is temporarily unavailable."]}

    return HTMLResponse(render_resolve_page(recent_health=recent_health))


@router.get("/app/profiles/{profile_id}", response_class=HTMLResponse, include_in_schema=False)
def app_profile_detail(
    profile_id: str,
    profile_read_service=Depends(get_profile_read_service),
) -> HTMLResponse:
    try:
        if hasattr(profile_read_service, "get_profile"):
            profile = profile_read_service.get_profile(profile_id=profile_id)
        elif hasattr(profile_read_service, "get_profile_by_id"):
            profile = profile_read_service.get_profile_by_id(profile_id=profile_id)
        else:
            raise AppError(
                message="ProfileReadService does not expose get_profile().",
                public_message="Profile read service is not configured.",
            )
    except AppError as exc:
        public_message = getattr(exc, "public_message", None) or "Canonical profile was not found."
        return HTMLResponse(render_not_found_page(public_message), status_code=404)
    except HTTPException as exc:
        if exc.status_code == 404:
            return HTMLResponse(render_not_found_page(str(exc.detail)), status_code=404)
        return HTMLResponse(render_error_page("Profile error", str(exc.detail)), status_code=exc.status_code)

    return HTMLResponse(render_profile_page(profile))
