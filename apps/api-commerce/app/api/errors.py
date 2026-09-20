from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.exceptions import AuthenticationError, DomainError, RateLimitedError
from app.core.logging import get_logger, request_id_var
from app.schemas.common import ErrorBody, ErrorResponse

logger = get_logger(__name__)


def error_response(
    status_code: int,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorBody(
            code=code,
            message=message,
            details=details or None,
            request_id=request_id_var.get() if request_id_var.get() != "-" else None,
        )
    )
    return JSONResponse(
        status_code=status_code, content=body.model_dump(exclude_none=True), headers=headers
    )


async def domain_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, DomainError)
    headers: dict[str, str] = {}
    if isinstance(exc, AuthenticationError):
        headers["WWW-Authenticate"] = "Bearer"
    if isinstance(exc, RateLimitedError):
        retry_after = exc.details.get("retry_after_seconds")
        if retry_after:
            headers["Retry-After"] = str(retry_after)
    if exc.status_code >= 500:
        logger.error("Domain error with server status", extra={"code": exc.error_code})
    return error_response(
        exc.status_code, exc.error_code, exc.message, exc.details, headers or None
    )


async def validation_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    fields = [
        {
            "field": ".".join(str(part) for part in error["loc"][1:]) or str(error["loc"][0]),
            "error": str(error.get("msg") or "inválido"),
        }
        for error in exc.errors()
    ]
    return error_response(
        422, "validation_error", "A requisição não passou na validação.", {"fields": fields}
    )


async def http_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    return error_response(
        exc.status_code,
        f"http_{exc.status_code}",
        str(exc.detail),
        headers=getattr(exc, "headers", None),
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception(
        "Unhandled exception",
        extra={"path": request.url.path, "method": request.method},
        exc_info=exc,
    )
    return error_response(
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "internal_error",
        "Erro interno. Informe o request_id ao suporte.",
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Must run on every mounted sub-app: handlers are not inherited."""
    app.add_exception_handler(DomainError, domain_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
