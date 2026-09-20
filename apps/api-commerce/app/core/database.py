from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings


def _build_engine(url: str) -> AsyncEngine:
    if url.startswith("sqlite"):
        return create_async_engine(url, echo=False)
    return create_async_engine(
        url,
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_recycle=settings.db_pool_recycle_seconds,
        echo=False,
    )


engine: AsyncEngine = _build_engine(settings.database_url)

SessionFactory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """One session per request. Commit at the end of the handler, rollback on any error.

    The session starts WITHOUT a tenant. `app.tenancy.deps` sets
    `session.info["tenant_id"]` once the tenant is resolved; until then any
    query against a `TenantScoped` model raises `TenantContextMissingError`.
    """
    async with SessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def check_database_health() -> bool:
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception:
        return False
    return True


async def dispose_engine() -> None:
    await engine.dispose()
