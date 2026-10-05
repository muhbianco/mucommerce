"""WhatsApp do cliente no checkout: o lojista precisa falar com ele sobre o pedido.

A loja escolhe no painel se é obrigatório (`checkout.require_whatsapp`); a SGPipas começa com ele
ligado (migration 0039).
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.identity.models import Customer
from app.orders.models import Order
from app.tenancy.context import bind_session_tenant
from app.tenancy.models import Tenant
from tests.shoppers import as_shopper, selling_store, signed_in
from tests.test_cart import FULFILLMENT
from tests.test_catalog import member_headers
from tests.test_checkout_place import order_body, place, ready_cart
from tests.test_pricing import product
from tests.test_storefront_catalog import set_stock


async def _loja(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    obrigatorio: bool,
) -> tuple[Tenant, dict[str, str], dict[str, str], str]:
    tenant = await selling_store(
        session_factory,
        flags={"delivery": True},
        settings={
            "fulfillment": FULFILLMENT,
            "checkout": {"require_whatsapp": obrigatorio},
        },
    )
    owner = await member_headers(client, session_factory, tenant)
    token = await signed_in(session_factory, tenant)
    me = as_shopper(tenant, token)
    brownie = await product(client, session_factory, tenant, owner)
    variant = brownie["variants"][0]["id"]
    await set_stock(session_factory, variant, 20)
    return tenant, owner, me, variant


async def _telefone_do_pedido(
    session_factory: async_sessionmaker[AsyncSession], tenant: Tenant, order_id: str
) -> Any:
    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        pedido = await session.get(Order, order_id)
        assert pedido is not None
        return (pedido.customer_snapshot or {}).get("phone")


async def test_opcional_aceita_sem_numero_e_recusa_numero_errado(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, _, me, variant = await _loja(client, session_factory, obrigatorio=False)

    errado = await place(
        client,
        me,
        order_body(await ready_cart(client, me, variant), contact={"name": "Ana", "phone": "123"}),
    )
    assert errado.status_code == 422, errado.text
    assert errado.json()["error"]["code"] == "whatsapp_invalid"

    sem = await place(client, me, order_body(await ready_cart(client, me, variant)))
    assert sem.status_code == 201, sem.text
    assert await _telefone_do_pedido(session_factory, tenant, sem.json()["id"]) is None


async def test_obrigatorio_pede_o_numero_e_guarda_normalizado(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, _, me, variant = await _loja(client, session_factory, obrigatorio=True)

    contexto = await client.get("/api/v1/storefront/context", headers={"host": "alpha.loja.test"})
    assert contexto.json()["checkout"] == {"require_whatsapp": True}

    sessao = (await client.get("/api/v1/me/session", headers=me)).json()
    assert sessao["customer"]["phone_masked"] is None

    sem = await place(client, me, order_body(await ready_cart(client, me, variant)))
    assert sem.status_code == 422, sem.text
    assert sem.json()["error"]["code"] == "whatsapp_required"

    feito = await place(
        client,
        me,
        order_body(
            await ready_cart(client, me, variant),
            contact={"name": "Ana", "phone": "(11) 99999-8888"},
        ),
    )
    assert feito.status_code == 201, feito.text
    assert await _telefone_do_pedido(session_factory, tenant, feito.json()["id"]) == "5511999998888"

    # A próxima compra já conhece o número (do último pedido): em branco, vale ele.
    sessao = (await client.get("/api/v1/me/session", headers=me)).json()
    assert sessao["customer"]["phone_masked"] == "+5511 •••• 8888"
    de_novo = await place(client, me, order_body(await ready_cart(client, me, variant)))
    assert de_novo.status_code == 201, de_novo.text
    assert (
        await _telefone_do_pedido(session_factory, tenant, de_novo.json()["id"]) == "5511999998888"
    )


async def test_obrigatorio_usa_o_whatsapp_da_conta(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, _, me, variant = await _loja(client, session_factory, obrigatorio=True)
    cliente_id = await _cliente_id(client, me)
    async with session_factory() as session:
        cliente = await session.get(Customer, cliente_id)
        assert cliente is not None
        cliente.phone_e164 = "5521988887777"
        await session.commit()

    feito = await place(client, me, order_body(await ready_cart(client, me, variant)))
    assert feito.status_code == 201, feito.text
    assert await _telefone_do_pedido(session_factory, tenant, feito.json()["id"]) == "5521988887777"


async def _cliente_id(client: AsyncClient, me: dict[str, str]) -> str:
    return str((await client.get("/api/v1/me/session", headers=me)).json()["customer"]["id"])
