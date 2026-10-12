"""Promoção que acaba quando o estoque zera (docs/14-pedidos-lunares.md §1): o que vier primeiro,
data ou estoque. O estoque que conta é o físico, e o carimbo vem do consumidor do outbox."""

# `shop` vem por nome do módulo do checkout (é assim que o pytest acha a fixture).
# ruff: noqa: F811
from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.audit import outbox
from app.audit.models import OutboxEvent
from app.catalog.models import Product
from app.catalog.pricing import promo_deadline
from app.catalog.promotions import SOLD_OUT_EVENT, crossed_to_zero, end_promotion_on_sellout
from app.core.config import settings
from app.payments.providers import fake
from app.tenancy.context import CROSS_TENANT_OPTION
from app.tenancy.models import Tenant
from tests.test_catalog import base
from tests.test_checkout_place import order_body, place, ready_cart, shop  # noqa: F401
from tests.test_inventory import adjust
from tests.test_pricing import product
from tests.test_storefront_catalog import set_stock

CROSS = {CROSS_TENANT_OPTION: True}
CARD = {"token": fake.APPROVE_TOKEN, "payment_method_id": "visa", "installments": 1}
Shop = tuple[Tenant, dict[str, str], dict[str, str]]


@pytest.fixture(autouse=True)
def fake_payments(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(settings, "payments_allowed_providers", "fake")
    monkeypatch.setattr(settings, "payments_fake_webhook_secret", SecretStr("whsec-" + "p" * 30))
    yield


@pytest.fixture(autouse=True)
def promo_consumer() -> Iterator[None]:
    """O consumidor desta etapa registrado de verdade. O registro é global e `test_outbox` o
    esvazia; sem isto, o resultado dependeria da ordem dos testes."""
    saved = dict(outbox.registry.consumers)
    outbox.registry.register("promo_sellout", (SOLD_OUT_EVENT,), end_promotion_on_sellout)
    yield
    outbox.registry.clear()
    outbox.registry.consumers.update(saved)


def test_o_que_vier_primeiro_data_ou_estoque() -> None:
    cedo = datetime(2026, 10, 12, 10, tzinfo=UTC)
    tarde = cedo + timedelta(days=3)
    assert promo_deadline(tarde, None) == tarde
    assert promo_deadline(None, cedo) == cedo
    assert promo_deadline(tarde, cedo) == cedo
    assert promo_deadline(cedo, tarde) == cedo
    assert promo_deadline(None, None) is None


def test_so_conta_quando_o_fisico_cruza_o_zero() -> None:
    assert crossed_to_zero(2000, 0)
    assert crossed_to_zero(500, -300)  # contagem que achou menos do que o sistema tinha
    assert not crossed_to_zero(0, 0)  # já estava zerado: não é uma nova venda do último
    assert not crossed_to_zero(3000, 1000)
    assert not crossed_to_zero(200, 100)  # sobra de produto por peso não é zero


async def relay(session_factory: async_sessionmaker[AsyncSession]) -> list[str]:
    """Roda o outbox até o fim; devolve os tipos de evento entregues."""
    async with session_factory() as session:
        dispatched = await outbox.relay_pending(session)
        await session.commit()
    for event_id, consumer in dispatched:
        async with session_factory() as session:
            await outbox.deliver(session, event_id, consumer)
            await session.commit()
    async with session_factory() as session:
        ids = [event_id for event_id, _ in dispatched]
        rows = await session.execute(
            select(OutboxEvent.event_type).where(OutboxEvent.id.in_(ids)).execution_options(**CROSS)
        )
        return [str(r) for r in rows.scalars()]


async def stamped(session_factory: async_sessionmaker[AsyncSession], product_id: str) -> Any:
    async with session_factory() as session:
        row = await session.scalar(
            select(Product).where(Product.id == product_id).execution_options(**CROSS)
        )
    assert row is not None
    return row.promo_sold_out_at


async def read(client: AsyncClient, tenant: Tenant, owner: dict[str, str], pid: str) -> Any:
    response = await client.get(f"{base(tenant)}/products/{pid}", headers=owner)
    assert response.status_code == 200, response.text
    return response.json()


async def test_a_opcao_so_existe_onde_pode_disparar(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], shop: Shop
) -> None:
    tenant, owner, _ = shop
    url = f"{base(tenant)}/products"
    sem_preco = await client.post(
        url,
        json={"name": "A", "base_price_cents": 1500, "promo_ends_on_sellout": True},
        headers=owner,
    )
    assert sem_preco.status_code == 422, sem_preco.text
    sem_estoque = await client.post(
        url,
        json={
            "name": "B",
            "base_price_cents": 1500,
            "promo_price_cents": 1000,
            "promo_ends_on_sellout": True,
            "stock_policy": "unlimited",
        },
        headers=owner,
    )
    assert sem_estoque.status_code == 422, sem_estoque.text

    criado = await client.post(
        url,
        json={
            "name": "C",
            "base_price_cents": 1500,
            "promo_price_cents": 1000,
            "promo_ends_on_sellout": True,
        },
        headers=owner,
    )
    assert criado.status_code == 201, criado.text
    corpo = criado.json()
    assert corpo["promo_ends_on_sellout"] is True and corpo["promo_sold_out_at"] is None
    # Tirar o controle de estoque de um produto com a opção ligada também é recusado.
    sem_controle = await client.patch(
        f"{url}/{corpo['id']}", json={"stock_policy": "untracked"}, headers=owner
    )
    assert sem_controle.status_code == 422, sem_controle.text
    nulo = await client.patch(
        f"{url}/{corpo['id']}", json={"promo_ends_on_sellout": None}, headers=owner
    )
    assert nulo.status_code == 422, nulo.text


async def test_ajuste_que_zera_o_estoque_encerra_e_repor_nao_ressuscita(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], shop: Shop
) -> None:
    tenant, owner, _ = shop
    item = await product(
        client,
        session_factory,
        tenant,
        owner,
        promo_price_cents=1000,
        promo_ends_on_sellout=True,
    )
    variant = item["variants"][0]["id"]
    await set_stock(session_factory, variant, 2)
    assert (await read(client, tenant, owner, item["id"]))["price"]["promo_active"] is True

    perda = await adjust(
        client,
        tenant,
        owner,
        kind="loss",
        reason="quebrou",
        lines=[{"variant_id": variant, "quantity": "2"}],
    )
    assert perda.status_code == 201, perda.text
    assert SOLD_OUT_EVENT in await relay(session_factory)
    assert await stamped(session_factory, item["id"]) is not None
    lido = await read(client, tenant, owner, item["id"])
    assert lido["price"]["promo_active"] is False
    assert lido["price"]["amount_cents"] == 1500
    assert lido["promo_sold_out_at"] is not None

    repos = await adjust(
        client, tenant, owner, kind="receipt", lines=[{"variant_id": variant, "quantity": "5"}]
    )
    assert repos.status_code == 201, repos.text
    await relay(session_factory)
    assert (await read(client, tenant, owner, item["id"]))["price"]["promo_active"] is False

    # Mexer na promoção rearma: é outra promoção, o estoque que zerou a anterior não vale.
    rearmada = await client.patch(
        f"{base(tenant)}/products/{item['id']}", json={"promo_price_cents": 900}, headers=owner
    )
    assert rearmada.status_code == 200, rearmada.text
    assert rearmada.json()["promo_sold_out_at"] is None
    assert rearmada.json()["price"] == {
        "amount_cents": 900,
        "compare_at_cents": 1500,
        "promo_active": True,
        "promo_ends_at": None,
    }


async def test_sem_a_opcao_a_promocao_segue_ate_a_data(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], shop: Shop
) -> None:
    tenant, owner, _ = shop
    item = await product(client, session_factory, tenant, owner, promo_price_cents=1000)
    variant = item["variants"][0]["id"]
    await set_stock(session_factory, variant, 1)
    contagem = await adjust(
        client, tenant, owner, kind="count", lines=[{"variant_id": variant, "quantity": "0"}]
    )
    assert contagem.status_code == 201, contagem.text
    await relay(session_factory)
    assert await stamped(session_factory, item["id"]) is None
    assert (await read(client, tenant, owner, item["id"]))["price"]["promo_active"] is True


async def test_o_pagamento_que_leva_o_ultimo_encerra_a_promocao(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], shop: Shop
) -> None:
    tenant, owner, me = shop
    saved = await client.put(
        f"/api/v1/admin/tenants/{tenant.id}/payments/providers/fake",
        json={"enabled": True, "is_default": True},
        headers=owner,
    )
    assert saved.status_code == 200, saved.text
    item = await product(
        client,
        session_factory,
        tenant,
        owner,
        promo_price_cents=1000,
        promo_ends_on_sellout=True,
    )
    variant = item["variants"][0]["id"]
    await set_stock(session_factory, variant, 2)
    cart = await ready_cart(client, me, variant, "2")
    assert cart["quote"]["subtotal_cents"] == 2000  # o carrinho cobra a promoção
    placed = await place(client, me, order_body(cart))
    assert placed.status_code == 201, placed.text

    # Reservado não é vendido: o Pix abandonado não pode matar a promoção.
    await relay(session_factory)
    assert await stamped(session_factory, item["id"]) is None

    paid = await client.post(
        f"/api/v1/checkout/orders/{placed.json()['id']}/payments",
        json={"provider": "fake", "method": "card", "card": CARD},
        headers=me | {"Idempotency-Key": str(uuid.uuid4())},
    )
    assert paid.status_code == 201, paid.text
    assert paid.json()["status"] == "approved"
    assert SOLD_OUT_EVENT in await relay(session_factory)
    assert await stamped(session_factory, item["id"]) is not None
