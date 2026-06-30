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

    def __getattr__(self, name):
        try:
            return self._data[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def model_dump(self, mode=None):
        return dict(self._data)

    @property
    def provided_sources(self):
        return [types.SimpleNamespace(value=key) for key in ("github", "devto", "hackernews", "stackoverflow_user_id") if self._data.get(key)]

    def safe_input_payload(self):
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

class FakeResolutionRunsRepo:
    def __init__(self, run_id):
        self.run_id = str(run_id)
        self.calls = []

    def create_run(self, *, input_name, input_payload, sources_attempted=None):
        self.calls.append(
            {
                "input_name": input_name,
                "input_payload": input_payload,
                "sources_attempted": sources_attempted or [],
            }
        )
        return {"id": self.run_id}


class AsyncRequiredRunIngestionService:
    def __init__(self):
        self.calls = []

    async def ingest(self, *, request, resolution_run_id):
        self.calls.append({"request": request, "resolution_run_id": str(resolution_run_id)})
        return {"resolution_run_id": str(resolution_run_id)}


class RequiredRunNormalizationService:
    def __init__(self, account):
        self.account = account
        self.calls = []

    def normalize_run(self, *, resolution_run_id, persist=True):
        self.calls.append({"resolution_run_id": str(resolution_run_id), "persist": persist})
        return {"accounts": [{"source_account": self.account}]}


class RequiredRunResolutionService:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def resolve(self, *, resolution_run_id, request, accounts, persist=True):
        self.calls.append(
            {
                "resolution_run_id": str(resolution_run_id),
                "request": request,
                "accounts": accounts,
                "persist": persist,
            }
        )
        return self.result


def test_orchestration_creates_and_threads_resolution_run_id_through_real_phase_signatures():
    profile_id = uuid4()
    run_id = uuid4()
    account = object()
    request = ProfileResolveRequest(name="Muhammed Amil", github="amil122")
    runs_repo = FakeResolutionRunsRepo(run_id)
    ingestion_service = AsyncRequiredRunIngestionService()
    normalization_service = RequiredRunNormalizationService(account)
    resolution_service = RequiredRunResolutionService(
        ResolutionResult(profile_id=str(profile_id), resolution_run_id=str(run_id), result_summary={"auto_match_count": 1})
    )
    service = ProfileOrchestrationService(
        ingestion_service=ingestion_service,
        normalization_service=normalization_service,
        resolution_service=resolution_service,
        canonical_profile_service=FakeCanonicalBuilder(),
        profile_read_service=FakeReadService(profile_id, run_id),
        resolution_runs_repo=runs_repo,
    )

    result = service.resolve_profile(request, build_summary=False)

    assert result.profile_id == profile_id
    assert runs_repo.calls == [
        {
            "input_name": "Muhammed Amil",
            "input_payload": request.safe_input_payload(),
            "sources_attempted": ["github"],
        }
    ]
    assert ingestion_service.calls == [{"request": request, "resolution_run_id": str(run_id)}]
    assert normalization_service.calls == [{"resolution_run_id": str(run_id), "persist": True}]
    assert resolution_service.calls == [
        {
            "resolution_run_id": str(run_id),
            "request": request,
            "accounts": [account],
            "persist": True,
        }
    ]
