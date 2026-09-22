"""Delivery addresses a customer keeps in a store (checkout picks one; the order snapshots it)."""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKeyConstraint, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantScoped, TimestampMixin, UUIDPrimaryKeyMixin

MAX_ADDRESSES = 10


class CustomerAddress(UUIDPrimaryKeyMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "customer_addresses"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_customer_addresses_tenant"),
        ForeignKeyConstraint(
            ["customer_id"], ["customers.id"], name="fk_customer_addresses_customer"
        ),
        Index("ix_customer_addresses_customer", "customer_id", "tenant_id"),
    )

    customer_id: Mapped[str] = mapped_column(String(36), nullable=False)
    label: Mapped[str | None] = mapped_column(String(40))
    recipient_name: Mapped[str] = mapped_column(String(120), nullable=False)
    phone_e164: Mapped[str | None] = mapped_column(String(20))
    postal_code: Mapped[str] = mapped_column(String(8), nullable=False)
    street: Mapped[str] = mapped_column(String(160), nullable=False)
    number: Mapped[str] = mapped_column(String(20), nullable=False)
    complement: Mapped[str | None] = mapped_column(String(80))
    district: Mapped[str] = mapped_column(String(80), nullable=False)
    city: Mapped[str] = mapped_column(String(80), nullable=False)
    state: Mapped[str] = mapped_column(String(2), nullable=False)
    reference: Mapped[str | None] = mapped_column(String(160))
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
