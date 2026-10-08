"""Login dos clientes sempre ligado em toda loja.

Desde a ADR 0019 o login dos clientes é obrigatório: o painel mostra o módulo sem botão e o
servidor recusa desligá-lo (`module_always_on`). Lojas antigas que estavam com ele desligado
passam a ter o login ligado; quem não tinha a linha ganha uma. Mais nada muda: o carrinho e os
outros módulos ficam como o lojista deixou.

As chaves que saíram do catálogo (`storefront`, `whatsapp_owned`) **não** são apagadas aqui: a
imagem anterior ainda lê `storefront` e, sem a linha, tiraria o catálogo do ar num rollback.
Saem numa migration posterior, quando esta versão estiver estável.

Downgrade é no-op: a versão anterior aceita o login ligado em qualquer loja.

Revision ID: 0040_customer_login_always_on
Revises: 0039_require_whatsapp_sgpipas
Create Date: 2026-10-08
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op
from uuid6 import uuid7

revision: str = "0040_customer_login_always_on"
down_revision: str | None = "0039_require_whatsapp_sgpipas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KEY = "customer_login"
_TENANTS = sa.table("tenants", sa.column("id", sa.String))
_FLAGS = sa.table(
    "tenant_feature_flags",
    sa.column("id", sa.String),
    sa.column("tenant_id", sa.String),
    sa.column("key", sa.String),
    sa.column("enabled", sa.Boolean),
    sa.column("config", sa.JSON),
    sa.column("created_at", sa.DateTime),
    sa.column("updated_at", sa.DateTime),
)


def upgrade() -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    op.execute(
        _FLAGS.update()
        .where(_FLAGS.c.key == _KEY, _FLAGS.c.enabled.is_(False))
        .values(enabled=True, updated_at=now)
    )
    bind = op.get_bind()
    com_linha = sa.select(_FLAGS.c.tenant_id).where(_FLAGS.c.key == _KEY)
    sem_linha = bind.execute(sa.select(_TENANTS.c.id).where(_TENANTS.c.id.not_in(com_linha)))
    rows = [
        {
            "id": str(uuid7()),
            "tenant_id": tenant_id,
            "key": _KEY,
            "enabled": True,
            "config": {},
            "created_at": now,
            "updated_at": now,
        }
        for (tenant_id,) in sem_linha
    ]
    if rows:
        op.bulk_insert(_FLAGS, rows)


def downgrade() -> None:
    pass
