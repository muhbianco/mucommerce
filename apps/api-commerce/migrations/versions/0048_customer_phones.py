"""Telefone → cliente por loja, para o robô de vendas (docs/14-pedidos-lunares.md §5).

Tabela nova `customer_phones` e um preenchimento com o que já existe:
- o WhatsApp de cada pedido (`orders.customer_snapshot.phone`), como `checkout`;
- o telefone da conta de quem tem linha de acesso na loja, como `account` (confirmado) ou
  `whatsapp` (cadastrado pelo assistente).

Os números já estão normalizados na origem (o checkout e a confirmação gravam em E.164). O
preenchimento anda em lotes por id, e é idempotente: a chave única descarta repetidos.

Revision ID: 0048_customer_phones
Revises: 0047_product_home_exposure
Create Date: 2026-10-12
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0048_customer_phones"
down_revision: str | None = "0047_product_home_exposure"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BATCH = 500


def dt() -> sa.types.TypeEngine[object]:
    return sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql", "mariadb")


def _ids() -> str:
    # O mesmo formato de id do resto do banco não importa aqui: só precisa ser único.
    return str(uuid.uuid4())


def upgrade() -> None:
    op.create_table(
        "customer_phones",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("customer_id", sa.String(36), nullable=False),
        sa.Column("phone_e164", sa.String(20), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("first_seen_at", dt(), nullable=False),
        sa.Column("last_seen_at", dt(), nullable=False),
        sa.Column("created_at", dt(), nullable=False),
        sa.Column("updated_at", dt(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_customer_phones_tenant"),
        sa.ForeignKeyConstraint(
            ["customer_id"], ["customers.id"], name="fk_customer_phones_customer"
        ),
        sa.UniqueConstraint(
            "tenant_id", "phone_e164", "customer_id", name="uq_customer_phones_line"
        ),
    )
    op.create_index("ix_customer_phones_tenant_id", "customer_phones", ["tenant_id"])
    op.create_index("ix_customer_phones_customer", "customer_phones", ["customer_id", "tenant_id"])
    _backfill()


def _backfill() -> None:
    bind = op.get_bind()
    seen: set[tuple[str, str, str]] = set()
    now = datetime.now(UTC).replace(tzinfo=None)
    rows: list[dict[str, Any]] = []

    def add(tenant_id: str, customer_id: str, phone: str | None, source: str, at: Any) -> None:
        if not phone or not customer_id:
            return
        chave = (tenant_id, phone, customer_id)
        if chave in seen:
            return
        seen.add(chave)
        when = at if isinstance(at, datetime) else now
        rows.append(
            {
                "id": _ids(),
                "tenant_id": tenant_id,
                "customer_id": customer_id,
                "phone_e164": phone[:20],
                "source": source,
                "first_seen_at": when,
                "last_seen_at": when,
                "created_at": now,
                "updated_at": now,
            }
        )

    ultimo = ""
    while True:
        lote = bind.execute(
            sa.text(
                "SELECT id, tenant_id, customer_id, customer_snapshot, placed_at FROM orders "
                "WHERE id > :ultimo ORDER BY id LIMIT :limite"
            ),
            {"ultimo": ultimo, "limite": BATCH},
        ).fetchall()
        if not lote:
            break
        for order_id, tenant_id, customer_id, snapshot, placed_at in lote:
            dados: Any = snapshot or {}
            while isinstance(dados, str):  # coluna JSON lida como texto (às vezes, duas camadas)
                dados = json.loads(dados)
            telefone = dados.get("phone") if isinstance(dados, dict) else None
            add(tenant_id, customer_id, telefone, "checkout", placed_at)
            ultimo = order_id

    ultimo = ""
    while True:
        lote = bind.execute(
            sa.text(
                "SELECT a.id, a.tenant_id, c.id, c.phone_e164, c.phone_verified_at, a.created_at "
                "FROM customer_tenant_access a JOIN customers c ON c.id = a.customer_id "
                "WHERE a.id > :ultimo AND c.phone_e164 IS NOT NULL ORDER BY a.id LIMIT :limite"
            ),
            {"ultimo": ultimo, "limite": BATCH},
        ).fetchall()
        if not lote:
            break
        for access_id, tenant_id, customer_id, phone, verified_at, created_at in lote:
            add(tenant_id, customer_id, phone, "account" if verified_at else "whatsapp", created_at)
            ultimo = access_id

    tabela = sa.table(
        "customer_phones",
        *(
            sa.column(nome)
            for nome in (
                "id",
                "tenant_id",
                "customer_id",
                "phone_e164",
                "source",
                "first_seen_at",
                "last_seen_at",
                "created_at",
                "updated_at",
            )
        ),
    )
    for inicio in range(0, len(rows), BATCH):
        op.bulk_insert(tabela, rows[inicio : inicio + BATCH])


def downgrade() -> None:
    op.drop_table("customer_phones")
