from __future__ import annotations

import importlib
import sys
import types
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict

from app.schemas.observability import HealthDashboardResponse
from app.schemas.profile_api import ProfileDetailResponse, ProfileResolveAPIResponse


class ProfileResolveRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str | None = None
    github: str | None = None


class FakeHealthService:
    def __init__(self, *, protected: bool = False):
        self.protected = protected
        self.include_raw_values = []

    def get_health(self, *, include_raw: bool = False):
        self.include_raw_values.append(include_raw)
        return HealthDashboardResponse(
            generated_at="2026-06-29T00:00:00+00:00",
            raw_views={"x": []} if include_raw else {},
        )

    def validate_dashboard_token(self, *, token=None, authorization=None):
        if not self.protected:
            return True
        return token == "secret" or authorization == "Bearer secret"

    def render_html_dashboard(self):
        return "<html><body>ok</body></html>"


class FakeReadService:
    def __init__(self, *, missing: bool = False, error_cls=None):
        self.missing = missing
        self.error_cls = error_cls

    def get_profile(self, profile_id):
        if self.missing:
            error_cls = self.error_cls
            if error_cls is None:
                from app.services.profile_read_service import AppError as error_cls
            raise error_cls(message="missing", public_message="Canonical profile was not found.")
        return ProfileDetailResponse(profile_id=profile_id, display_name="Muhammed Amil")


class FakeOrchestrationService:
    def resolve_profile(self, request, *, build_summary=True, allow_summary_fallback=True, persist=True):
        profile_id = uuid4()
        return ProfileResolveAPIResponse(
            profile_id=profile_id,
            display_name="Muhammed Amil",
            request=request.model_dump(mode="json"),
            resolution_status="resolved",
            resolution_duration_ms=1,
        )


@pytest.fixture()
def app_with_routes(monkeypatch):
    requests_module = types.ModuleType("app.schemas.requests")
    requests_module.ProfileResolveRequest = ProfileResolveRequest
    monkeypatch.setitem(sys.modules, "app.schemas.requests", requests_module)

    deps = types.ModuleType("app.dependencies")
    deps.get_health_dashboard_service = lambda: FakeHealthService()
    deps.get_profile_read_service = lambda: FakeReadService()
    deps.get_profile_orchestration_service = lambda: FakeOrchestrationService()
    monkeypatch.setitem(sys.modules, "app.dependencies", deps)

    health_module = importlib.import_module("app.api.health")
    profiles_module = importlib.import_module("app.api.profiles")
    health_module = importlib.reload(health_module)
    profiles_module = importlib.reload(profiles_module)

    app = FastAPI()
    app.include_router(health_module.router)
    app.include_router(profiles_module.router)
    return app, deps, health_module, profiles_module


def test_health_route_passes_include_raw(app_with_routes):
    app, _deps, health_module, _profiles_module = app_with_routes
    service = FakeHealthService()
    app.dependency_overrides[health_module.get_health_dashboard_service] = lambda: service
    client = TestClient(app)

    response = client.get("/health?include_raw=true")

    assert response.status_code == 200
    assert response.json()["raw_views"] == {"x": []}
    assert service.include_raw_values == [True]


def test_dashboard_route_can_require_token(app_with_routes):
    app, _deps, health_module, _profiles_module = app_with_routes
    app.dependency_overrides[health_module.get_health_dashboard_service] = lambda: FakeHealthService(protected=True)
    client = TestClient(app)

    assert client.get("/dashboard").status_code == 401
    assert client.get("/dashboard?token=secret").status_code == 200
    assert client.get("/dashboard", headers={"Authorization": "Bearer secret"}).status_code == 200


def test_get_profile_route_maps_missing_profile_to_404(app_with_routes):
    app, _deps, _health_module, profiles_module = app_with_routes
    app.dependency_overrides[profiles_module.get_profile_read_service] = lambda: FakeReadService(missing=True, error_cls=profiles_module.AppError)
    client = TestClient(app)

    response = client.get(f"/profiles/{uuid4()}")

    assert response.status_code == 404


def test_resolve_profile_route_returns_response(app_with_routes):
    app, _deps, _health_module, profiles_module = app_with_routes
    app.dependency_overrides[profiles_module.get_profile_orchestration_service] = lambda: FakeOrchestrationService()
    client = TestClient(app)

    response = client.post("/profiles/resolve", json={"name": "Muhammed Amil", "github": "amil122"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["display_name"] == "Muhammed Amil"
    assert payload["request"] == {"name": "Muhammed Amil", "github": "amil122"}
    assert payload["resolution_status"] == "resolved"
