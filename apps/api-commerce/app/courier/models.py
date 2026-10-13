"""Corridas de entregador chamadas pela loja (docs/15-entrega-uber-direct.md, F2).

Tabela própria, não `order_shipments`: o despacho da transportadora pressupõe volumes, etiqueta e
DC-e, e aqui não há nada disso. Um pedido pode ter várias linhas — cada tentativa é uma (a Uber
não achou entregador, a loja chamou de novo) —, mas só **uma ativa**: `active_marker` é 1 na
ativa e NULL no histórico, com UNIQUE por pedido. É o banco que impede chamar dois entregadores
para o mesmo pedido, como a remessa única impede duas etiquetas.

A linha nasce **antes** da chamada (`requested`) e o id dela é a chave de idempotência mandada à
Uber: o worker que morre no meio e a tarefa repetida pedem a mesma corrida, não outra.

Segredo nenhum mora aqui; as chaves da loja ficam cifradas em `tenant_integration_credentials`.
Do entregador guardamos o que a loja precisa ver (nome, veículo), nunca o telefone.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantScoped, TimestampMixin, UtcDateTime, UUIDPrimaryKeyMixin


class CourierStatus(StrEnum):
    #: Reservada; o worker ainda não chamou a Uber (ou vai tentar de novo em `next_attempt_at`).
    REQUESTED = "requested"
    #: Um worker está cotando/criando agora; `next_attempt_at` é o fim do prazo dele.
    CREATING = "creating"
    #: A cotação passou do teto da loja: nada foi criado, a loja confirma no pedido.
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    #: Criada na Uber; `provider_status` diz em que pé está.
    ACTIVE = "active"
    #: Alguém pediu para cancelar (a loja, ou o pedido cancelado); o worker cancela na Uber.
    CANCELLING = "cancelling"
    DELIVERED = "delivered"
    #: Não deu (Uber recusou, sem entregador, devolvida, sem chaves): a loja decide.
    FAILED = "failed"
    #: Cancelada pela loja, ou junto com o pedido.
    CANCELLED = "cancelled"


#: Enquanto a linha está num destes, ela é a corrida do pedido (`active_marker` = 1).
OPEN_COURIER_STATUSES = frozenset(
    {
        CourierStatus.REQUESTED,
        CourierStatus.CREATING,
        CourierStatus.AWAITING_CONFIRMATION,
        CourierStatus.ACTIVE,
        CourierStatus.CANCELLING,
    }
)


class CourierDelivery(UUIDPrimaryKeyMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "courier_deliveries"
    __table_args__ = (
        # Uma corrida aberta por pedido; as encerradas ficam com NULL (NULL não colide).
        UniqueConstraint(
            "tenant_id", "order_id", "active_marker", name="uq_courier_deliveries_active"
        ),
        UniqueConstraint("tenant_id", "id", name="uq_courier_deliveries_tenant_row"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_courier_deliveries_tenant"),
        ForeignKeyConstraint(
            ["tenant_id", "order_id"],
            ["orders.tenant_id", "orders.id"],
            name="fk_courier_deliveries_order",
        ),
        Index("ix_courier_deliveries_order", "tenant_id", "order_id", "created_at"),
        # O varredor procura, em todas as lojas, o que está na hora de chamar ou consultar.
        Index("ix_courier_deliveries_due", "status", "next_attempt_at"),
        Index("ix_courier_deliveries_provider_id", "provider", "provider_delivery_id"),
    )

    order_id: Mapped[str] = mapped_column(String(36), nullable=False)
    provider: Mapped[str] = mapped_column(String(24), nullable=False)
    #: `test` (entregador simulado) ou `production`, congelado na hora de chamar.
    environment: Mapped[str] = mapped_column(String(12), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default=CourierStatus.REQUESTED)
    active_marker: Mapped[int | None] = mapped_column(SmallInteger, default=1)
    #: Quem pediu: `system:...` (chamada automática no "Pronto") ou o operador.
    requested_by_actor: Mapped[str] = mapped_column(String(120), nullable=False)
    #: Teto aceito para esta corrida (o da loja, ou o valor que o operador confirmou).
    max_fee_cents: Mapped[int | None] = mapped_column(BigInteger)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(UtcDateTime)

    provider_delivery_id: Mapped[str | None] = mapped_column(String(64))
    provider_status: Mapped[str | None] = mapped_column(String(32))
    quote_id: Mapped[str | None] = mapped_column(String(64))
    #: O que a Uber cobra da loja pela corrida (centavos). O cliente pagou a taxa da zona.
    fee_cents: Mapped[int | None] = mapped_column(BigInteger)
    #: A cotação que passou do teto (para a loja ver o valor antes de confirmar).
    quoted_fee_cents: Mapped[int | None] = mapped_column(BigInteger)
    tracking_url: Mapped[str | None] = mapped_column(String(500))
    courier_name: Mapped[str | None] = mapped_column(String(80))
    courier_vehicle: Mapped[str | None] = mapped_column(String(80))
    pickup_eta: Mapped[datetime | None] = mapped_column(UtcDateTime)
    dropoff_eta: Mapped[datetime | None] = mapped_column(UtcDateTime)
    last_error: Mapped[str | None] = mapped_column(String(300))
    dispatched_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    picked_up_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    delivered_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    closed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    #: Última vez que perguntamos à Uber (o varredor usa para espaçar as consultas).
    tracked_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
