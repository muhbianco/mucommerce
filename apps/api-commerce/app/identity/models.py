from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, BigInteger, ForeignKey, Index, LargeBinary, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    Base,
    TenantScoped,
    TimestampMixin,
    UtcDateTime,
    UUIDPrimaryKeyMixin,
)


class AdminUserStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class AccessStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    BLOCKED = "blocked"
    REVOKED = "revoked"


class AdminUser(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Staff of MuhBianco or of tenants. Global (a person may manage many tenants)."""

    __tablename__ = "admin_users"

    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    google_subject: Mapped[str | None] = mapped_column(String(255), unique=True)
    platform_role: Mapped[str | None] = mapped_column(String(16))  # superadmin | operator | NULL
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=AdminUserStatus.ACTIVE)
    last_login_at: Mapped[datetime | None] = mapped_column(UtcDateTime)

    memberships: Mapped[list[TenantMembership]] = relationship(
        back_populates="admin_user", lazy="selectin", cascade="all, delete-orphan"
    )

    @property
    def is_active(self) -> bool:
        return self.status == AdminUserStatus.ACTIVE

    @property
    def is_platform_admin(self) -> bool:
        return bool(self.platform_role)


class TenantMembership(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Which admin user has which role in which tenant.

    Deliberately NOT TenantScoped: it is read while authorising, before the
    request has a tenant context (the tenant comes from the path, and this table
    is what validates that path).
    """

    __tablename__ = "tenant_memberships"
    __table_args__ = (
        UniqueConstraint("tenant_id", "admin_user_id", name="uq_tenant_memberships_user"),
    )

    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), nullable=False)
    admin_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("admin_users.id"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")

    admin_user: Mapped[AdminUser] = relationship(back_populates="memberships")


class AdminRefreshToken(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "admin_refresh_tokens"

    admin_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("admin_users.id"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    replaced_by_id: Mapped[str | None] = mapped_column(String(36))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    ip: Mapped[str | None] = mapped_column(String(45))


class Customer(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A person. Global identity; store access is per tenant (`CustomerTenantAccess`)."""

    __tablename__ = "customers"

    email_normalized: Mapped[str | None] = mapped_column(String(320), unique=True)
    email_verified_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    phone_e164: Mapped[str | None] = mapped_column(String(20), index=True)
    phone_verified_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    full_name: Mapped[str | None] = mapped_column(String(200))
    document_enc: Mapped[bytes | None] = mapped_column(LargeBinary(256))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    anonymized_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


class CustomerIdentity(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "customer_identities"
    __table_args__ = (UniqueConstraint("provider", "subject", name="uq_customer_identities_sub"),)

    customer_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("customers.id"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    email_at_provider: Mapped[str | None] = mapped_column(String(320))
    raw_claims: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    last_login_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


class CustomerSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Opaque session per (customer, tenant). Cookie is host-only on the tenant domain."""

    __tablename__ = "customer_sessions"
    __table_args__ = (Index("ix_customer_sessions_customer_tenant", "customer_id", "tenant_id"),)

    customer_id: Mapped[str] = mapped_column(String(36), ForeignKey("customers.id"), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    ip: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(300))


class CustomerTenantAccess(UUIDPrimaryKeyMixin, TimestampMixin, TenantScoped, Base):
    """Whitelist (`liberar_loja`) source of truth. Chatwoot mirrors it."""

    __tablename__ = "customer_tenant_access"
    __table_args__ = (
        UniqueConstraint("tenant_id", "customer_id", name="uq_customer_tenant_access_customer"),
        Index("ix_customer_tenant_access_status", "tenant_id", "status"),
    )

    customer_id: Mapped[str] = mapped_column(String(36), ForeignKey("customers.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=AccessStatus.PENDING)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="panel")
    chatwoot_contact_id: Mapped[int | None] = mapped_column(BigInteger)
    approved_by_actor: Mapped[str | None] = mapped_column(String(120))
    approved_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    note: Mapped[str | None] = mapped_column(String(500))
