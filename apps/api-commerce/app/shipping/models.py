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
    Boolean,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    ActorStampMixin,
    Base,
    TenantScoped,
    TimestampMixin,
    UtcDateTime,
    UUIDPrimaryKeyMixin,
)
from app.shipping.packing.model import PackageKind


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


#: Quantas embalagens uma loja pode ter. Lista limitada: o motor testa combinações delas.
MAX_PACKAGES_PER_TENANT = 30
#: Quantas regras de embalagem um produto pode ter ("só nestas caixas").
MAX_RULES_PER_PRODUCT = 10


class ShippingPackage(UUIDPrimaryKeyMixin, TimestampMixin, ActorStampMixin, TenantScoped, Base):
    """Uma embalagem que a loja tem de verdade (frete v2, docs/13-frete-v2.md §3).

    Medida **por dentro** é o que cabe; **por fora** é o que a transportadora cobra. Sem a de
    fora, ela é derivada da de dentro mais a parede do tipo (`WALL_MM`).

    Exatamente uma embalagem padrão por loja: `default_marker` é 1 na padrão e NULL nas outras,
    com UNIQUE — o banco garante "no máximo uma" (NULL não colide) e o serviço garante "pelo
    menos uma" (a primeira nasce padrão; a padrão não arquiva nem apaga).
    """

    __tablename__ = "shipping_packages"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_shipping_packages_name"),
        UniqueConstraint("tenant_id", "id", name="uq_shipping_packages_tenant_row"),
        UniqueConstraint("tenant_id", "default_marker", name="uq_shipping_packages_default"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_shipping_packages_tenant"),
        Index("ix_shipping_packages_active", "tenant_id", "active", "position"),
    )

    name: Mapped[str] = mapped_column(String(60), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default=PackageKind.BOX)
    #: Envelope e saco: altura = espessura máxima. Tubo: largura = altura = diâmetro.
    inner_length_mm: Mapped[int] = mapped_column(Integer, nullable=False)
    inner_width_mm: Mapped[int] = mapped_column(Integer, nullable=False)
    inner_height_mm: Mapped[int] = mapped_column(Integer, nullable=False)
    outer_length_mm: Mapped[int | None] = mapped_column(Integer)
    outer_width_mm: Mapped[int | None] = mapped_column(Integer)
    outer_height_mm: Mapped[int | None] = mapped_column(Integer)
    empty_weight_grams: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_weight_grams: Mapped[int] = mapped_column(Integer, nullable=False, default=30_000)
    material_cost_cents: Mapped[int | None] = mapped_column(BigInteger)
    #: O motor pode usar para qualquer produto (True) ou só para quem pede nas regras (False).
    auto_select: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    default_marker: Mapped[int | None] = mapped_column(SmallInteger)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    @property
    def is_default(self) -> bool:
        return self.default_marker == 1


class ProductPackageRule(UUIDPrimaryKeyMixin, TimestampMixin, TenantScoped, Base):
    """Regra "este produto vai nesta embalagem", com a capacidade declarada opcional.

    `max_units` é declaração da loja, não cálculo do motor: no produto rígido só reduz, no
    flexível vale até 200% do volume interno (`app.shipping.packing.declared`).
    """

    __tablename__ = "product_package_rules"
    __table_args__ = (
        # Também serve de índice para a FK do produto (colunas à esquerda).
        UniqueConstraint(
            "tenant_id", "product_id", "package_id", name="uq_product_package_rules_pair"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["products.tenant_id", "products.id"],
            name="fk_product_package_rules_product",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "package_id"],
            ["shipping_packages.tenant_id", "shipping_packages.id"],
            name="fk_product_package_rules_package",
        ),
        Index("ix_product_package_rules_package", "tenant_id", "package_id"),
    )

    product_id: Mapped[str] = mapped_column(String(36), nullable=False)
    package_id: Mapped[str] = mapped_column(String(36), nullable=False)
    max_units: Mapped[int | None] = mapped_column(Integer)
