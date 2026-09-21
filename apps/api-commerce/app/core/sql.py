from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

from sqlalchemy import Executable, Table
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession


def is_mariadb(session: AsyncSession) -> bool:
    """Row locks (FOR UPDATE, SKIP LOCKED) and upserts differ between MariaDB and SQLite."""
    return session.bind is not None and session.bind.dialect.name in {"mysql", "mariadb"}


def insert_if_missing(
    session: AsyncSession, table: Any, rows: Sequence[dict[str, Any]], *, keep_column: str
) -> Executable:
    """INSERT that silently keeps existing rows on a unique-key conflict.

    Lets concurrent "create the row if it does not exist yet" calls race without an
    IntegrityError. MariaDB has no DO NOTHING, so a conflict runs a no-op update of
    `keep_column`. Core statement: pass explicit ids and tenant_id (no ORM defaults).
    """
    target = cast(Table, table)
    if is_mariadb(session):
        stmt = mysql_insert(target).values(list(rows))
        return stmt.on_duplicate_key_update({keep_column: target.c[keep_column]})
    return sqlite_insert(target).values(list(rows)).on_conflict_do_nothing()
