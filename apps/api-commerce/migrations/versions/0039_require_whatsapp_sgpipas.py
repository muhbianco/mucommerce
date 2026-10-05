"""WhatsApp do cliente obrigatório no checkout da SGPipas.

A opção nova (`checkout.require_whatsapp`, desligada por padrão) liga ou desliga pelo painel, em
Entrega e checkout. O dono pediu que ela comece ligada na SGPipas, onde o lojista precisa falar com
o cliente (CPF que faltou, endereço que a transportadora não acha). Só mexe nessa loja, e só se a
chave ainda não foi escolhida no painel; o downgrade tira a chave (volta ao padrão: opcional).

Revision ID: 0039_require_whatsapp_sgpipas
Revises: 0038_address_recipient_document
Create Date: 2026-10-05
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0039_require_whatsapp_sgpipas"
down_revision: str | None = "0038_address_recipient_document"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SLUG = "sgpipas"
_TENANTS = sa.table("tenants", sa.column("id", sa.String), sa.column("slug", sa.String))
_SETTINGS = sa.table(
    "tenant_settings",
    sa.column("tenant_id", sa.String),
    sa.column("key", sa.String),
    sa.column("value", sa.JSON),
    sa.column("updated_at", sa.DateTime),
)


def _checkout_row() -> tuple[str, dict[str, Any]] | None:
    bind = op.get_bind()
    row = bind.execute(
        sa.select(_SETTINGS.c.tenant_id, _SETTINGS.c.value)
        .select_from(_SETTINGS.join(_TENANTS, _TENANTS.c.id == _SETTINGS.c.tenant_id))
        .where(_TENANTS.c.slug == _SLUG, _SETTINGS.c.key == "checkout")
    ).first()
    if row is None:
        return None
    value = row.value
    if isinstance(value, str):
        value = json.loads(value)
    return str(row.tenant_id), dict(value or {})


def _write(tenant_id: str, value: dict[str, Any]) -> None:
    op.get_bind().execute(
        sa.update(_SETTINGS)
        .where(_SETTINGS.c.tenant_id == tenant_id, _SETTINGS.c.key == "checkout")
        .values(value=value, updated_at=datetime.now(UTC).replace(tzinfo=None))
    )


def upgrade() -> None:
    found = _checkout_row()
    if found is None:
        return  # banco sem a SGPipas (testes, outros ambientes): nada a fazer
    tenant_id, value = found
    if "require_whatsapp" not in value:
        _write(tenant_id, {**value, "require_whatsapp": True})


def downgrade() -> None:
    found = _checkout_row()
    if found is None:
        return
    tenant_id, value = found
    if "require_whatsapp" in value:
        _write(tenant_id, {k: v for k, v in value.items() if k != "require_whatsapp"})
