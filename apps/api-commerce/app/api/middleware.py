from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging import get_logger, request_id_var, tenant_id_var

logger = get_logger("api.access")

REQUEST_ID_HEADER = "X-Request-ID"
X_ROBOTS_TAG_HEADER = "X-Robots-Tag"
X_ROBOTS_TAG_VALUE = "noindex, nofollow"


class NoIndexMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers[X_ROBOTS_TAG_HEADER] = X_ROBOTS_TAG_VALUE
        return response


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Correlates logs per request (X-Request-ID) and records latency."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = (request.headers.get(REQUEST_ID_HEADER) or "").strip()
        request_id = incoming[:64] if incoming else str(uuid.uuid4())
        token = request_id_var.set(request_id)
        tenant_token = tenant_id_var.set("-")
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "Request failed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )
            request_id_var.reset(token)
            tenant_id_var.reset(tenant_token)
            raise

        response.headers[REQUEST_ID_HEADER] = request_id
        response.headers[X_ROBOTS_TAG_HEADER] = X_ROBOTS_TAG_VALUE
        logger.info(
            "Request completed",
            extra={
                "method": request.method,
                "path": request.url.path,
                "host": request.headers.get("host"),
                "status_code": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )
        request_id_var.reset(token)
        tenant_id_var.reset(tenant_token)
        return response
