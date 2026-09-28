"""Remessas e o que a transportadora contou sobre elas (etapa J, ADR 0015).

A linha da remessa nasce **antes** de chamar o provedor e é única por pedido: é ela que
impede comprar duas etiquetas para o mesmo pedido quando o operador clica duas vezes — o
Melhor Envio não tem chave de idempotência, então a unicidade é nossa.

Segredo nenhum mora aqui; o token da loja fica cifrado em `tenant_integration_credentials`.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantScoped, TimestampMixin, UtcDateTime, UUIDPrimaryKeyMixin


class ShipmentStatus(StrEnum):
    #: Linha criada, provedor ainda não respondeu: o despacho está em curso.
    CREATING = "creating"
    #: Etiqueta comprada e gerada.
    PURCHASED = "purchased"
    POSTED = "posted"
    IN_TRANSIT = "in_transit"
    DELIVERED = "delivered"
    RETURNED = "returned"
    CANCELLED = "cancelled"
    #: A compra falhou; a linha fica para o operador ver o motivo e tentar de novo.
    FAILED = "failed"


#: Enquanto está num destes, vale continuar perguntando o rastreio ao provedor.
OPEN_SHIPMENT_STATUSES = frozenset(
    {ShipmentStatus.PURCHASED, ShipmentStatus.POSTED, ShipmentStatus.IN_TRANSIT}
)


class OrderShipment(UUIDPrimaryKeyMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "order_shipments"
    __table_args__ = (
        # Um pedido, uma remessa: é esta restrição que custa menos do que uma etiqueta a mais.
        UniqueConstraint("tenant_id", "order_id", name="uq_order_shipments_order"),
        # A FK composta de shipment_events aponta para (tenant_id, id): sem esta única,
        # o MariaDB recusa a referência.
        UniqueConstraint("tenant_id", "id", name="uq_order_shipments_tenant_row"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_order_shipments_tenant"),
        ForeignKeyConstraint(
            ["tenant_id", "order_id"],
            ["orders.tenant_id", "orders.id"],
            name="fk_order_shipments_order",
        ),
        Index("ix_order_shipments_status", "tenant_id", "status"),
        Index("ix_order_shipments_tracking", "tenant_id", "tracking_code"),
    )

    order_id: Mapped[str] = mapped_column(String(36), nullable=False)
    provider: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=ShipmentStatus.CREATING)
    service_code: Mapped[str] = mapped_column(String(24), nullable=False)
    service_name: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    carrier: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    #: Id da remessa no provedor; só existe depois que a compra passou.
    provider_shipment_id: Mapped[str | None] = mapped_column(String(64))
    tracking_code: Mapped[str | None] = mapped_column(String(64))
    label_url: Mapped[str | None] = mapped_column(String(500))
    #: O que a loja cobrou do cliente (congelado no pedido) e o que a etiqueta custou de fato.
    charged_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    cost_cents: Mapped[int | None] = mapped_column(BigInteger)
    parcels: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    last_error: Mapped[str | None] = mapped_column(String(300))
    requested_by_actor: Mapped[str | None] = mapped_column(String(120))
    purchased_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    posted_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    delivered_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    #: Última vez que perguntamos o rastreio (o job usa para espaçar as consultas).
    tracked_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


class ShipmentEvent(UUIDPrimaryKeyMixin, TimestampMixin, TenantScoped, Base):
    """Linha do tempo do rastreio. Chave natural evita gravar o mesmo evento duas vezes."""

    __tablename__ = "shipment_events"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "shipment_id", "status", "occurred_at", name="uq_shipment_events_moment"
        ),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_shipment_events_tenant"),
        ForeignKeyConstraint(
            ["tenant_id", "shipment_id"],
            ["order_shipments.tenant_id", "order_shipments.id"],
            name="fk_shipment_events_shipment",
        ),
        Index("ix_shipment_events_shipment", "tenant_id", "shipment_id", "occurred_at"),
    )

    shipment_id: Mapped[str] = mapped_column(String(36), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    description: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    location: Mapped[str | None] = mapped_column(String(120))
    occurred_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
