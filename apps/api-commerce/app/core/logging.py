from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from typing import Any

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
tenant_id_var: ContextVar[str] = ContextVar("tenant_id", default="-")

_RESERVED = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}

# Keys whose values must never reach the logs, whatever the nesting.
_SENSITIVE_KEYS = {
    "authorization",
    "x-signature",
    "x-internal-token",
    "x-chatwoot-signature",
    "access_token",
    "identification",
    "x-customer-session",
    "x-fake-signature",
    "refresh_token",
    "token",
    "password",
    "secret",
    "client_secret",
    "webhook_secret",
    "card",
    "cvv",
    "document",
}


def redact(value: Any, depth: int = 0) -> Any:
    """Recursively replace sensitive values with a marker. Safe for logs."""
    if depth > 6:
        return "<...>"
    if isinstance(value, dict):
        return {
            key: ("<redacted>" if str(key).lower() in _SENSITIVE_KEYS else redact(val, depth + 1))
            for key, val in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact(item, depth + 1) for item in value]
    return value


class JsonFormatter(logging.Formatter):
    """One JSON object per line: Portainer, Alloy and journald read it without a parser."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_var.get(),
            "tenant_id": tenant_id_var.get(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        extras = {k: v for k, v in record.__dict__.items() if k not in _RESERVED}
        if extras:
            payload["context"] = redact(extras)

        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())

    logging.getLogger("uvicorn.access").disabled = True
    for noisy in ("uvicorn.error", "sqlalchemy.engine", "celery"):
        logging.getLogger(noisy).handlers = [handler]


class _SafeExtraAdapter(logging.LoggerAdapter):  # type: ignore[type-arg]
    def process(self, msg: str, kwargs: Any) -> tuple[str, Any]:
        extra = kwargs.get("extra")
        if not isinstance(extra, dict) or not extra:
            return msg, kwargs
        safe: dict[str, Any] = {}
        for key, value in extra.items():
            safe[f"ctx_{key}" if key in _RESERVED else key] = value
        kwargs["extra"] = safe
        return msg, kwargs


def get_logger(name: str) -> logging.LoggerAdapter:  # type: ignore[type-arg]
    """Logger adapter that renames reserved `extra` keys and keeps JSON output stable."""
    return _SafeExtraAdapter(logging.getLogger(name), {})
