"""Inventory (phase 1, S5): adjustments with a ledger, low stock, idempotency, isolation."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.audit.models import OutboxEvent
from app.core.scopes import TenantRole
from app.inventory.models import InventoryBalance
from app.inventory.service import audit_ledger
from app.tenancy.context import CROSS_TENANT_OPTION
from app.tenancy.models import Tenant
from app.tenancy.service import Actor, TenantService
from tests.conftest import create_tenant
from tests.test_catalog import base, create_product, member_headers

CROSS = {CROSS_TENANT_OPTION: True}


async def stock_tenant(
    session_factory: async_sessionmaker[AsyncSession], slug: str, *, inventory: bool = True
) -> Tenant:
    tenant = await create_tenant(session_factory, slug)
    async with session_factory() as session:
        service = TenantService(session)
        await service.set_features(
            await service.get_or_404(tenant.id),
            {"catalog": True, "inventory": inventory},
            Actor.system("tests"),
        )
        await session.commit()
    return tenant


@pytest.fixture
async def shop(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> tuple[Tenant, dict[str, str], str]:
    tenant = await stock_tenant(session_factory, "alpha")
    headers = await member_headers(client, session_factory, tenant)
    product = await create_product(client, tenant, headers, name="Brownie")
    return tenant, headers, product["variants"][0]["id"]


_keys = iter(range(1_000_000))


async def adjust(
    client: AsyncClient,
    tenant: Tenant,
    headers: dict[str, str],
    key: str | None = None,
    **body: Any,
) -> Any:
    idem = key or f"k-{next(_keys)}"
    return await client.post(
        f"{base(tenant)}/inventory/adjustments",
        json=body,
        headers=headers | {"Idempotency-Key": idem},
    )


async def balance(client: AsyncClient, tenant: Tenant, headers: dict[str, str]) -> dict[str, Any]:
    page = (await client.get(f"{base(tenant)}/inventory/balances", headers=headers)).json()
    return dict(page["items"][0])


async def test_receipt_loss_count_and_ledger(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: tuple[Tenant, dict[str, str], str],
) -> None:
    tenant, headers, variant_id = shop
    start = await balance(client, tenant, headers)
    assert (start["on_hand"], start["available"], start["low_stock"]) == ("0.000", "0.000", False)

    receipt = await adjust(
        client,
        tenant,
        headers,
        kind="receipt",
        lines=[{"variant_id": variant_id, "quantity": "10", "unit_cost_cents": 450}],
    )
    assert receipt.status_code == 201, receipt.text
    [movement] = receipt.json()["movements"]
    assert movement["movement_type"] == "purchase_in"
    assert (movement["quantity"], movement["balance_after"]) == ("10.000", "10.000")
    assert movement["unit_cost_micro"] == 4_500_000

    no_reason = await adjust(
        client, tenant, headers, kind="loss", lines=[{"variant_id": variant_id, "quantity": 3}]
    )
    assert no_reason.status_code == 422
    loss = await adjust(
        client,
        tenant,
        headers,
        kind="loss",
        reason="Quebra no transporte",
        lines=[{"variant_id": variant_id, "quantity": 3}],
    )
    assert loss.json()["movements"][0]["balance_after"] == "7.000"

    count = await adjust(
        client, tenant, headers, kind="count", lines=[{"variant_id": variant_id, "quantity": 5}]
    )
    assert count.json()["movements"][0]["quantity"] == "-2.000"
    assert (await balance(client, tenant, headers))["on_hand"] == "5.000"

    ledger = await client.get(
        f"{base(tenant)}/inventory/variants/{variant_id}/movements",
        params={"limit": 2},
        headers=headers,
    )
    page = ledger.json()
    assert [m["movement_type"] for m in page["items"]] == ["count", "loss"]
    rest = await client.get(
        f"{base(tenant)}/inventory/variants/{variant_id}/movements",
        params={"cursor": page["next_cursor"]},
        headers=headers,
    )
    assert [m["movement_type"] for m in rest.json()["items"]] == ["purchase_in"]

    async with session_factory() as session:
        assert await audit_ledger(session) == 0


async def test_adjustment_is_all_or_nothing(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: tuple[Tenant, dict[str, str], str],
) -> None:
    tenant, headers, first = shop
    second = (await create_product(client, tenant, headers, name="Bolo"))["variants"][0]["id"]
    await adjust(
        client, tenant, headers, kind="receipt", lines=[{"variant_id": first, "quantity": 2}]
    )
    refused = await adjust(
        client,
        tenant,
        headers,
        kind="adjustment",
        reason="Correção",
        lines=[{"variant_id": first, "quantity": -1}, {"variant_id": second, "quantity": -1}],
    )
    assert refused.status_code == 409
    error = refused.json()["error"]
    assert error["details"]["code"] == "insufficient_stock"
    assert [line["variant_id"] for line in error["details"]["lines"]] == [second]
    page = (await client.get(f"{base(tenant)}/inventory/balances", headers=headers)).json()
    assert sorted(item["on_hand"] for item in page["items"]) == ["0.000", "2.000"]


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({"kind": "receipt", "lines": [{"quantity": "1.5"}]}, 422),  # unit product: whole only
        ({"kind": "receipt", "lines": [{"quantity": 0}]}, 422),
        ({"kind": "adjustment", "reason": "x", "lines": [{"quantity": 0}]}, 422),
        ({"kind": "count", "lines": [{"quantity": -1}]}, 422),
        ({"kind": "loss", "reason": "x", "lines": [{"quantity": 1, "unit_cost_cents": 1}]}, 422),
        ({"kind": "receipt", "lines": [{"quantity": "1.0001"}]}, 422),  # 3 decimals max
        ({"kind": "receipt", "lines": []}, 422),
        ({"kind": "desconhecido", "lines": [{"quantity": 1}]}, 422),
    ],
)
async def test_invalid_adjustments(
    client: AsyncClient,
    shop: tuple[Tenant, dict[str, str], str],
    body: dict[str, Any],
    status: int,
) -> None:
    tenant, headers, variant_id = shop
    for line in body["lines"]:
        line["variant_id"] = variant_id
    assert (await adjust(client, tenant, headers, **body)).status_code == status


async def test_duplicate_lines_untracked_and_weight_products(
    client: AsyncClient, shop: tuple[Tenant, dict[str, str], str]
) -> None:
    tenant, headers, variant_id = shop
    duplicated = await adjust(
        client,
        tenant,
        headers,
        kind="receipt",
        lines=[
            {"variant_id": variant_id, "quantity": 1},
            {"variant_id": variant_id, "quantity": 1},
        ],
    )
    assert duplicated.status_code == 422

    service = await create_product(
        client, tenant, headers, name="Encomenda", stock_policy="made_to_order"
    )
    untracked = await adjust(
        client,
        tenant,
        headers,
        kind="receipt",
        lines=[{"variant_id": service["variants"][0]["id"], "quantity": 1}],
    )
    assert untracked.status_code == 422
    listed = (await client.get(f"{base(tenant)}/inventory/balances", headers=headers)).json()
    assert [item["sku"] for item in listed["items"]] == ["P00001"]  # only tracked variants

    granola = await create_product(
        client, tenant, headers, name="Granola", sold_by="weight", unit_label="g"
    )
    weighed = await adjust(
        client,
        tenant,
        headers,
        kind="receipt",
        lines=[{"variant_id": granola["variants"][0]["id"], "quantity": "250.5"}],
    )
    assert weighed.status_code == 201
    assert weighed.json()["movements"][0]["balance_after"] == "250.500"


async def test_idempotency_key_is_required_and_replays(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: tuple[Tenant, dict[str, str], str],
) -> None:
    tenant, headers, variant_id = shop
    body = {"kind": "receipt", "lines": [{"variant_id": variant_id, "quantity": 4}]}
    missing = await client.post(f"{base(tenant)}/inventory/adjustments", json=body, headers=headers)
    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "idempotency_key_required"

    first = await adjust(client, tenant, headers, key="same", **body)
    second = await adjust(client, tenant, headers, key="same", **body)
    assert second.headers.get("Idempotent-Replayed") == "true"
    assert second.json() == first.json()
    assert (await balance(client, tenant, headers))["on_hand"] == "4.000"
    changed = await adjust(
        client,
        tenant,
        headers,
        key="same",
        kind="receipt",
        lines=[{"variant_id": variant_id, "quantity": 5}],
    )
    assert changed.status_code == 422


async def test_low_stock_threshold_list_and_event(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: tuple[Tenant, dict[str, str], str],
) -> None:
    tenant, headers, variant_id = shop
    await adjust(
        client, tenant, headers, kind="receipt", lines=[{"variant_id": variant_id, "quantity": 5}]
    )
    level = await client.put(
        f"{base(tenant)}/inventory/variants/{variant_id}/min-level",
        json={"min_level": 4},
        headers=headers,
    )
    assert level.json()["low_stock"] is False
    low_url = f"{base(tenant)}/inventory/balances"
    assert (await client.get(low_url, params={"low_stock": "true"}, headers=headers)).json()[
        "items"
    ] == []

    for _ in range(2):  # 5 → 4 → 3: crosses the threshold once
        await adjust(
            client,
            tenant,
            headers,
            kind="loss",
            reason="Degustação",
            lines=[{"variant_id": variant_id, "quantity": 1}],
        )
    low = (await client.get(low_url, params={"low_stock": "true"}, headers=headers)).json()
    assert [(i["on_hand"], i["low_stock"]) for i in low["items"]] == [("3.000", True)]
    async with session_factory() as session:
        count = (
            await session.execute(
                select(func.count())
                .select_from(OutboxEvent)
                .where(OutboxEvent.event_type == "inventory.low_stock")
                .execution_options(**CROSS)
            )
        ).scalar_one()
    assert count == 1


async def test_ledger_audit_detects_a_tampered_balance(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: tuple[Tenant, dict[str, str], str],
) -> None:
    tenant, headers, variant_id = shop
    await adjust(
        client, tenant, headers, kind="receipt", lines=[{"variant_id": variant_id, "quantity": 5}]
    )
    async with session_factory() as session:
        await session.execute(
            update(InventoryBalance)
            .where(InventoryBalance.variant_id == variant_id)
            .values(on_hand_milli=9_000)
            .execution_options(synchronize_session=False, **CROSS)
        )
        await session.commit()
    async with session_factory() as session:
        assert await audit_ledger(session) == 1


async def test_flag_scopes_and_other_tenants(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: tuple[Tenant, dict[str, str], str],
) -> None:
    tenant, headers, variant_id = shop

    support = await member_headers(client, session_factory, tenant, TenantRole.SUPPORT)
    assert (
        await client.get(f"{base(tenant)}/inventory/balances", headers=support)
    ).status_code == 403
    ops = await member_headers(client, session_factory, tenant, TenantRole.OPS)
    ops_receipt = await adjust(
        client, tenant, ops, kind="receipt", lines=[{"variant_id": variant_id, "quantity": 1}]
    )
    assert ops_receipt.status_code == 201

    dark = await stock_tenant(session_factory, "dark", inventory=False)
    dark_headers = await member_headers(client, session_factory, dark)
    off = await client.get(f"{base(dark)}/inventory/balances", headers=dark_headers)
    assert off.json()["error"]["code"] == "feature_disabled"

    beta = await stock_tenant(session_factory, "beta")
    beta_headers = await member_headers(client, session_factory, beta)
    foreign = await adjust(
        client,
        beta,
        beta_headers,
        kind="receipt",
        lines=[{"variant_id": variant_id, "quantity": 1}],
    )
    assert foreign.status_code == 422
    movements = await client.get(
        f"{base(beta)}/inventory/variants/{variant_id}/movements", headers=beta_headers
    )
    assert movements.status_code == 404
    level = await client.put(
        f"{base(beta)}/inventory/variants/{variant_id}/min-level",
        json={"min_level": 1},
        headers=beta_headers,
    )
    assert level.status_code == 404
    assert (await balance(client, tenant, headers))["on_hand"] == "1.000"


async def test_archived_products_cannot_be_adjusted(
    client: AsyncClient, shop: tuple[Tenant, dict[str, str], str]
) -> None:
    tenant, headers, variant_id = shop
    product_id = (await client.get(f"{base(tenant)}/products", headers=headers)).json()["items"][0][
        "id"
    ]
    await client.delete(f"{base(tenant)}/products/{product_id}", headers=headers)
    refused = await adjust(
        client, tenant, headers, kind="receipt", lines=[{"variant_id": variant_id, "quantity": 1}]
    )
    assert refused.status_code == 422
