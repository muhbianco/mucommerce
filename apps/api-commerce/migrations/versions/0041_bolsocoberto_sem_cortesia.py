"""Bolso Coberto: desliga Chatwoot e assistente de vendas ligados pelo admin sem assinatura.

Desde a ADR 0019 esses dois módulos são da assinatura: ligam com a compra no catálogo e o admin
do site não mexe mais neles. Na Bolso Coberto estavam ligados por cortesia antiga do admin, sem
`commerce_chatwoot` nem assinatura de vendas — e não teria mais como desligar pela tela. O dono
pediu para desligar em 08/10/2026. Só mexe nessa loja; o downgrade devolve os dois ligados, que
era o estado anterior.

Revision ID: 0041_bolsocoberto_sem_cortesia
Revises: 0040_customer_login_always_on
Create Date: 2026-10-08
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0041_bolsocoberto_sem_cortesia"
down_revision: str | None = "0040_customer_login_always_on"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SLUG = "bolsocoberto"
_KEYS = ("chatwoot", "sales_agent")
_TENANTS = sa.table("tenants", sa.column("id", sa.String), sa.column("slug", sa.String))
_FLAGS = sa.table(
    "tenant_feature_flags",
    sa.column("tenant_id", sa.String),
    sa.column("key", sa.String),
    sa.column("enabled", sa.Boolean),
    sa.column("updated_at", sa.DateTime),
)


def _set(enabled: bool) -> None:
    loja = sa.select(_TENANTS.c.id).where(_TENANTS.c.slug == _SLUG).scalar_subquery()
    op.execute(
        _FLAGS.update()
        .where(_FLAGS.c.tenant_id == loja, _FLAGS.c.key.in_(_KEYS))
        .values(enabled=enabled, updated_at=datetime.now(UTC).replace(tzinfo=None))
    )


def upgrade() -> None:
    _set(False)


def downgrade() -> None:
    _set(True)
