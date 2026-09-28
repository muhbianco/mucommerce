"""Insumos, fornecedores e o razão de estoque deles (etapa G, fatia 1).

**Por que insumo não é produto.** O catálogo inteiro assume que um item é vendável: vitrine,
preço, reserva por pedido, variante, modificador. Farinha não tem nada disso, e enfiá-la lá
significaria um `if not supply` em cada consulta da loja — um esquecimento e o insumo aparece
na vitrine. Aqui o risco é zero por construção: a loja nem sabe que esta tabela existe.

Unidades seguem a convenção do estoque: inteiros em milésimos (`*_milli`), então 1 g = 1000 e
`SUM(qty_milli) == on_hand_milli` fecha na unidade em qualquer banco. Custo é micro-real por
unidade base (`*_micro`, 1 real = 1_000_000), que aguenta R$ 0,000001 por grama sem arredondar
no meio do caminho.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKeyConstraint,
    Index,
    Integer,
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
    utcnow,
)

MICRO = 1_000_000


class SupplyUnit(StrEnum):
    """Unidade base do insumo. Compra-se em saco, usa-se em grama."""

    GRAM = "g"
    MILLILITER = "ml"
    UNIT = "un"


class SupplyMovementType(StrEnum):
    RECEIPT = "purchase_in"  # entrada de compra, com custo
    ADJUSTMENT = "adjustment"  # correção de contagem, sem custo novo
    LOSS = "loss"  # perda, quebra, vencimento
    COUNT = "count"  # inventário: o saldo passa a ser o contado
    PRODUCTION_OUT = "production_out"  # consumido numa ordem de produção (fatia 3)


class Supplier(UUIDPrimaryKeyMixin, TimestampMixin, ActorStampMixin, TenantScoped, Base):
    __tablename__ = "suppliers"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_suppliers_name"),
        UniqueConstraint("tenant_id", "id", name="uq_suppliers_tenant_row"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_suppliers_tenant"),
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    document: Mapped[str | None] = mapped_column(String(20))
    phone: Mapped[str | None] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(160))
    note: Mapped[str | None] = mapped_column(String(500))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Supply(UUIDPrimaryKeyMixin, TimestampMixin, ActorStampMixin, TenantScoped, Base):
    """Um insumo e o saldo dele. Saldo e custo médio moram juntos porque mudam juntos."""

    __tablename__ = "supplies"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_supplies_name"),
        UniqueConstraint("tenant_id", "id", name="uq_supplies_tenant_row"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_supplies_tenant"),
        Index("ix_supplies_active", "tenant_id", "active"),
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    unit: Mapped[str] = mapped_column(String(4), nullable=False, default=SupplyUnit.GRAM)
    on_hand_milli: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    #: Custo médio móvel por unidade base. `None` enquanto nunca houve entrada com preço.
    avg_cost_micro: Mapped[int | None] = mapped_column(BigInteger)
    min_level_milli: Mapped[int | None] = mapped_column(BigInteger)
    note: Mapped[str | None] = mapped_column(String(500))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class SupplyReceipt(UUIDPrimaryKeyMixin, TimestampMixin, ActorStampMixin, TenantScoped, Base):
    """Cabeçalho de uma compra: o que entrou, de quem, por quanto."""

    __tablename__ = "supply_receipts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_supply_receipts_tenant_row"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_supply_receipts_tenant"),
        ForeignKeyConstraint(
            ["tenant_id", "supplier_id"],
            ["suppliers.tenant_id", "suppliers.id"],
            name="fk_supply_receipts_supplier",
        ),
        Index("ix_supply_receipts_when", "tenant_id", "occurred_at"),
    )

    supplier_id: Mapped[str | None] = mapped_column(String(36))
    #: Número da nota ou do recibo, do jeito que o lojista anota.
    document: Mapped[str | None] = mapped_column(String(60))
    total_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    line_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    note: Mapped[str | None] = mapped_column(String(500))
    occurred_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)


class SupplyMovement(UUIDPrimaryKeyMixin, TenantScoped, Base):
    """Append-only, como o razão do estoque: correção é movimento novo, nunca edição."""

    __tablename__ = "supply_movements"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_supply_movements_tenant"),
        ForeignKeyConstraint(
            ["tenant_id", "supply_id"],
            ["supplies.tenant_id", "supplies.id"],
            name="fk_supply_movements_supply",
        ),
        Index("ix_supply_movements_supply", "tenant_id", "supply_id", "occurred_at"),
        Index("ix_supply_movements_reference", "tenant_id", "reference_type", "reference_id"),
    )

    supply_id: Mapped[str] = mapped_column(String(36), nullable=False)
    movement_type: Mapped[str] = mapped_column(String(16), nullable=False)
    qty_milli: Mapped[int] = mapped_column(BigInteger, nullable=False)
    balance_after_milli: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: Custo unitário desta entrada (só em `purchase_in`); o médio fica no insumo.
    unit_cost_micro: Mapped[int | None] = mapped_column(BigInteger)
    reference_type: Mapped[str] = mapped_column(String(32), nullable=False)
    reference_id: Mapped[str] = mapped_column(String(36), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(200))
    actor: Mapped[str] = mapped_column(String(120), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
