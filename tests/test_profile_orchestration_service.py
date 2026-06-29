from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

import pytest

from app.schemas.profile_api import ProfileDetailResponse
import sys
import types


class ProfileResolveRequest:
    def __init__(self, **data):
        self._data = dict(data)

    def model_dump(self, mode=None):
        return dict(self._data)


requests_module = types.ModuleType("app.schemas.requests")
requests_module.ProfileResolveRequest = ProfileResolveRequest
sys.modules.setdefault("app.schemas.requests", requests_module)

from app.services.profile_orchestration_service import AppError, ProfileOrchestrationService


@dataclass
class ResolutionResult:
    profile_id: str
    resolution_run_id: str
    status: str = "resolved"
    result_summary: dict | None = None


class FakeResolutionService:
    def __init__(self, result):
        self.result = result

    def resolve(self, *, request, accounts=None, persist=True):
        return self.result


class FakeCanonicalBuilder:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.calls = []

    def build_by_profile_id(self, *, profile_id):
        self.calls.append(str(profile_id))
        if self.fail:
            raise RuntimeError("builder failed")
        return {"profile_id": str(profile_id)}


class FakeSummaryService:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.calls = []

    def generate_for_profile_id(self, *, profile_id, persist=True, allow_fallback=True):
        self.calls.append(str(profile_id))
        if self.fail:
            raise RuntimeError("summary failed")
        return {"profile_id": str(profile_id)}


class FakeReadService:
    def __init__(self, profile_id, run_id):
        self.profile_id = profile_id
        self.run_id = run_id

    def get_profile(self, profile_id):
        return ProfileDetailResponse(
            profile_id=self.profile_id,
            resolution_run_id=self.run_id,
            display_name="Muhammed Amil",
            warnings=[],
        )


class TypeErrorInsideResolutionService:
    def resolve(self, *, request, accounts=None, persist=True):
        raise TypeError("internal bug should not be swallowed")


def test_orchestration_success_response_serializes_request_and_merges_warnings():
    profile_id = uuid4()
    run_id = uuid4()
    request = ProfileResolveRequest(name="Muhammed Amil", github="amil122")
    service = ProfileOrchestrationService(
        resolution_service=FakeResolutionService(
            ResolutionResult(profile_id=str(profile_id), resolution_run_id=str(run_id), result_summary={"auto_match_count": 1})
        ),
        canonical_profile_service=FakeCanonicalBuilder(),
        summary_service=FakeSummaryService(fail=True),
        profile_read_service=FakeReadService(profile_id, run_id),
    )

    result = service.resolve_profile(request)

    assert result.profile_id == profile_id
    assert result.request == request.model_dump(mode="json")
    assert result.resolution_status == "resolved"
    assert result.raw_result_summary == {"auto_match_count": 1}
    assert any(warning.code == "summary_generation_failed" for warning in result.warnings)


def test_orchestration_requires_canonical_builder_success():
    profile_id = uuid4()
    run_id = uuid4()
    request = ProfileResolveRequest(name="Muhammed Amil", github="amil122")
    service = ProfileOrchestrationService(
        resolution_service=FakeResolutionService(ResolutionResult(profile_id=str(profile_id), resolution_run_id=str(run_id))),
        canonical_profile_service=FakeCanonicalBuilder(fail=True),
        profile_read_service=FakeReadService(profile_id, run_id),
    )

    with pytest.raises(RuntimeError):
        service.resolve_profile(request)


def test_orchestration_does_not_swallow_internal_type_error():
    request = ProfileResolveRequest(name="Muhammed Amil", github="amil122")
    service = ProfileOrchestrationService(
        resolution_service=TypeErrorInsideResolutionService(),
        canonical_profile_service=FakeCanonicalBuilder(),
        profile_read_service=FakeReadService(uuid4(), uuid4()),
    )

    with pytest.raises(TypeError, match="internal bug"):
        service.resolve_profile(request)


def test_orchestration_raises_when_no_compatible_resolution_method():
    request = ProfileResolveRequest(name="Muhammed Amil", github="amil122")
    service = ProfileOrchestrationService(
        resolution_service=object(),
        canonical_profile_service=FakeCanonicalBuilder(),
        profile_read_service=FakeReadService(uuid4(), uuid4()),
    )

    with pytest.raises(AppError):
        service.resolve_profile(request)
