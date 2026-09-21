"""Uploaded images and their processed WebP renditions.

Lifecycle: `pending` (upload form issued) → `processing` (upload confirmed, worker queued) →
`ready` | `failed`. The upload lands in the private bucket under `incoming/` (purged by a
lifecycle rule after 2 days); renditions live in the public bucket under immutable keys.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, ForeignKeyConstraint, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    ActorStampMixin,
    Base,
    TenantScoped,
    TimestampMixin,
    UtcDateTime,
    UUIDPrimaryKeyMixin,
)


class MediaStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class MediaOwner(StrEnum):
    PRODUCT = "product"
    # Tenant-level images (logo, landing blocks): no owner_id.
    TENANT_BRAND = "tenant_brand"
    LANDING = "landing"


class MediaAsset(UUIDPrimaryKeyMixin, TimestampMixin, ActorStampMixin, TenantScoped, Base):
    __tablename__ = "media_assets"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_media_assets_tenant_row"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_media_assets_tenant"),
        Index("ix_media_assets_owner", "tenant_id", "owner_type", "owner_id", "position"),
        # Cross-tenant maintenance job (requeue stuck uploads/processing).
        Index("ix_media_assets_status", "status", "updated_at"),
    )

    owner_type: Mapped[str] = mapped_column(String(16), nullable=False)
    owner_id: Mapped[str | None] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=MediaStatus.PENDING)
    declared_mime: Mapped[str] = mapped_column(String(32), nullable=False)
    declared_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    filename: Mapped[str | None] = mapped_column(String(200))
    upload_key: Mapped[str] = mapped_column(String(255), nullable=False)
    # Set once processed: public key prefix and {name: {key, width, height, bytes}}.
    public_prefix: Mapped[str | None] = mapped_column(String(255))
    renditions: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64))
    alt: Mapped[str | None] = mapped_column(String(300))
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure_reason: Mapped[str | None] = mapped_column(String(300))
    processed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
