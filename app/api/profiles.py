from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.dependencies import get_profile_orchestration_service, get_profile_read_service
from app.schemas.profile_api import ProfileDetailResponse, ProfileResolveAPIResponse
from app.schemas.requests import ProfileResolveRequest
from app.services.profile_orchestration_service import ProfileOrchestrationService
from app.services.profile_read_service import ProfileReadService
try:
    from app.utils.errors import AppError
except ImportError:  # pragma: no cover - only used when running overlay tests outside the full repo
    class AppError(RuntimeError):
        def __init__(self, *, message: str, public_message: str | None = None) -> None:
            super().__init__(message)
            self.message = message
            self.public_message = public_message or message

router = APIRouter(prefix="/profiles", tags=["profiles"])


@router.post("/resolve", response_model=ProfileResolveAPIResponse)
def resolve_profile(
    request: ProfileResolveRequest,
    build_summary: bool = Query(default=True, description="Generate Phase 9 AI summary after deterministic resolution."),
    allow_summary_fallback: bool = Query(default=True, description="Use deterministic fallback summary if Gemini fails."),
    service: ProfileOrchestrationService = Depends(get_profile_orchestration_service),
) -> ProfileResolveAPIResponse:
    """Resolve public developer identities into one canonical profile.

    The resolver is deterministic-first. Optional Gemini review/summary layers are
    bounded by deterministic guardrails and do not act as the source of truth.
    """

    return service.resolve_profile(
        request,
        build_summary=build_summary,
        allow_summary_fallback=allow_summary_fallback,
        persist=True,
    )


@router.get("/{profile_id}", response_model=ProfileDetailResponse)
def get_profile(
    profile_id: UUID,
    service: ProfileReadService = Depends(get_profile_read_service),
) -> ProfileDetailResponse:
    """Return a canonical profile, accepted sources, review candidates, and AI summary."""

    try:
        return service.get_profile(profile_id)
    except AppError as exc:
        message = getattr(exc, "public_message", None) or "Canonical profile was not found."
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message) from exc
