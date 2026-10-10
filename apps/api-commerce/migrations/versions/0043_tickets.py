"""O ingresso passa a existir depois da compra.

Até aqui a loja vendia ingresso e acabava ali: o cliente pagava e não recebia nada que o fizesse
entrar no evento, e a loja não tinha como saber quem já entrou.

`tickets` é um ingresso por unidade comprada — três do mesmo lote são três linhas, com três
códigos. É o que deixa repassar um lugar para um amigo e deixa a porta controlar entrada por
entrada. O código é opaco (`ING-` + 10 caracteres sem ambiguidade visual, de `secrets`): quem vê
um não adivinha o próximo nem conta quantos a loja vendeu.

`events.ticketing` e `events.delivery` são os dois eixos do ADR 0020 — quem emite e como chega.
O padrão de ambos é o que a loja já fazia: `own` + `digital`, ou seja, vender e deixar a entrega
com o lojista. Evento que já existe não acorda emitindo QR que ninguém pediu.

Revision ID: 0043_tickets
Revises: 0042_agent_links
Create Date: 2026-10-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0043_tickets"
down_revision: str | None = "0042_agent_links"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: Coluna nova em tabela com linhas: nasce com `server_default` para preencher o que já existe,
#: e o default sai logo em seguida. Deixá-lo no banco faria o schema divergir do modelo (onde o
#: padrão é do Python) — divergência que o `test_models_match_migrations` cobra, e com razão:
#: default no banco é regra escondida de quem lê o código.
_EVENT_DEFAULTS = (("ticketing", "own"), ("delivery", "digital"))


def upgrade() -> None:
    with op.batch_alter_table("events") as batch:
        for nome, padrao in _EVENT_DEFAULTS:
            batch.add_column(
                sa.Column(nome, sa.String(length=16), nullable=False, server_default=padrao)
            )
    with op.batch_alter_table("events") as batch:
        for nome, _padrao in _EVENT_DEFAULTS:
            batch.alter_column(
                nome,
                existing_type=sa.String(length=16),
                existing_nullable=False,
                server_default=None,
            )
    op.create_table(
        "tickets",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("order_id", sa.String(length=36), nullable=False),
        sa.Column("order_item_id", sa.String(length=36), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("lot_id", sa.String(length=36), nullable=False),
        sa.Column("event_name", sa.String(length=200), nullable=False),
        sa.Column("lot_name", sa.String(length=200), nullable=False),
        sa.Column("holder_name", sa.String(length=200), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column("used_by", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        # Global: a porta valida por código, sem saber de qual loja ele é antes de achar a linha.
        sa.UniqueConstraint("code", name="uq_tickets_code"),
        # Idempotência da emissão: webhook de pagamento repetido não emite ingresso de novo.
        sa.UniqueConstraint("tenant_id", "order_item_id", "seq", name="uq_tickets_item_seq"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "order_id"],
            ["orders.tenant_id", "orders.id"],
            name="fk_tickets_order",
        ),
    )
    # `TenantScoped` indexa `tenant_id` em todo modelo de negócio: o índice vem do mixin, e o
    # schema tem de ter o mesmo.
    op.create_index("ix_tickets_tenant_id", "tickets", ["tenant_id"])
    op.create_index("ix_tickets_event", "tickets", ["tenant_id", "event_id", "status"])
    op.create_index("ix_tickets_order", "tickets", ["tenant_id", "order_id"])


def downgrade() -> None:
    # Só a tabela: `drop_index` de índice que sustenta a FK composta faz o MariaDB recusar com
    # 1553, e o drop da tabela leva os índices junto.
    op.drop_table("tickets")
    op.drop_column("events", "delivery")
    op.drop_column("events", "ticketing")
