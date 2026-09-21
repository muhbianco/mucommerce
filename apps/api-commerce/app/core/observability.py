from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from app import __version__
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def setup_sentry() -> None:
    if not settings.sentry_dsn:
        return
    try:
        import sentry_sdk
        from sentry_sdk.integrations.celery import CeleryIntegration
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration
    except ImportError:  # pragma: no cover - optional dependency
        logger.warning("sentry-sdk not installed; SENTRY_DSN ignored")
        return

    def _scrub(event: Any, hint: Any) -> Any:
        del hint
        request = event.get("request") if isinstance(event, dict) else None
        if isinstance(request, dict):
            headers = request.get("headers")
            if isinstance(headers, dict):
                for key in list(headers):
                    if key.lower() in {
                        "authorization",
                        "cookie",
                        "x-internal-token",
                        "x-signature",
                    }:
                        headers[key] = "<redacted>"
            request.pop("data", None)
        return event

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        release=f"api-commerce@{__version__}",
        send_default_pii=False,
        traces_sample_rate=0.1,
        before_send=_scrub,
        integrations=[StarletteIntegration(), FastApiIntegration(), CeleryIntegration()],
    )


def setup_metrics(app: FastAPI) -> None:
    if not settings.metrics_enabled:
        return
    try:
        from prometheus_fastapi_instrumentator import Instrumentator
    except ImportError:  # pragma: no cover
        return
    Instrumentator(
        should_group_status_codes=True,
        should_ignore_untemplated=True,
        excluded_handlers=["/metrics", "/healthz", "/readyz"],
    ).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
