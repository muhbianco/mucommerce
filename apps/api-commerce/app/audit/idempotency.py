from __future__ import annotations

import functools
import hashlib
import json
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any, ParamSpec

from fastapi import Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import IdempotencyKey
from app.core.exceptions import (
    IdempotencyInProgressError,
    IdempotencyKeyRequiredError,
    IdempotencyKeyReusedError,
)
from app.models.base import utcnow
from app.tenancy.context import CROSS_TENANT_OPTION, session_tenant_id

IDEMPOTENCY_HEADER = "Idempotency-Key"
REPLAYED_HEADER = "Idempotent-Replayed"
TTL = timedelta(hours=24)
LOCK_TIMEOUT = timedelta(minutes=2)

P = ParamSpec("P")


async def _request_hash(request: Request) -> str:
    body = await request.body()
    material = b"|".join([request.method.encode(), request.url.path.encode(), body])
    return hashlib.sha256(material).hexdigest()


def _serialize(result: Any) -> tuple[int, Any]:
    if isinstance(result, JSONResponse):
        return result.status_code, json.loads(bytes(result.body))
    if isinstance(result, Response):
        return result.status_code, {"raw": bytes(result.body).decode("utf-8", "replace")}
    if isinstance(result, BaseModel):
        return 200, result.model_dump(mode="json")
    return 200, jsonable_encoder(result)


def idempotent(
    scope: str, *, status_code: int = 200, required: bool = True
) -> Callable[[Callable[P, Awaitable[Any]]], Callable[P, Awaitable[Any]]]:
    """Wrap an endpoint so a repeated `Idempotency-Key` replays the first response.

    The endpoint must receive `request: Request` and `session: AsyncSession`
    as keyword parameters (FastAPI injects both). Behaviour:

    - missing header → 422 `idempotency_key_required` (with `required=False` the request just
      runs without replay protection: for creates where a duplicate is harmless to retry)
    - same key, same payload, finished → stored response + `Idempotent-Replayed: true`
    - same key, same payload, still running → 409 `idempotency_in_progress`
    - same key, different payload → 422 `idempotency_key_reused`
    """

    def decorator(func: Callable[P, Awaitable[Any]]) -> Callable[P, Awaitable[Any]]:
        @functools.wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
            request = kwargs.get("request")
            session = kwargs.get("session")
            if not isinstance(request, Request) or not isinstance(session, AsyncSession):
                raise RuntimeError("idempotent() needs `request` and `session` kwargs")

            key = (request.headers.get(IDEMPOTENCY_HEADER) or "").strip()
            if not key and not required:
                return await func(*args, **kwargs)
            if not key or len(key) > 128:
                raise IdempotencyKeyRequiredError()

            tenant_id = session_tenant_id(session) or "-"
            req_hash = await _request_hash(request)
            existing = await _find(session, scope, tenant_id, key)
            if existing is not None and existing.expires_at <= utcnow():
                # Past its TTL the key is free again. The unique row is reused (the purge
                # job may not have deleted it yet), starting over as a first request.
                existing.request_hash = req_hash
                existing.response_status = None
                existing.response_body = None
                existing.locked_at = None
                existing.expires_at = utcnow() + TTL

            if existing is not None:
                if existing.request_hash != req_hash:
                    raise IdempotencyKeyReusedError()
                if existing.response_status is not None:
                    return JSONResponse(
                        status_code=existing.response_status,
                        content=existing.response_body,
                        headers={REPLAYED_HEADER: "true"},
                    )
                if existing.locked_at and existing.locked_at > utcnow() - LOCK_TIMEOUT:
                    raise IdempotencyInProgressError()
                record = existing
                record.locked_at = utcnow()
                record.request_hash = req_hash
                await session.flush()
            else:
                record = IdempotencyKey(
                    scope=scope,
                    tenant_id=tenant_id,
                    idem_key=key,
                    request_hash=req_hash,
                    locked_at=utcnow(),
                    expires_at=utcnow() + TTL,
                )
                session.add(record)
                try:
                    await session.flush()
                except IntegrityError as exc:
                    # Lost the race against a concurrent identical request. Nothing
                    # else was written yet, so a full rollback is safe here.
                    await session.rollback()
                    raise IdempotencyInProgressError() from exc

            result = await func(*args, **kwargs)
            stored_status, stored_body = _serialize(result)
            if stored_status == 200 and status_code != 200:
                stored_status = status_code
            record.response_status = stored_status
            record.response_body = stored_body
            record.locked_at = None
            await session.flush()
            return result

        return wrapper

    return decorator


async def purge_expired(
    session: AsyncSession, *, batch_size: int = 500, max_batches: int = 20
) -> int:
    """Delete keys past their TTL in bounded batches (daily beat job). Returns rows deleted."""
    deleted = 0
    for _ in range(max_batches):
        ids = list(
            (
                await session.execute(
                    select(IdempotencyKey.id)
                    .where(IdempotencyKey.expires_at < utcnow())
                    .limit(batch_size)
                    .execution_options(**{CROSS_TENANT_OPTION: True})
                )
            ).scalars()
        )
        if not ids:
            break
        await session.execute(
            delete(IdempotencyKey)
            .where(IdempotencyKey.id.in_(ids))
            .execution_options(synchronize_session=False, **{CROSS_TENANT_OPTION: True})
        )
        deleted += len(ids)
        if len(ids) < batch_size:
            break
    return deleted


async def _find(
    session: AsyncSession, scope: str, tenant_id: str, key: str
) -> IdempotencyKey | None:
    stmt = (
        select(IdempotencyKey)
        .where(IdempotencyKey.scope == scope)
        .where(IdempotencyKey.tenant_id == tenant_id)
        .where(IdempotencyKey.idem_key == key)
        .execution_options(**{CROSS_TENANT_OPTION: True})
    )
    return (await session.execute(stmt)).scalar_one_or_none()
