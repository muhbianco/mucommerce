"""Coupons (stage E, S17): what they take off, when they cannot be used, and giving one back."""

# The `shop` fixture is imported by name (pytest finds it that way) and used as a parameter.
# ruff: noqa: F811
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.coupons.models import Coupon, CouponRedemption
from app.coupons.rules import evaluate, percent_of
from app.models.base import utcnow
from app.orders.jobs import run_expire_orders
from app.orders.models import Order
from app.tenancy.context import CROSS_TENANT_OPTION
from app.tenancy.models import Tenant
from app.tenancy.service import Actor, TenantService
from tests.test_checkout_place import order_body, place, ready_cart, shop  # noqa: F401
from tests.test_pricing import product
from tests.test_storefront_catalog import set_stock

CROSS = {CROSS_TENANT_OPTION: True}
CART = "/api/v1/cart"
Shop = tuple[Tenant, dict[str, str], dict[str, str]]


def coupons_url(tenant: Tenant, coupon_id: str = "") -> str:
    base = f"/api/v1/admin/tenants/{tenant.id}/coupons"
    return f"{base}/{coupon_id}" if coupon_id else base


async def with_coupons(session_factory: async_sessionmaker[AsyncSession], tenant: Tenant) -> None:
    async with session_factory() as session:
        service = TenantService(session)
        await service.set_features(
            await service.get_or_404(tenant.id), {"coupons": True}, Actor.system("tests")
        )
        await session.commit()


def coupon_body(**overrides: Any) -> dict[str, Any]:
    return {"code": "BEMVINDO", "kind": "percent", "percent_bps": 1000} | overrides


async def _cart_with_brownies(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: Shop,
    units: str = "2",
) -> dict[str, Any]:
    tenant, owner, me = shop
    brownie = await product(client, session_factory, tenant, owner)
    variant = brownie["variants"][0]["id"]
    await set_stock(session_factory, variant, 10)
    return await ready_cart(client, me, variant, units)


# ----------------------------------------------------------------------------------- rules
def test_percent_rounds_half_up_and_respects_the_cap() -> None:
    assert percent_of(1001, 500) == 50  # 5% of R$ 10,01 is 50,05 centavos
    assert percent_of(1010, 500) == 51  # 50,5 centavos, arredondado para cima
    assert percent_of(1000, 1000) == 100

    coupon = Coupon(
        code="X",
        kind="percent",
        percent_bps=2000,
        min_subtotal_cents=0,
        redemptions_count=0,
        status="active",
        max_discount_cents=300,
    )
    assert (
        evaluate(coupon, subtotal_cents=5000, customer_uses=0, now=utcnow()).discount_cents == 300
    )
    coupon.max_discount_cents = None
    assert (
        evaluate(coupon, subtotal_cents=5000, customer_uses=0, now=utcnow()).discount_cents == 1000
    )


def test_a_fixed_coupon_never_gives_money_back() -> None:
    coupon = Coupon(
        code="X",
        kind="fixed",
        amount_cents=5000,
        min_subtotal_cents=0,
        redemptions_count=0,
        status="active",
    )
    outcome = evaluate(coupon, subtotal_cents=3000, customer_uses=0, now=utcnow())
    assert outcome.discount_cents == 3000  # the goods, never more


@pytest.mark.parametrize(
    ("changes", "problem"),
    [
        ({"status": "paused"}, "coupon_inactive"),
        ({"starts_at": datetime(2099, 1, 1)}, "coupon_not_started"),
        ({"ends_at": datetime(2020, 1, 1)}, "coupon_expired"),
        ({"min_subtotal_cents": 999_999}, "coupon_min_subtotal"),
        ({"max_redemptions": 1, "redemptions_count": 1}, "coupon_exhausted"),
    ],
)
def test_every_reason_a_coupon_cannot_be_used(changes: dict[str, Any], problem: str) -> None:
    from datetime import UTC

    base: dict[str, Any] = {
        "code": "X",
        "kind": "percent",
        "percent_bps": 1000,
        "min_subtotal_cents": 0,
        "redemptions_count": 0,
        "status": "active",
    }
    for key, value in changes.items():
        base[key] = value.replace(tzinfo=UTC) if isinstance(value, datetime) else value
    outcome = evaluate(Coupon(**base), subtotal_cents=3000, customer_uses=0, now=utcnow())
    assert (outcome.ok, outcome.problem) == (False, problem)
    assert evaluate(None, subtotal_cents=1, customer_uses=0, now=utcnow()).problem == (
        "coupon_not_found"
    )


# ----------------------------------------------------------------------------- through the API
async def test_the_customer_applies_a_coupon_and_pays_less(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], shop: Shop
) -> None:
    tenant, owner, me = shop
    await with_coupons(session_factory, tenant)
    created = await client.post(coupons_url(tenant), json=coupon_body(), headers=owner)
    assert created.status_code == 201, created.text
    await _cart_with_brownies(client, session_factory, shop)

    applied = await client.put(f"{CART}/coupon", json={"code": "bemvindo"}, headers=me)
    assert applied.status_code == 200, applied.text
    quote = applied.json()["quote"]
    assert (quote["subtotal_cents"], quote["discount_cents"], quote["total_cents"]) == (
        3000,
        300,
        2700,
    )
    assert quote["coupon"] == {"code": "BEMVINDO", "discount_cents": 300, "problem": None}

    cart = (await client.get(CART, headers=me)).json()
    placed = await place(client, me, order_body(cart))
    assert placed.status_code == 201, placed.text
    assert placed.json()["discount_cents"] == 300 and placed.json()["total_cents"] == 2700
    async with session_factory() as session:
        [redemption] = list(
            (await session.execute(select(CouponRedemption).execution_options(**CROSS))).scalars()
        )
        coupon = await session.scalar(select(Coupon).execution_options(**CROSS))
    assert (redemption.discount_cents, redemption.status) == (300, "active")
    assert coupon is not None and coupon.redemptions_count == 1


async def test_a_coupon_that_cannot_be_used_says_why(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], shop: Shop
) -> None:
    tenant, owner, me = shop
    await with_coupons(session_factory, tenant)
    await client.post(
        coupons_url(tenant), json=coupon_body(min_subtotal_cents=10000), headers=owner
    )
    await _cart_with_brownies(client, session_factory, shop)

    unknown = await client.put(f"{CART}/coupon", json={"code": "NAOEXISTE"}, headers=me)
    assert unknown.status_code == 404 and unknown.json()["error"]["code"] == "coupon_invalid"
    assert unknown.json()["error"]["details"]["problem"] == "coupon_not_found"

    too_small = await client.put(f"{CART}/coupon", json={"code": "BEMVINDO"}, headers=me)
    assert too_small.json()["error"]["details"]["problem"] == "coupon_min_subtotal"
    assert too_small.json()["error"]["details"]["limits"] == {"min_subtotal_cents": 10000}
    assert (await client.get(CART, headers=me)).json()["quote"]["discount_cents"] == 0


async def test_a_used_up_coupon_is_free_again_when_the_order_expires(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], shop: Shop
) -> None:
    tenant, owner, me = shop
    await with_coupons(session_factory, tenant)
    await client.post(
        coupons_url(tenant),
        json=coupon_body(max_redemptions=1, per_customer_limit=1),
        headers=owner,
    )
    cart = await _cart_with_brownies(client, session_factory, shop)
    await client.put(f"{CART}/coupon", json={"code": "BEMVINDO"}, headers=me)
    cart = (await client.get(CART, headers=me)).json()
    order = (await place(client, me, order_body(cart))).json()

    listed = await client.get(coupons_url(tenant), headers=owner)
    assert listed.json()["items"][0]["redemptions_count"] == 1
    # The same customer cannot use it again while that order holds it.
    await _cart_with_brownies(client, session_factory, shop, units="1")
    again = await client.put(f"{CART}/coupon", json={"code": "BEMVINDO"}, headers=me)
    assert again.json()["error"]["details"]["problem"] in (
        "coupon_exhausted",
        "coupon_customer_limit",
    )

    async with session_factory() as session:
        await session.execute(
            update(Order)
            .where(Order.id == order["id"])
            .values(expires_at=utcnow() - timedelta(minutes=1))
            .execution_options(synchronize_session=False, **CROSS)
        )
        await session.commit()
    assert await run_expire_orders(session_factory, utcnow()) == 1

    freed = await client.get(coupons_url(tenant), headers=owner)
    assert freed.json()["items"][0]["redemptions_count"] == 0
    ok = await client.put(f"{CART}/coupon", json={"code": "BEMVINDO"}, headers=me)
    assert ok.status_code == 200 and ok.json()["quote"]["discount_cents"] == 150


async def test_only_a_store_with_the_module_manages_coupons(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], shop: Shop
) -> None:
    tenant, owner, _ = shop
    off = await client.get(coupons_url(tenant), headers=owner)
    assert off.status_code == 403 and off.json()["error"]["code"] == "feature_disabled"

    await with_coupons(session_factory, tenant)
    created = await client.post(coupons_url(tenant), json=coupon_body(), headers=owner)
    coupon_id = created.json()["id"]
    twice = await client.post(coupons_url(tenant), json=coupon_body(code="bemvindo"), headers=owner)
    assert twice.status_code == 409 and twice.json()["error"]["code"] == "coupon_code_taken"

    bad = await client.post(
        coupons_url(tenant), json={"code": "SEMVALOR", "kind": "fixed"}, headers=owner
    )
    assert bad.status_code == 422

    paused = await client.patch(
        coupons_url(tenant, coupon_id), json={"status": "paused"}, headers=owner
    )
    assert paused.json()["status"] == "paused" and paused.json()["code"] == "BEMVINDO"
    empty = await client.patch(coupons_url(tenant, coupon_id), json={}, headers=owner)
    assert empty.status_code == 422
