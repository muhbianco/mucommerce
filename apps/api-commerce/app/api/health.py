from __future__ import annotations

import asyncio

from fastapi import APIRouter, Response, status

from app import __version__
from app.core.config import settings
from app.core.database import check_database_health
from app.core.redis import get_redis
from app.core.storage import get_storage
from app.schemas.common import HealthResponse, ReadinessResponse

router = APIRouter(tags=["Infraestrutura"])


@router.get("/healthz", response_model=HealthResponse, summary="Liveness")
async def healthz() -> HealthResponse:
    """Process is up. Does not touch dependencies (DB down must not restart the container)."""
    return HealthResponse(status="ok", version=__version__, environment=settings.environment)


@router.get("/readyz", response_model=ReadinessResponse, summary="Readiness")
async def readyz(response: Response) -> ReadinessResponse:
    """Dependencies reachable: DB `SELECT 1` and Redis `PING` (when configured), 2 s budget.

    Storage (HEAD on the public bucket) is reported but does not gate readiness.
    """
    try:
        database_ok = await asyncio.wait_for(check_database_health(), timeout=2.0)
    except Exception:
        database_ok = False

    redis_ok: bool | None = None
    redis = get_redis()
    if redis is not None:
        try:
            redis_ok = bool(await asyncio.wait_for(redis.ping(), timeout=2.0))
        except Exception:
            redis_ok = False

    storage_ok: bool | None = None
    if settings.storage_configured:
        try:
            storage_ok = await asyncio.wait_for(
                get_storage().bucket_exists(settings.storage_public_bucket), timeout=2.0
            )
        except Exception:
            storage_ok = False

    ready = database_ok and redis_ok is not False
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(
        status="ok" if ready else "degraded",
        version=__version__,
        environment=settings.environment,
        database=database_ok,
        redis=redis_ok,
        storage=storage_ok,
    )
