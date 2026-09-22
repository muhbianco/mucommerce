"""Orders in the store's panel (stage E, S16): who may move what, and what the screen sees."""

# The `shop` fixture is imported by name (pytest finds it that way) and used as a parameter.
# ruff: noqa: F811
from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.scopes import TenantRole
from app.payments.providers import fake
from app.tenancy.models import Tenant
from tests.test_catalog import member_headers
from tests.test_checkout_place import balance, shop  # noqa: F401
from tests.test_customer_orders import placed_order

CARD = {"token": fake.APPROVE_TOKEN, "payment_method_id": "visa", "installments": 1}
Shop = tuple[Tenant, dict[str, str], dict[str, str]]


@pytest.fixture(autouse=True)
def fake_payments(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(settings, "payments_allowed_providers", "fake")
    monkeypatch.setattr(settings, "payments_fake_webhook_secret", SecretStr("whsec-" + "o" * 30))
    yield


def orders_url(tenant: Tenant, order_id: str = "") -> str:
    base = f"/api/v1/admin/tenants/{tenant.id}/orders"
    return f"{base}/{order_id}" if order_id else base


async def _paid_order(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], shop: Shop
) -> tuple[dict[str, Any], str]:
    tenant, owner, me = shop
    await client.put(
        f"/api/v1/admin/tenants/{tenant.id}/payments/providers/fake",
        json={"enabled": True},
        headers=owner,
    )
    order, variant = await placed_order(client, session_factory, shop)
    paid = await client.post(
        f"/api/v1/checkout/orders/{order['id']}/payments",
        json={"provider": "fake", "method": "card", "card": CARD},
        headers=me | {"Idempotency-Key": str(uuid.uuid4())},
    )
    assert paid.json()["status"] == "approved"
    return order, variant


async def move(
    client: AsyncClient, tenant: Tenant, headers: dict[str, str], order_id: str, **body: Any
) -> Any:
    return await client.post(
        f"{orders_url(tenant, order_id)}/transition", json=body, headers=headers
    )


async def test_the_store_follows_an_order_from_paid_to_delivered(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], shop: Shop
) -> None:
    tenant, owner, _ = shop
    order, _ = await _paid_order(client, session_factory, shop)
    detail = (await client.get(orders_url(tenant, order["id"]), headers=owner)).json()
    assert detail["order"]["status"] == "payment_confirmed"
    assert [p["status"] for p in detail["payments"]] == ["approved"]
    assert detail["allowed_transitions"] == ["cancelled", "accepted"]
    assert detail["customer"]["name"] == "Maria" and detail["version"] >= 1

    ops = await member_headers(client, session_factory, tenant, TenantRole.OPS)
    for target in ("accepted", "in_production", "ready_for_pickup", "delivered"):
        moved = await move(client, tenant, ops, order["id"], to=target)
        assert moved.status_code == 200, moved.text
        assert moved.json()["order"]["status"] == target
    final = (await client.get(orders_url(tenant, order["id"]), headers=owner)).json()
    assert final["order"]["fulfillment_status"] == "picked_up"
    assert [e["status"] for e in final["order"]["timeline"]][-1] == "delivered"
    assert final["allowed_transitions"] == []  # nothing left to do


async def test_each_role_sees_only_what_it_may_do(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: Shop,
    operator_headers: dict[str, str],
) -> None:
    tenant, owner, _ = shop
    order, _ = await _paid_order(client, session_factory, shop)

    support = await member_headers(client, session_factory, tenant, TenantRole.SUPPORT)
    seen = await client.get(orders_url(tenant, order["id"]), headers=support)
    assert seen.status_code == 200 and seen.json()["allowed_transitions"] == []
    assert (await move(client, tenant, support, order["id"], to="accepted")).status_code == 403

    ops = await member_headers(client, session_factory, tenant, TenantRole.OPS)
    assert (await client.get(orders_url(tenant, order["id"]), headers=ops)).json()[
        "allowed_transitions"
    ] == ["accepted"]  # ops moves it along but cannot cancel
    refused = await move(client, tenant, ops, order["id"], to="cancelled")
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "permission_denied"

    # Platform staff read orders; the store runs them.
    assert (await client.get(orders_url(tenant), headers=operator_headers)).status_code == 200
    staff = await move(client, tenant, operator_headers, order["id"], to="accepted")
    assert staff.status_code == 403

    assert (await client.get(orders_url(tenant, order["id"]), headers=owner)).json()[
        "allowed_transitions"
    ] == ["cancelled", "accepted"]


async def test_a_screen_that_fell_behind_does_not_move_the_wrong_order(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], shop: Shop
) -> None:
    tenant, owner, _ = shop
    order, _ = await _paid_order(client, session_factory, shop)
    detail = (await client.get(orders_url(tenant, order["id"]), headers=owner)).json()
    version = detail["version"]
    assert (await move(client, tenant, owner, order["id"], to="accepted")).status_code == 200

    stale = await move(
        client, tenant, owner, order["id"], to="in_production", expected_version=version
    )
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "stale_order"
    wrong = await move(client, tenant, owner, order["id"], to="delivered")
    assert wrong.status_code == 409
    assert wrong.json()["error"]["code"] == "invalid_transition"
    assert "ready_for_pickup" in wrong.json()["error"]["details"]["allowed"]


async def test_cancelling_a_paid_order_from_the_panel_gives_the_money_back(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], shop: Shop
) -> None:
    tenant, owner, _ = shop
    order, variant = await _paid_order(client, session_factory, shop)
    assert await balance(session_factory, variant) == (3000, 0)

    cancelled = await move(
        client, tenant, owner, order["id"], to="cancelled", reason="sem ingrediente"
    )
    assert cancelled.status_code == 200, cancelled.text
    body = cancelled.json()
    assert body["order"]["status"] == "cancelled"
    assert [(r["kind"], r["status"], r["amount_cents"]) for r in body["refunds"]] == [
        ("operator", "completed", 3000)
    ]
    assert body["order"]["refund_status"] == "full"
    assert await balance(session_factory, variant) == (5000, 0)


async def test_the_list_filters_and_never_shows_another_stores_order(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], shop: Shop
) -> None:
    from tests.shoppers import selling_store

    tenant, owner, _ = shop
    order, _ = await _paid_order(client, session_factory, shop)
    listed = await client.get(orders_url(tenant), headers=owner)
    assert [o["number"] for o in listed.json()["items"]] == [1]
    assert listed.json()["items"][0]["customer_name"] == "Maria"
    by_status = await client.get(f"{orders_url(tenant)}?status=cancelled", headers=owner)
    assert by_status.json()["items"] == []
    found = await client.get(f"{orders_url(tenant)}?q=1", headers=owner)
    assert [o["id"] for o in found.json()["items"]] == [order["id"]]
    missed = await client.get(f"{orders_url(tenant)}?q=Joana", headers=owner)
    assert missed.json()["items"] == []

    other = await selling_store(session_factory, "beta", flags={"checkout": True})
    other_owner = await member_headers(client, session_factory, other)
    assert (await client.get(orders_url(other), headers=other_owner)).json()["items"] == []
    assert (
        await client.get(orders_url(other, order["id"]), headers=other_owner)
    ).status_code == 404
