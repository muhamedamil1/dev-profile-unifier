from __future__ import annotations

import inspect
import time
from typing import Any, Callable
from uuid import UUID

from app.schemas.profile_api import APIWarning, ProfileResolveAPIResponse
from app.schemas.requests import ProfileResolveRequest
try:
    from app.utils.errors import AppError
except ImportError:  # pragma: no cover - only used when running overlay tests outside the full repo
    class AppError(RuntimeError):
        def __init__(self, *, message: str, public_message: str | None = None) -> None:
            super().__init__(message)
            self.message = message
            self.public_message = public_message or message


class ProfileOrchestrationService:
    """Application workflow for POST /profiles/resolve.

    This class is intentionally thin: each pipeline phase remains owned by the
    services already built in Phases 6–9. This service coordinates them, records
    timing, and returns a read-model response.

    Production contract:
    - resolution persistence is required
    - deterministic canonical profile build is required
    - profile readback is required
    - AI summary generation is optional and returns warnings on failure
    """

    def __init__(
        self,
        *,
        ingestion_service=None,
        normalization_service=None,
        resolution_service=None,
        canonical_profile_service=None,
        summary_service=None,
        profile_read_service=None,
        settings=None,
    ) -> None:
        self.ingestion_service = ingestion_service
        self.normalization_service = normalization_service
        self.resolution_service = resolution_service
        self.canonical_profile_service = canonical_profile_service
        self.summary_service = summary_service
        self.profile_read_service = profile_read_service
        self.settings = settings

    def resolve_profile(
        self,
        request: ProfileResolveRequest,
        *,
        build_summary: bool = True,
        allow_summary_fallback: bool = True,
        persist: bool = True,
    ) -> ProfileResolveAPIResponse:
        started = time.perf_counter()
        warnings: list[APIWarning] = []

        if not self.resolution_service:
            raise AppError(
                message="Resolution service is not configured.",
                public_message="Profile resolution is not available.",
            )

        ingestion_result = self._run_optional_ingestion(request=request, persist=persist, warnings=warnings)
        normalized_accounts = self._run_optional_normalization(
            ingestion_result=ingestion_result,
            request=request,
            persist=persist,
            warnings=warnings,
        )

        resolution_result = self._run_resolution(
            request=request,
            normalized_accounts=normalized_accounts,
            ingestion_result=ingestion_result,
            persist=persist,
        )

        profile_id = self._extract_profile_id(resolution_result)
        resolution_run_id = self._extract_resolution_run_id(resolution_result)

        if not profile_id:
            raise AppError(
                message="Resolution completed without a canonical profile id.",
                public_message="Profile resolution did not produce a canonical profile.",
            )

        self._build_canonical_profile(profile_id=profile_id, resolution_run_id=resolution_run_id)
        self._generate_summary(
            profile_id=profile_id,
            enabled=build_summary,
            allow_fallback=allow_summary_fallback,
            persist=persist,
            warnings=warnings,
        )

        if not self.profile_read_service:
            raise AppError(
                message="Profile read service is not configured.",
                public_message="Resolved profile could not be loaded.",
            )

        detail = self.profile_read_service.get_profile(profile_id)
        duration_ms = int((time.perf_counter() - started) * 1000)

        detail_payload = detail.model_dump()
        detail_payload["warnings"] = [*detail.warnings, *warnings]
        detail_payload["request"] = request.model_dump(mode="json")
        detail_payload["resolution_status"] = self._extract_resolution_status(resolution_result)
        detail_payload["resolution_duration_ms"] = duration_ms
        detail_payload["raw_result_summary"] = self._extract_result_summary(resolution_result)
        return ProfileResolveAPIResponse(**detail_payload)

    def _run_optional_ingestion(
        self,
        *,
        request: ProfileResolveRequest,
        persist: bool,
        warnings: list[APIWarning],
    ) -> Any:
        if not self.ingestion_service:
            warnings.append(
                APIWarning(
                    code="ingestion_service_missing",
                    message="Ingestion service was not configured; assuming resolution service handles ingestion.",
                )
            )
            return None

        return self._call_first_compatible(
            self.ingestion_service,
            [
                ("ingest_for_request", {"request": request, "persist": persist}),
                ("ingest", {"request": request, "persist": persist}),
                ("run", {"request": request, "persist": persist}),
            ],
        )

    def _run_optional_normalization(
        self,
        *,
        ingestion_result: Any,
        request: ProfileResolveRequest,
        persist: bool,
        warnings: list[APIWarning],
    ) -> Any:
        if not self.normalization_service:
            warnings.append(
                APIWarning(
                    code="normalization_service_missing",
                    message="Normalization service was not configured; assuming resolution service handles normalization.",
                )
            )
            return None

        return self._call_first_compatible(
            self.normalization_service,
            [
                ("normalize_ingestion_result", {"ingestion_result": ingestion_result, "persist": persist}),
                ("normalize_for_request", {"request": request, "ingestion_result": ingestion_result, "persist": persist}),
                ("normalize", {"ingestion_result": ingestion_result, "persist": persist}),
                ("run", {"ingestion_result": ingestion_result, "persist": persist}),
            ],
        )

    def _run_resolution(
        self,
        *,
        request: ProfileResolveRequest,
        normalized_accounts: Any,
        ingestion_result: Any,
        persist: bool,
    ) -> Any:
        return self._call_first_compatible(
            self.resolution_service,
            [
                ("resolve", {"request": request, "accounts": normalized_accounts, "persist": persist}),
                ("resolve_profile", {"request": request, "accounts": normalized_accounts, "persist": persist}),
                ("run", {"request": request, "accounts": normalized_accounts, "ingestion_result": ingestion_result, "persist": persist}),
                ("resolve_request", {"request": request, "persist": persist}),
            ],
        )

    def _build_canonical_profile(self, *, profile_id: str | UUID, resolution_run_id: str | UUID | None) -> None:
        if not self.canonical_profile_service:
            raise AppError(
                message="Canonical profile builder is not configured.",
                public_message="Resolved profile could not be built.",
            )

        if hasattr(self.canonical_profile_service, "build_by_profile_id"):
            self.canonical_profile_service.build_by_profile_id(profile_id=profile_id)
            return

        if resolution_run_id and hasattr(self.canonical_profile_service, "build_by_resolution_run_id"):
            self.canonical_profile_service.build_by_resolution_run_id(resolution_run_id=resolution_run_id)
            return

        raise AppError(
            message="Canonical profile builder has no compatible build method.",
            public_message="Resolved profile could not be built.",
        )

    def _generate_summary(
        self,
        *,
        profile_id: str | UUID,
        enabled: bool,
        allow_fallback: bool,
        persist: bool,
        warnings: list[APIWarning],
    ) -> None:
        if not enabled:
            return
        if not self.summary_service:
            warnings.append(APIWarning(code="summary_service_missing", message="Summary service was not configured."))
            return
        try:
            self.summary_service.generate_for_profile_id(
                profile_id=profile_id,
                persist=persist,
                allow_fallback=allow_fallback,
            )
        except Exception as exc:  # noqa: BLE001 - summary is optional; identity resolution should still return
            warnings.append(
                APIWarning(
                    code="summary_generation_failed",
                    message="AI summary generation failed.",
                    details={"error_type": type(exc).__name__},
                )
            )

    @staticmethod
    def _call_first_compatible(target: Any, candidates: list[tuple[str, dict[str, Any]]]) -> Any:
        """Call the first method whose signature can accept the provided kwargs.

        This avoids the unsafe pattern of catching every TypeError from inside a
        service method. If a compatible method raises TypeError internally, that
        TypeError is allowed to surface because it represents a real bug.
        """

        skipped: list[str] = []
        for method_name, kwargs in candidates:
            method = getattr(target, method_name, None)
            if not callable(method):
                skipped.append(f"{method_name}:missing")
                continue

            compatible, filtered_kwargs, reason = ProfileOrchestrationService._compatible_kwargs(method, kwargs)
            if not compatible:
                skipped.append(f"{method_name}:{reason}")
                continue

            return method(**filtered_kwargs)

        names = ", ".join(name for name, _ in candidates)
        raise AppError(
            message=(
                f"No compatible method found on {target.__class__.__name__}; tried {names}. "
                f"Skipped: {', '.join(skipped)}"
            ),
            public_message="Profile resolution pipeline is not correctly configured.",
        )

    @staticmethod
    def _compatible_kwargs(method: Callable[..., Any], kwargs: dict[str, Any]) -> tuple[bool, dict[str, Any], str]:
        try:
            signature = inspect.signature(method)
        except (TypeError, ValueError):
            return True, dict(kwargs), "signature_unavailable"

        parameters = signature.parameters
        accepts_var_kwargs = any(param.kind == inspect.Parameter.VAR_KEYWORD for param in parameters.values())
        accepted: dict[str, Any] = {}

        for key, value in kwargs.items():
            if accepts_var_kwargs or key in parameters:
                accepted[key] = value

        missing_required: list[str] = []
        for name, param in parameters.items():
            if name == "self":
                continue
            if param.kind in {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD}:
                continue
            if param.default is not inspect.Parameter.empty:
                continue
            if name not in accepted:
                missing_required.append(name)

        if missing_required:
            return False, accepted, "missing_required=" + "+".join(missing_required)

        return True, accepted, "compatible"

    @staticmethod
    def _extract_profile_id(result: Any) -> str | UUID | None:
        for key in ("canonical_profile_id", "profile_id", "id"):
            value = ProfileOrchestrationService._get_value(result, key)
            if value:
                return value
        profile = ProfileOrchestrationService._get_value(result, "profile")
        if isinstance(profile, dict) and profile.get("id"):
            return profile["id"]
        return None

    @staticmethod
    def _extract_resolution_run_id(result: Any) -> str | UUID | None:
        for key in ("resolution_run_id", "run_id"):
            value = ProfileOrchestrationService._get_value(result, key)
            if value:
                return value
        run = ProfileOrchestrationService._get_value(result, "resolution_run")
        if isinstance(run, dict) and run.get("id"):
            return run["id"]
        return None

    @staticmethod
    def _extract_resolution_status(result: Any) -> str | None:
        value = ProfileOrchestrationService._get_value(result, "status") or ProfileOrchestrationService._get_value(result, "resolution_status")
        return str(value.value if hasattr(value, "value") else value) if value is not None else None

    @staticmethod
    def _extract_result_summary(result: Any) -> dict[str, Any]:
        for key in ("result_summary", "summary", "metadata"):
            value = ProfileOrchestrationService._get_value(result, key)
            if isinstance(value, dict):
                return dict(value)
        return {}

    @staticmethod
    def _get_value(obj: Any, key: str) -> Any:
        if isinstance(obj, dict):
            return obj.get(key)
        return getattr(obj, key, None)
