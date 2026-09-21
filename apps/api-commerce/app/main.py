from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

from app import __version__
from app.api import health
from app.api.errors import register_exception_handlers
from app.api.middleware import RequestContextMiddleware
from app.api.v1 import router as v1
from app.api.versioning import ApiVersion, create_version_app
from app.core.config import settings
from app.core.database import dispose_engine
from app.core.logging import configure_logging, get_logger
from app.core.observability import setup_metrics, setup_sentry
from app.core.redis import close_redis
from app.tenancy.orm_filter import register_tenant_filter
from app.workers import consumers as _consumers  # noqa: F401  (registers outbox consumers)

logger = get_logger(__name__)

API_VERSIONS: tuple[ApiVersion, ...] = (
    ApiVersion(
        name="v1",
        router=v1.router,
        summary=v1.SUMMARY,
        tags_metadata=v1.TAGS_METADATA,
    ),
)
CURRENT_VERSION = API_VERSIONS[-1]

register_tenant_filter()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_logging(settings.log_level)
    setup_sentry()
    logger.info(
        "Starting api-commerce",
        extra={"version": __version__, "environment": settings.environment},
    )
    if settings.run_migrations_on_startup:
        from app.core.bootstrap import run_migrations

        await run_migrations()
        configure_logging(settings.log_level)
    try:
        yield
    finally:
        await close_redis()
        await dispose_engine()
        logger.info("api-commerce stopped")


def create_app() -> FastAPI:
    application = FastAPI(
        title=settings.api_title,
        description=settings.api_description,
        version=__version__,
        lifespan=lifespan,
        root_path=settings.root_path,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    application.add_middleware(RequestContextMiddleware)
    if settings.cors_origin_list:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origin_list,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID"],
            expose_headers=["X-Request-ID", "Idempotent-Replayed"],
        )

    register_exception_handlers(application)
    application.include_router(health.router)
    setup_metrics(application)

    for version in API_VERSIONS:
        version_app = create_version_app(version)
        application.mount(version.prefix, version_app)
        if version.name == CURRENT_VERSION.name:
            application.mount("/api/latest", version_app)

    @application.get("/", include_in_schema=False)
    async def root() -> dict[str, object]:
        return {
            "service": "api-commerce",
            "health": "/healthz",
            "current": CURRENT_VERSION.name,
            "latest": "/api/latest",
        }

    @application.get("/robots.txt", include_in_schema=False)
    async def robots() -> PlainTextResponse:
        return PlainTextResponse("User-agent: *\nDisallow: /\n")

    return application


app = create_app()
