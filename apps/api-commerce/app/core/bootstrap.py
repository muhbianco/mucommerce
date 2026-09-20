from __future__ import annotations

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


def alembic_sqlalchemy_url(url: str) -> str:
    """ConfigParser interpolation treats `%` as a token; percent-encoded DSN must be doubled."""
    return url.replace("%", "%%")


def alembic_config(url: str | None = None) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.attributes["configure_logger"] = False
    if url:
        config.set_main_option("sqlalchemy.url", alembic_sqlalchemy_url(url))
    return config


async def ensure_database_exists() -> None:
    """CREATE DATABASE IF NOT EXISTS with the *migration* user.

    The migration user has database-level privileges only (`GRANT ALL ON mucommerce.*`),
    which MariaDB accepts for creating that specific database. The runtime user never
    runs this.
    """
    if settings.is_sqlite:
        return
    server_engine = create_async_engine(settings.migrate_server_url, isolation_level="AUTOCOMMIT")
    try:
        async with server_engine.connect() as connection:
            await connection.execute(
                text(
                    f"CREATE DATABASE IF NOT EXISTS `{settings.db_name}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_520_ci"
                )
            )
        logger.info("Database ensured", extra={"database": settings.db_name})
    finally:
        await server_engine.dispose()


def upgrade_head(url: str | None = None) -> None:
    command.upgrade(alembic_config(url or settings.migrate_database_url), "head")


async def run_migrations() -> None:
    """Development convenience only (RUN_MIGRATIONS_ON_STARTUP=true)."""
    await ensure_database_exists()
    await asyncio.to_thread(upgrade_head)
    logger.info("Migrations applied")
