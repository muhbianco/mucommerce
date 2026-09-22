"""Fulfillment settings V2 (stage E, S2): zones, windows, stable ids, evaluation, migration."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import sqlalchemy as sa
from alembic import command
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.bootstrap import alembic_config
from app.core.exceptions import ValidationError
from app.fulfillment.service import FulfillmentChoice, evaluate, offered_modes
from app.fulfillment.windows import find_slot, slots
from app.fulfillment.zones import match_zone, normalize_place
from app.tenancy.context import TenantContext
from app.tenancy.settings_normalizers import normalize_fulfillment
from app.tenancy.settings_schemas import (
    DeliveryZone,
    FulfillmentV2,
    SchedulingSettings,
    validate_setting,
)
from tests.test_catalog import catalog_tenant, member_headers

SP = ZoneInfo("America/Sao_Paulo")
# Monday 2026-12-07 10:00 in São Paulo.
MONDAY_10H = datetime(2026, 12, 7, 13, 0, tzinfo=UTC)


def zone(**overrides: Any) -> DeliveryZone:
    base: dict[str, Any] = {
        "id": "z1",
        "name": "Centro",
        "kind": "cep_ranges",
        "cep_ranges": [{"start": "01000000", "end": "01099999"}],
    }
    return DeliveryZone.model_validate(base | overrides)


# ----------------------------------------------------------------------------- zones
def test_cep_ranges_are_inclusive_and_checked_before_districts() -> None:
    by_district = zone(
        id="z2",
        name="Bela Vista",
        kind="districts",
        cep_ranges=[],
        city="São Paulo",
        state="SP",
        districts=["Bela Vista", "Liberdade"],
    )
    centro = zone()
    for cep in ("01000-000", "01099999"):
        assert match_zone([by_district, centro], cep=cep, city=None, state=None, district=None)
    assert match_zone([centro], cep="01100000", city=None, state=None, district=None) is None
    found = match_zone(
        [by_district, centro], cep="01310100", city="sao  paulo", state="sp", district="LIBERDADE"
    )
    assert found is not None and found.id == "z2"
    other_city = match_zone(
        [by_district], cep="13000000", city="Campinas", state="SP", district="Liberdade"
    )
    assert other_city is None
    inactive = zone(active=False)
    assert match_zone([inactive], cep="01000000", city=None, state=None, district=None) is None
    assert normalize_place("  São   Paulo ") == "sao paulo"


def test_zone_and_window_shapes_are_validated() -> None:
    for bad in (
        {"delivery": {"zones": [{"id": "z", "name": "Z", "kind": "cep_ranges"}]}},
        {
            "delivery": {
                "zones": [{"id": "z", "name": "Z", "kind": "districts", "districts": ["A"]}]
            }
        },
        {
            "delivery": {
                "zones": [
                    {
                        "id": "z",
                        "name": "Z",
                        "kind": "cep_ranges",
                        "cep_ranges": [{"start": "02000000", "end": "01000000"}],
                    }
                ]
            }
        },
        {
            "scheduling": {
                "windows": [{"weekday": 0, "start": "18:00", "end": "09:00", "modes": ["pickup"]}]
            }
        },
        {"pickup": {"locations": [{"id": "a", "name": "Loja", "address": "x"}] * 2}},
        {"modes": ["pickup"]},  # V1 shape
    ):
        with pytest.raises(ValidationError):
            validate_setting("fulfillment", bad)


# ----------------------------------------------------------------------------- ids
def test_ids_survive_rename_and_reorder_and_unknown_ids_are_refused() -> None:
    first = normalize_fulfillment(
        None,
        {"pickup": {"locations": [{"name": "Loja", "address": "Rua A, 1"}]}},
    )
    loc_id = first["pickup"]["locations"][0]["id"]
    renamed = normalize_fulfillment(
        first,
        {
            "pickup": {
                "locations": [
                    {"name": "Quiosque", "address": "Rua B, 2"},
                    {"id": loc_id, "name": "Loja do centro", "address": "Rua A, 1"},
                ]
            }
        },
    )
    assert renamed["pickup"]["locations"][1]["id"] == loc_id
    by_name = normalize_fulfillment(
        renamed, {"pickup": {"locations": [{"name": "quiosque", "address": "x"}]}}
    )
    assert by_name["pickup"]["locations"][0]["id"] == renamed["pickup"]["locations"][0]["id"]
    with pytest.raises(ValidationError):
        normalize_fulfillment(
            first, {"pickup": {"locations": [{"id": "nope", "name": "x", "address": "y"}]}}
        )


# ----------------------------------------------------------------------------- windows
def test_slots_follow_the_store_clock_and_lead_time() -> None:
    cfg = SchedulingSettings(
        enabled=True,
        windows=[
            {"weekday": 0, "start": "10:30", "end": "12:00", "modes": ["pickup"]},
            {"weekday": 1, "start": "09:00", "end": "11:00", "modes": ["pickup", "delivery"]},
        ],
        min_lead_minutes=60,
        days_ahead=7,
    )
    offered = slots(cfg, SP, MONDAY_10H, "pickup")
    # Monday 10:30 starts in 30 min (< lead): the next is Tuesday, then the following Monday.
    assert [(s.date, s.start) for s in offered[:2]] == [
        (date(2026, 12, 8), "09:00"),
        (date(2026, 12, 14), "10:30"),
    ]
    assert offered[0].starts_at == datetime(2026, 12, 8, 12, 0, tzinfo=UTC)
    assert [s.start for s in slots(cfg, SP, MONDAY_10H, "delivery")][:1] == ["09:00"]
    assert find_slot(cfg, SP, MONDAY_10H, "pickup", day=date(2026, 12, 7), start="10:30") is None


# ----------------------------------------------------------------------------- evaluate
@dataclass
class Address:
    postal_code: str = "01001000"
    city: str = "São Paulo"
    state: str = "SP"
    district: str = "Sé"


def store(flags: dict[str, bool], fulfillment: dict[str, Any]) -> TenantContext:
    value = FulfillmentV2.model_validate(normalize_fulfillment(None, fulfillment)).model_dump()
    return TenantContext(
        id="t",
        slug="t",
        name="T",
        public_key="k",
        status="active",
        timezone="America/Sao_Paulo",
        locale="pt-BR",
        currency="BRL",
        features=flags,
        settings={"fulfillment": value},
    )


def test_evaluate_prices_the_zone_and_reports_problems() -> None:
    cfg = {
        "min_order_cents": 1000,
        "pickup": {"enabled": True, "locations": [{"name": "Loja", "address": "Rua A"}]},
        "delivery": {
            "enabled": True,
            "zones": [
                {
                    "name": "Centro",
                    "kind": "cep_ranges",
                    "cep_ranges": [{"start": "01000000", "end": "01099999"}],
                    "fee_cents": 800,
                    "min_order_cents": 3000,
                }
            ],
        },
    }
    tenant = store({"pickup": True, "delivery": True}, cfg)
    assert offered_modes(tenant) == ["pickup", "delivery"]
    delivery = evaluate(
        tenant,
        FulfillmentChoice("delivery"),
        subtotal_cents=5000,
        address=Address(),
        now=MONDAY_10H,
    )
    assert (delivery.fee_cents, delivery.problems, delivery.snapshot["name"]) == (800, (), "Centro")
    cheap = evaluate(
        tenant,
        FulfillmentChoice("delivery"),
        subtotal_cents=2000,
        address=Address(),
        now=MONDAY_10H,
    )
    assert cheap.problems == ("below_minimum",)  # the zone's minimum beats the store's
    far = evaluate(
        tenant,
        FulfillmentChoice("delivery"),
        subtotal_cents=5000,
        address=Address(postal_code="20000000", city="Rio", state="RJ"),
        now=MONDAY_10H,
    )
    assert far.problems == ("out_of_zone",)
    location_id = tenant.settings["fulfillment"]["pickup"]["locations"][0]["id"]
    pickup = evaluate(
        tenant,
        FulfillmentChoice("pickup", pickup_location_id=location_id),
        subtotal_cents=1000,
        address=None,
        now=MONDAY_10H,
    )
    assert (pickup.fee_cents, pickup.problems) == (0, ())
    unknown = evaluate(
        tenant,
        FulfillmentChoice("pickup", pickup_location_id="x"),
        subtotal_cents=1000,
        address=None,
        now=MONDAY_10H,
    )
    assert unknown.problems == ("location_unknown",)

    no_flag = store({"pickup": True}, cfg)
    assert offered_modes(no_flag) == ["pickup"]
    assert evaluate(
        no_flag,
        FulfillmentChoice("delivery"),
        subtotal_cents=5000,
        address=Address(),
        now=MONDAY_10H,
    ).problems == ("mode_unavailable",)


def test_scheduled_stores_need_an_offered_slot() -> None:
    cfg = {
        "pickup": {"enabled": True, "locations": [{"name": "Loja", "address": "Rua A"}]},
        "scheduling": {
            "enabled": True,
            "windows": [{"weekday": 1, "start": "09:00", "end": "11:00", "modes": ["pickup"]}],
        },
    }
    tenant = store({"pickup": True}, cfg)
    loc = tenant.settings["fulfillment"]["pickup"]["locations"][0]["id"]
    missing = evaluate(
        tenant,
        FulfillmentChoice("pickup", pickup_location_id=loc),
        subtotal_cents=0,
        address=None,
        now=MONDAY_10H,
    )
    assert missing.problems == ("slot_required",)
    chosen = evaluate(
        tenant,
        FulfillmentChoice(
            "pickup", pickup_location_id=loc, slot_date=date(2026, 12, 8), slot_start="09:00"
        ),
        subtotal_cents=0,
        address=None,
        now=MONDAY_10H,
    )
    assert chosen.problems == () and chosen.slot is not None
    wrong = evaluate(
        tenant,
        FulfillmentChoice(
            "pickup", pickup_location_id=loc, slot_date=date(2026, 12, 8), slot_start="10:00"
        ),
        subtotal_cents=0,
        address=None,
        now=MONDAY_10H,
    )
    assert wrong.problems == ("slot_invalid",)


# ----------------------------------------------------------------------------- panel + storefront
async def test_panel_writes_fulfillment_and_the_storefront_sees_the_projection(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant = await catalog_tenant(session_factory, "alpha")
    headers = await member_headers(client, session_factory, tenant)
    url = f"/api/v1/admin/tenants/{tenant.id}/settings/fulfillment"
    body = {
        "value": {
            "pickup": {"enabled": True, "locations": [{"name": "Loja", "address": "Rua A, 1"}]},
            "delivery": {
                "enabled": True,
                "zones": [
                    {
                        "name": "Centro",
                        "kind": "cep_ranges",
                        "cep_ranges": [{"start": "01000000", "end": "01099999"}],
                        "fee_cents": 800,
                    }
                ],
            },
        }
    }
    saved = await client.put(url, json=body, headers=headers)
    assert saved.status_code == 200, saved.text
    location_id = saved.json()["pickup"]["locations"][0]["id"]
    again = await client.put(url, json=body, headers=headers)
    assert again.json()["pickup"]["locations"][0]["id"] == location_id

    from tests.test_storefront_catalog import set_access

    await set_access(session_factory, tenant, "public")
    context = await client.get("/api/v1/storefront/context", headers={"host": "alpha.loja.test"})
    public = context.json()["fulfillment"]
    assert public["modes"] == []  # flags `pickup` / `delivery` still off
    from app.tenancy.service import Actor, TenantService

    async with session_factory() as session:
        service = TenantService(session)
        await service.set_features(
            await service.get_or_404(tenant.id), {"pickup": True}, Actor.system("tests")
        )
        await session.commit()
    context = await client.get("/api/v1/storefront/context", headers={"host": "alpha.loja.test"})
    public = context.json()["fulfillment"]
    assert public["modes"] == ["pickup"]
    assert public["pickup_locations"][0] == {
        "id": location_id,
        "name": "Loja",
        "address": "Rua A, 1",
        "instructions": None,
    }
    assert public["delivery_zones"] == [] and "cep_ranges" not in json.dumps(public)


# ----------------------------------------------------------------------------- migration
def _required_values(table: sa.Table, **given: Any) -> dict[str, Any]:
    values = dict(given)
    for column in table.columns:
        if column.name in values or column.nullable or column.server_default is not None:
            continue
        python_type = column.type.python_type if hasattr(column.type, "python_type") else str
        values[column.name] = {
            int: 0,
            bool: False,
            datetime: datetime(2026, 1, 1),
            dict: {},
        }.get(python_type, f"{column.name}-x")
    return values


def test_migration_turns_v1_rows_into_v2_and_back(tmp_path: Path) -> None:
    url = f"sqlite+aiosqlite:///{tmp_path / 'm.db'}"
    config = alembic_config(url)
    command.upgrade(config, "0016_events")
    engine = sa.create_engine(url.replace("+aiosqlite", ""))
    meta = sa.MetaData()
    tenants = sa.Table("tenants", meta, autoload_with=engine)
    settings_table = sa.Table("tenant_settings", meta, autoload_with=engine)
    with engine.begin() as conn:
        conn.execute(tenants.insert().values(_required_values(tenants, id="t1", slug="t1")))
        conn.execute(
            settings_table.insert().values(
                _required_values(
                    settings_table,
                    id="s1",
                    tenant_id="t1",
                    key="fulfillment",
                    value={"modes": ["pickup", "delivery"], "min_order_cents": 1500},
                    schema_version=1,
                )
            )
        )
    command.upgrade(config, "0017_fulfillment_v2")
    with engine.connect() as conn:
        value, version = conn.execute(
            sa.select(settings_table.c.value, settings_table.c.schema_version)
        ).one()
    value = json.loads(value) if isinstance(value, str) else value
    assert version == 2
    parsed = FulfillmentV2.model_validate(value)
    assert (parsed.pickup.enabled, parsed.delivery.enabled, parsed.min_order_cents) == (
        True,
        True,
        1500,
    )
    command.downgrade(config, "0016_events")
    with engine.connect() as conn:
        value, version = conn.execute(
            sa.select(settings_table.c.value, settings_table.c.schema_version)
        ).one()
    engine.dispose()
    value = json.loads(value) if isinstance(value, str) else value
    assert (version, value) == (1, {"modes": ["pickup", "delivery"], "min_order_cents": 1500})
