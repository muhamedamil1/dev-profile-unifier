from __future__ import annotations

import importlib.util
import sys
import types

from fastapi import FastAPI
from fastapi.testclient import TestClient


class AppError(Exception):
    def __init__(self, message: str, public_message: str | None = None):
        super().__init__(message)
        self.message = message
        self.public_message = public_message


# The real project already has these modules. The standalone overlay tests provide
# lightweight stubs so the UI routes can be validated before copying into the repo.
if importlib.util.find_spec("app.dependencies") is None:
    deps_module = types.ModuleType("app.dependencies")
    deps_module.get_health_dashboard_service = lambda: None
    deps_module.get_profile_read_service = lambda: None
    sys.modules.setdefault("app.dependencies", deps_module)

if importlib.util.find_spec("app.utils.errors") is None:
    utils_module = types.ModuleType("app.utils")
    utils_module.__path__ = []
    errors_module = types.ModuleType("app.utils.errors")
    errors_module.AppError = AppError
    sys.modules.setdefault("app.utils", utils_module)
    sys.modules.setdefault("app.utils.errors", errors_module)

from app.api.ui import AppError as RouteAppError, get_health_dashboard_service, get_profile_read_service, router

AppError = RouteAppError


class FakeProfileReadService:
    def __init__(self, profile=None, error=False):
        self.profile = profile or {
            "profile_id": "profile-1",
            "display_name": "Muhammed Amil",
            "headline": "Developer focused on Python",
            "confidence_level": "high",
            "sources": [],
            "review_candidates": [],
            "rejected_candidates": [],
            "ai_summary": {},
            "warnings": [],
        }
        self.error = error

    def get_profile(self, *, profile_id):
        if self.error:
            raise AppError(
                message="missing",
                public_message="Canonical profile was not found.",
            )
        return self.profile


class FakeHealthService:
    def get_health_snapshot(self, *, include_raw=False):
        return {
            "status": "healthy",
            "generated_at": "2026-06-30T00:00:00Z",
            "github_rate_limit": {"remaining": 0, "total": 60},
            "llm_usage": {},
            "profile_metrics": {},
            "api_calls_by_source": [],
            "warnings": [],
        }


def make_client(profile_service=None, health_service=None):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_profile_read_service] = lambda: profile_service or FakeProfileReadService()
    app.dependency_overrides[get_health_dashboard_service] = lambda: health_service or FakeHealthService()
    return TestClient(app)


def test_root_redirects_to_app():
    client = make_client()
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/app"


def test_app_home_renders_resolve_form():
    client = make_client()
    response = client.get("/app")

    assert response.status_code == 200
    assert "Resolve developer profile" in response.text
    assert "fetch('/profiles/resolve'" in response.text


def test_app_profile_detail_renders_profile():
    client = make_client()
    response = client.get("/app/profiles/profile-1")

    assert response.status_code == 200
    assert "Muhammed Amil" in response.text
    assert "Developer focused on Python" in response.text


def test_app_profile_detail_missing_returns_html_404():
    client = make_client(profile_service=FakeProfileReadService(error=True))
    response = client.get("/app/profiles/missing")

    assert response.status_code == 404
    assert "Canonical profile was not found" in response.text


def test_profile_detail_raw_api_link_is_url_encoded():
    client = make_client(
        profile_service=FakeProfileReadService(
            profile={
                "profile_id": "profile id",
                "display_name": "Jane Doe",
                "headline": "Engineer",
                "confidence_level": "medium",
                "sources": [],
                "review_candidates": [],
                "rejected_candidates": [],
                "ai_summary": {},
                "warnings": [],
            }
        )
    )
    response = client.get("/app/profiles/profile%20id")

    assert response.status_code == 200
    assert "/profiles/profile%20id" in response.text
