from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.tenancy.orm_filter import register_tenant_filter

register_tenant_filter()


def run_async[T](coro: Coroutine[Any, Any, T]) -> T:
    """Celery workers are sync; each task gets its own loop and its own engine."""
    return asyncio.run(coro)


async def with_session[T](fn: Callable[[AsyncSession], Awaitable[T]]) -> T:
    """Open a dedicated engine + session for a task, commit on success, dispose after."""
    engine = create_async_engine(settings.database_url, pool_pre_ping=not settings.is_sqlite)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            try:
                result = await fn(session)
                await session.commit()
                return result
            except Exception:
                await session.rollback()
                raise
    finally:
        await engine.dispose()
