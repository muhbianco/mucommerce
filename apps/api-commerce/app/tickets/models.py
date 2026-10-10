"""O ingresso que o cliente leva para a porta do evento.

Um por unidade comprada: três do mesmo lote são três linhas, com três códigos. É o que deixa
repassar um lugar para um amigo e deixa a porta controlar entrada por entrada — com um código
para três pessoas, o grupo teria de chegar junto e quem entrou não poderia ser distinguido de
quem ainda não entrou (ADR 0020).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import ForeignKeyConstraint, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantScoped, TimestampMixin, UUIDPrimaryKeyMixin, UtcDateTime


class TicketStatus(StrEnum):
    #: Emitido e válido: ainda não entrou.
    ISSUED = "issued"
    #: Queimado na porta. Guarda quem validou e quando — não volta atrás sozinho.
    USED = "used"
    #: Pedido estornado ou cancelado. O ingresso **não** é apagado: é dado do cliente, e a loja
    #: precisa saber que aquele lugar existiu e por que não vale mais.
    REFUNDED = "refunded"
    #: Anulado pela loja (erro de emissão, fraude).
    VOID = "void"


class Ticket(UUIDPrimaryKeyMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "tickets"
    __table_args__ = (
        # O código é global: a porta valida por código, sem saber de qual loja ele é antes de
        # achar a linha. Único por tenant seria suficiente para o banco e insuficiente para a
        # URL de validação.
        UniqueConstraint("code", name="uq_tickets_code"),
        # Idempotência da emissão: a linha do pedido já emitida não emite de novo quando o
        # webhook de pagamento chega duas vezes.
        UniqueConstraint("tenant_id", "order_item_id", "seq", name="uq_tickets_item_seq"),
        ForeignKeyConstraint(
            ["tenant_id", "order_id"],
            ["orders.tenant_id", "orders.id"],
            name="fk_tickets_order",
        ),
        Index("ix_tickets_event", "tenant_id", "event_id", "status"),
        Index("ix_tickets_order", "tenant_id", "order_id"),
    )

    order_id: Mapped[str] = mapped_column(String(36), nullable=False)
    order_item_id: Mapped[str] = mapped_column(String(36), nullable=False)
    #: 1..N dentro da linha do pedido. Com `order_item_id`, é a chave da idempotência.
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    #: `ING-` + 10 caracteres sem ambiguidade visual. Opaco: não é sequencial e não carrega id de
    #: pedido, então ver um código não deixa adivinhar o próximo nem contar o que a loja vendeu.
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    event_id: Mapped[str] = mapped_column(String(36), nullable=False)
    lot_id: Mapped[str] = mapped_column(String(36), nullable=False)
    #: Nome do lote e do evento congelados na emissão: o ingresso vale pelo que estava escrito
    #: nele, mesmo que a loja renomeie o lote depois.
    event_name: Mapped[str] = mapped_column(String(200), nullable=False)
    lot_name: Mapped[str] = mapped_column(String(200), nullable=False)
    holder_name: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=TicketStatus.ISSUED)
    used_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    #: Quem deixou entrar (id do usuário do painel). A porta responde por quem validou.
    used_by: Mapped[str | None] = mapped_column(String(36))
