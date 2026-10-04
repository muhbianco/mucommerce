"""Copia as caixas do JSON de envio para `shipping_packages`, sem mudar o frete de ninguém.

Só dados. Para cada loja com `fulfillment.shipping`:

- `box` (a caixa padrão, sem id) vira a embalagem "Caixa padrão", marcada como padrão e
  automática — é a que o motor v1 usava para todo produto sem escolha;
- cada `boxes[i]` vira embalagem **com o mesmo id**, `auto_select=false` (no v1 ela só valia para
  quem apontava para ela), na posição i+1;
- havendo `boxes` sem `box`, nasce a "Caixa padrão" com a caixa genérica do v1 (200x150x100 mm,
  30 kg), que era o que valia para quem não escolhia;
- medida de fora = medida de dentro, para a cobrança continuar igual à do v1;
- produto com `shipping_box_id` de uma embalagem copiada vira `restricted`, com uma regra para
  ela.

O JSON fica intocado: com a flag `shipping.packing_v2` desligada, tudo segue pelo v1. O
downgrade apaga as embalagens e regras (inclusive as criadas depois) e volta os produtos para
`auto`.

Revision ID: 0037_backfill_shipping_packages
Revises: 0036_product_packing_traits
Create Date: 2026-10-04
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from alembic import op
from uuid6 import uuid7

revision: str = "0037_backfill_shipping_packages"
down_revision: str | None = "0036_product_packing_traits"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: A caixa genérica do motor v1 (`DEFAULT_BOX_MM`), congelada aqui: a migration não pode mudar
#: de comportamento se o código mudar depois.
_V1_DEFAULT_BOX = {"width_mm": 200, "height_mm": 150, "depth_mm": 100, "max_weight_grams": 30_000}
_DEFAULT_NAME = "Caixa padrão"
_ACTOR = "system:migration-0037"

_SETTINGS = sa.table(
    "tenant_settings",
    sa.column("tenant_id", sa.String),
    sa.column("key", sa.String),
    sa.column("value", sa.JSON),
)
_PACKAGES = sa.table(
    "shipping_packages",
    sa.column("id", sa.String),
    sa.column("tenant_id", sa.String),
    sa.column("name", sa.String),
    sa.column("kind", sa.String),
    sa.column("inner_length_mm", sa.Integer),
    sa.column("inner_width_mm", sa.Integer),
    sa.column("inner_height_mm", sa.Integer),
    sa.column("outer_length_mm", sa.Integer),
    sa.column("outer_width_mm", sa.Integer),
    sa.column("outer_height_mm", sa.Integer),
    sa.column("empty_weight_grams", sa.Integer),
    sa.column("max_weight_grams", sa.Integer),
    sa.column("material_cost_cents", sa.BigInteger),
    sa.column("auto_select", sa.Boolean),
    sa.column("default_marker", sa.SmallInteger),
    sa.column("active", sa.Boolean),
    sa.column("position", sa.Integer),
    sa.column("created_by_actor", sa.String),
    sa.column("updated_by_actor", sa.String),
    sa.column("created_at", sa.DateTime),
    sa.column("updated_at", sa.DateTime),
)
_RULES = sa.table(
    "product_package_rules",
    sa.column("id", sa.String),
    sa.column("tenant_id", sa.String),
    sa.column("product_id", sa.String),
    sa.column("package_id", sa.String),
    sa.column("max_units", sa.Integer),
    sa.column("created_at", sa.DateTime),
    sa.column("updated_at", sa.DateTime),
)
_PRODUCTS = sa.table(
    "products",
    sa.column("id", sa.String),
    sa.column("tenant_id", sa.String),
    sa.column("shipping_box_id", sa.String),
    sa.column("packing_mode", sa.String),
)


def _shipping_by_tenant() -> list[tuple[str, dict[str, Any]]]:
    rows = op.get_bind().execute(
        sa.select(_SETTINGS.c.tenant_id, _SETTINGS.c.value).where(_SETTINGS.c.key == "fulfillment")
    )
    result = []
    for tenant_id, value in rows:
        if isinstance(value, str):  # MariaDB returns JSON columns as text on some drivers
            value = json.loads(value)
        shipping = (value or {}).get("shipping") or {}
        if shipping.get("box") or shipping.get("boxes"):
            result.append((tenant_id, shipping))
    return result


def _unique_name(wanted: str, taken: set[str]) -> str:
    name, n = wanted, 1
    while name.casefold() in taken:
        n += 1
        name = f"{wanted} ({n})"[:60]
    taken.add(name.casefold())
    return name


def _package_row(
    *,
    package_id: str,
    tenant_id: str,
    name: str,
    box: dict[str, Any],
    default: bool,
    position: int,
    now: datetime,
) -> dict[str, Any]:
    comprimento = int(box["depth_mm"])
    largura = int(box["width_mm"])
    altura = int(box["height_mm"])
    return {
        "id": package_id,
        "tenant_id": tenant_id,
        "name": name,
        "kind": "box",
        "inner_length_mm": comprimento,
        "inner_width_mm": largura,
        "inner_height_mm": altura,
        # De fora = de dentro: o v1 cotava a medida cadastrada, e o backfill não muda preço.
        "outer_length_mm": comprimento,
        "outer_width_mm": largura,
        "outer_height_mm": altura,
        "empty_weight_grams": int(box.get("empty_weight_grams") or 0),
        "max_weight_grams": int(box.get("max_weight_grams") or 30_000),
        "material_cost_cents": None,
        "auto_select": default,
        "default_marker": 1 if default else None,
        "active": True,
        "position": position,
        "created_by_actor": _ACTOR,
        "updated_by_actor": _ACTOR,
        "created_at": now,
        "updated_at": now,
    }


def upgrade() -> None:
    bind = op.get_bind()
    now = datetime.now(UTC).replace(tzinfo=None)
    copiadas: set[tuple[str, str]] = set()
    for tenant_id, shipping in _shipping_by_tenant():
        boxes = [b for b in shipping.get("boxes") or [] if isinstance(b, dict) and b.get("id")]
        taken = {str(b.get("name") or "").casefold() for b in boxes}
        rows = [
            _package_row(
                package_id=str(uuid7()),
                tenant_id=tenant_id,
                name=_unique_name(_DEFAULT_NAME, taken),
                box=shipping.get("box") or _V1_DEFAULT_BOX,
                default=True,
                position=0,
                now=now,
            )
        ]
        for i, box in enumerate(boxes, start=1):
            rows.append(
                _package_row(
                    package_id=str(box["id"]),
                    tenant_id=tenant_id,
                    name=str(box.get("name") or f"Caixa {i}")[:60],
                    box=box,
                    default=False,
                    position=i,
                    now=now,
                )
            )
            copiadas.add((tenant_id, str(box["id"])))
        bind.execute(_PACKAGES.insert(), rows)

    if not copiadas:
        return
    produtos = bind.execute(
        sa.select(_PRODUCTS.c.id, _PRODUCTS.c.tenant_id, _PRODUCTS.c.shipping_box_id).where(
            _PRODUCTS.c.shipping_box_id.is_not(None)
        )
    ).all()
    for product_id, tenant_id, box_id in produtos:
        if (tenant_id, box_id) not in copiadas:
            continue  # id que já não existia: o v1 tratava como "caixa padrão", o v2 como auto
        bind.execute(
            _RULES.insert().values(
                id=str(uuid7()),
                tenant_id=tenant_id,
                product_id=product_id,
                package_id=box_id,
                max_units=None,
                created_at=now,
                updated_at=now,
            )
        )
        bind.execute(
            _PRODUCTS.update().where(_PRODUCTS.c.id == product_id).values(packing_mode="restricted")
        )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(_RULES.delete())
    bind.execute(_PACKAGES.delete())
    bind.execute(
        _PRODUCTS.update().where(_PRODUCTS.c.packing_mode != "auto").values(packing_mode="auto")
    )
