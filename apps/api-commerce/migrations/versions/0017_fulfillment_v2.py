"""settings: fulfillment V1 → V2 (pickup locations, delivery zones, windows)

Data only. V1 was `{modes, min_order_cents}`; V2 keeps `min_order_cents`, turns each mode into
`{enabled, locations|zones: []}` and adds `scheduling` (off). No location or zone exists yet,
so nothing becomes available until the store configures one. Downgrade writes V1 back from the
enabled flags (locations, zones and windows are lost).

Revision ID: 0017_fulfillment_v2
Revises: 0016_events
Create Date: 2026-09-22
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0017_fulfillment_v2"
down_revision: str | None = "0016_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SETTINGS = sa.table(
    "tenant_settings",
    sa.column("id", sa.String),
    sa.column("key", sa.String),
    sa.column("value", sa.JSON),
    sa.column("schema_version", sa.Integer),
)


def _rows(version: int) -> list[tuple[str, dict[str, Any]]]:
    rows = op.get_bind().execute(
        sa.select(_SETTINGS.c.id, _SETTINGS.c.value)
        .where(_SETTINGS.c.key == "fulfillment")
        .where(_SETTINGS.c.schema_version == version)
    )
    result = []
    for row_id, value in rows:
        if isinstance(value, str):  # MariaDB returns JSON columns as text on some drivers
            value = json.loads(value)
        result.append((row_id, value or {}))
    return result


def _write(row_id: str, value: dict[str, Any], version: int) -> None:
    op.get_bind().execute(
        _SETTINGS.update()
        .where(_SETTINGS.c.id == row_id)
        .values(value=value, schema_version=version)
    )


def upgrade() -> None:
    for row_id, v1 in _rows(1):
        modes = v1.get("modes") or ["pickup"]
        v2 = {
            "pickup": {"enabled": "pickup" in modes, "locations": []},
            "delivery": {"enabled": "delivery" in modes, "zones": []},
            "min_order_cents": int(v1.get("min_order_cents") or 0),
            "scheduling": {
                "enabled": False,
                "windows": [],
                "min_lead_minutes": 60,
                "days_ahead": 7,
            },
        }
        _write(row_id, v2, 2)


def downgrade() -> None:
    for row_id, v2 in _rows(2):
        modes = [
            mode for mode in ("pickup", "delivery") if (v2.get(mode) or {}).get("enabled")
        ] or ["pickup"]
        _write(row_id, {"modes": modes, "min_order_cents": int(v2.get("min_order_cents") or 0)}, 1)
