from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import health, profiles
from app.config import Settings, get_settings
from app.utils.errors import DevProfileUnifierError
from app.api import ui


logger = logging.getLogger(__name__)


def configure_logging(settings: Settings) -> None:
    """
    Configure process-level logging.

    Keep this simple and Render-friendly: structured enough to debug, but still
    readable in terminal logs.
    """
    logging.basicConfig(
        level=settings.log_level.upper(),
        format=(
            "%(asctime)s | %(levelname)s | %(name)s | "
            "%(message)s"
        ),
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings)

    settings.assert_production_ready()

    logger.info(
        "Starting %s v%s in %s mode",
        settings.app_name,
        settings.app_version,
        settings.app_env,
    )

    missing = settings.missing_required_settings()
    if missing and settings.is_development:
        logger.warning(
            "Missing required production settings during development: %s",
            ", ".join(missing),
        )

    yield

    logger.info("Shutting down %s", settings.app_name)


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=(
            "Evidence-based developer profile unifier for GitHub, Stack Overflow, "
            "dev.to, and Hacker News."
        ),
        lifespan=lifespan,
    )

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["*"],
        )

    register_middleware(app)
    register_exception_handlers(app)
    register_routes(app)

    return app


def register_routes(app: FastAPI) -> None:
    app.include_router(health.router)
    app.include_router(profiles.router)
    app.include_router(ui.router)


    @app.get("/", tags=["root"])
    async def root() -> dict[str, str]:
        settings = get_settings()
        return {
            "service": settings.app_name,
            "version": settings.app_version,
            "status": "running",
            "health": "/health",
            "dashboard": "/dashboard",
            "profiles_resolve": "/profiles/resolve",
            "docs": "/docs",
        }


def register_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_context_middleware(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        start = time.perf_counter()

        request.state.request_id = request_id

        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "Unhandled request error | request_id=%s | method=%s | path=%s",
                request_id,
                request.method,
                request.url.path,
            )
            raise

        duration_ms = int((time.perf_counter() - start) * 1000)

        response.headers["x-request-id"] = request_id
        response.headers["x-process-time-ms"] = str(duration_ms)

        logger.info(
            "request completed | request_id=%s | method=%s | path=%s | "
            "status_code=%s | duration_ms=%s",
            request_id,
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )

        return response


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(DevProfileUnifierError)
    async def app_error_handler(
        request: Request,
        exc: DevProfileUnifierError,
    ) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)

        logger.warning(
            "application error | request_id=%s | code=%s | message=%s",
            request_id,
            exc.code,
            exc.message,
        )

        return JSONResponse(
            status_code=exc.status_code,
            content=exc.to_response(request_id=request_id),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)

        logger.info(
            "request validation error | request_id=%s | errors=%s",
            request_id,
            exc.errors(),
        )

        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "Request validation failed.",
                    "details": exc.errors(),
                },
                "request_id": request_id,
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        settings = get_settings()

        logger.exception(
            "unhandled error | request_id=%s | method=%s | path=%s",
            request_id,
            request.method,
            request.url.path,
        )

        message = (
            str(exc)
            if settings.is_development
            else "An unexpected server error occurred."
        )

        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_server_error",
                    "message": message,
                },
                "request_id": request_id,
            },
        )


app = create_app()