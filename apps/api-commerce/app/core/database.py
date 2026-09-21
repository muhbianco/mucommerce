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

# READ COMMITTED, not MariaDB's default REPEATABLE READ: locking reads then lock only the rows
# they find, without gap locks. Under REPEATABLE READ, `SELECT ... FOR UPDATE` over a range with
# no rows (the first outbox event of a new aggregate, whose UUIDv7 id always sorts last) takes a
# gap lock that two concurrent writers then both need to insert into: a deadlock. The code never
# relies on repeatable snapshots; every read-modify-write takes an explicit row lock.
APP_ISOLATION_LEVEL = "READ COMMITTED"


def mariadb_engine_options() -> dict[str, object]:
    return {"isolation_level": APP_ISOLATION_LEVEL, "pool_pre_ping": True}


def create_app_engine(url: str, *, pooled: bool = True) -> AsyncEngine:
    """Engine for application sessions (API, workers, CLI): same isolation everywhere."""
    if url.startswith("sqlite"):
        return create_async_engine(url, echo=False)
    options = mariadb_engine_options()
    if pooled:
        options |= {
            "pool_size": settings.db_pool_size,
            "max_overflow": settings.db_max_overflow,
            "pool_recycle": settings.db_pool_recycle_seconds,
        }
    return create_async_engine(url, echo=False, **options)


def _build_engine(url: str) -> AsyncEngine:
    return create_app_engine(url)


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
