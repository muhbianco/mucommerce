from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, MetaData, String, TypeDecorator
from sqlalchemy.dialects import mysql
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeEngine

from app.core.ids import new_id

# Deterministic constraint names: Alembic autogenerate cannot drop anonymous indexes on MariaDB.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

TENANT_ID_LENGTH = 36


class UtcDateTime(TypeDecorator[datetime]):
    """MariaDB DATETIME has no offset. Store naive UTC, return aware UTC."""

    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        if dialect.name in {"mysql", "mariadb"}:
            return dialect.type_descriptor(mysql.DATETIME(fsp=6))
        return dialect.type_descriptor(DateTime())

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Naive datetime rejected; use datetime.now(UTC).")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC)


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UUIDPrimaryKeyMixin:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )


class ActorStampMixin:
    """Who wrote the row. Format `type:id` (admin:<uuid>, customer:<uuid>, system:<name>)."""

    created_by_actor: Mapped[str] = mapped_column(String(120), nullable=False, default="system:api")
    updated_by_actor: Mapped[str] = mapped_column(String(120), nullable=False, default="system:api")


class TenantScoped:
    """Every business row carries `tenant_id`.

    Models with this mixin get the automatic `tenant_id = :current` filter on
    SELECT and the automatic stamp on INSERT (see `app.tenancy.orm_filter`).
    Lookup tables that must be queried before a tenant is known (tenants,
    tenant_domains, admin users) do NOT use this mixin.
    """

    # Plain mapped_column on a mixin is copied to every subclass and, unlike
    # declared_attr, can be referenced by `with_loader_criteria(TenantScoped, ...)`.
    tenant_id: Mapped[str] = mapped_column(String(TENANT_ID_LENGTH), nullable=False, index=True)
