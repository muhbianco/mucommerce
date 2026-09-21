from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.ids import new_public_key
from app.models.base import (
    Base,
    TenantScoped,
    TimestampMixin,
    UtcDateTime,
    UUIDPrimaryKeyMixin,
)
from app.tenancy.settings_schemas import default_settings


class TenantStatus(StrEnum):
    DRAFT = "draft"
    PROVISIONING = "provisioning"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    ARCHIVED = "archived"


class DomainKind(StrEnum):
    PLATFORM_SUBDOMAIN = "platform_subdomain"
    CUSTOM_APEX = "custom_apex"
    CUSTOM_SUBDOMAIN = "custom_subdomain"


class DomainPurpose(StrEnum):
    STOREFRONT = "storefront"
    CHAT_REDIRECT = "chat_redirect"


class DomainRole(StrEnum):
    PRIMARY = "primary"
    ALIAS = "alias"


class DomainStatus(StrEnum):
    PENDING_DNS = "pending_dns"
    VERIFYING = "verifying"
    VERIFIED = "verified"
    ACTIVE = "active"
    FAILED = "failed"
    DISABLED = "disabled"


class TlsStatus(StrEnum):
    NONE = "none"
    REQUESTED = "requested"
    ISSUED = "issued"
    ERROR = "error"


class Tenant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Global lookup table: NOT TenantScoped (it is the tenant)."""

    __tablename__ = "tenants"

    slug: Mapped[str] = mapped_column(String(63), nullable=False, unique=True)
    public_key: Mapped[str] = mapped_column(
        String(32), nullable=False, unique=True, default=lambda: new_public_key(32)
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    legal_name: Mapped[str | None] = mapped_column(String(200))
    document: Mapped[str | None] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=TenantStatus.DRAFT)
    plan: Mapped[str] = mapped_column(String(32), nullable=False, default="standard")
    default_currency: Mapped[str] = mapped_column(String(3), nullable=False, default="BRL")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="America/Sao_Paulo")
    locale: Mapped[str] = mapped_column(String(10), nullable=False, default="pt-BR")
    activated_at: Mapped[datetime | None] = mapped_column(UtcDateTime)

    domains: Mapped[list[TenantDomain]] = relationship(
        back_populates="tenant", lazy="selectin", cascade="all, delete-orphan"
    )


class TenantDomain(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Hostname → tenant. Global unique hostname, so it is NOT TenantScoped."""

    __tablename__ = "tenant_domains"
    __table_args__ = (
        Index("ix_tenant_domains_tenant_purpose_role", "tenant_id", "purpose", "role"),
    )

    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), nullable=False)
    hostname: Mapped[str] = mapped_column(String(253), nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    purpose: Mapped[str] = mapped_column(
        String(32), nullable=False, default=DomainPurpose.STOREFRONT
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False, default=DomainRole.ALIAS)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=DomainStatus.PENDING_DNS
    )
    verification_token: Mapped[str] = mapped_column(
        String(43), nullable=False, default=lambda: new_public_key(43)
    )
    verified_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    tls_status: Mapped[str] = mapped_column(String(16), nullable=False, default=TlsStatus.NONE)
    last_check_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    last_error: Mapped[str | None] = mapped_column(String(500))
    failed_checks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    tenant: Mapped[Tenant] = relationship(back_populates="domains")


class TenantSetting(UUIDPrimaryKeyMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "tenant_settings"
    __table_args__ = (UniqueConstraint("tenant_id", "key", name="uq_tenant_settings_key"),)

    key: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class TenantFeatureFlag(UUIDPrimaryKeyMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "tenant_feature_flags"
    __table_args__ = (UniqueConstraint("tenant_id", "key", name="uq_tenant_feature_flags_key"),)

    key: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class TenantSequence(UUIDPrimaryKeyMixin, TenantScoped, Base):
    """Human-readable numbers per tenant (orders, production orders)."""

    __tablename__ = "tenant_sequences"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_tenant_sequences_name"),)

    name: Mapped[str] = mapped_column(String(32), nullable=False)
    next_value: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class TenantIntegrationCredential(UUIDPrimaryKeyMixin, TimestampMixin, TenantScoped, Base):
    """Encrypted secret material (payment tokens, Chatwoot tokens). Never returned by the API."""

    __tablename__ = "tenant_integration_credentials"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "provider", "key_name", name="uq_tenant_integration_credentials_key"
        ),
    )

    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    key_name: Mapped[str] = mapped_column(String(64), nullable=False)
    # LargeBinary maps to BLOB on MariaDB and on SQLite.
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary(4096), nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary(12), nullable=False)
    key_version: Mapped[int] = mapped_column(Integer, nullable=False)
    masked: Mapped[str] = mapped_column(String(64), nullable=False)
    rotated_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


DEFAULT_FEATURE_FLAGS: dict[str, bool] = {
    "storefront": True,
    # Phase 1 slice 1: off until the tenant is ready; turned on first in the loja modelo.
    "catalog": False,
    "inventory": False,
    "events": True,
    "pickup": True,
    "delivery": False,
    "manufacturing": False,
    "coupons": False,
    "chatwoot": True,
    "payments.mercadopago": False,
    "payments.infinitepay": False,
    "sales_agent": False,
    "whatsapp_owned": False,
}

# One source of truth: the versioned schemas (app/tenancy/settings_schemas.py).
DEFAULT_SETTINGS: dict[str, dict[str, Any]] = default_settings()
