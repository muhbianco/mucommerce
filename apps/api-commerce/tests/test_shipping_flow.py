"""Do carrinho à etiqueta (etapa J): cotar, escolher, comprar o frete e fechar pelo rastreio.

Usa o provedor `fake`, que responde preço por peso e distância e move o rastreio por comando.
O que importa aqui é o que custa dinheiro: não comprar duas etiquetas, não mentir sobre o
estado do pedido, e fechar sozinho quando a transportadora diz que entregou.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.integrations.credentials import CredentialStore
from app.models.base import utcnow
from app.orders.models import Order
from app.orders.state_machine import OrderStatus
from app.shipping.dispatch import ShipmentService
from app.shipping.jobs import run_track_shipments
from app.shipping.models import ShipmentStatus
from app.shipping.providers import fake as fake_shipping
from app.tenancy.context import bind_session_tenant
from app.tenancy.resolver import TenantResolver
from app.tenancy.service import Actor
from tests.shoppers import as_shopper, selling_store, signed_in
from tests.test_catalog import member_headers
from tests.test_checkout_place import order_body, place
from tests.test_pricing import product
from tests.test_storefront_catalog import set_stock

CART = "/api/v1/cart"
ORIGEM = {
    "name": "Loja Teste",
    "postal_code": "01001000",
    "address": "Praça da Sé",
    "number": "1",
    "district": "Sé",
    "city": "São Paulo",
    "state": "SP",
    "document": "46867029000176",
}
FULFILLMENT: dict[str, Any] = {
    "pickup": {"enabled": True, "locations": [{"name": "Balcão", "address": "Rua A, 1"}]},
    "shipping": {
        "enabled": True,
        "provider": "fake",
        "origin": ORIGEM,
        "box": {"width_mm": 300, "height_mm": 200, "depth_mm": 200, "max_weight_grams": 10000},
    },
}
ENDERECO = {
    "recipient_name": "Maria",
    "postal_code": "20000000",
    "street": "Rua do Destino",
    "number": "100",
    "district": "Centro",
    "city": "Rio de Janeiro",
    "state": "RJ",
}


@pytest.fixture(autouse=True)
def fake_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "shipping_allowed_providers", "fake")
    fake_shipping.reset()


async def loja(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> tuple[Any, dict[str, str], dict[str, str]]:
    tenant = await selling_store(
        session_factory,
        flags={"pickup": True},
        settings={"fulfillment": FULFILLMENT},
    )
    owner = await member_headers(client, session_factory, tenant)
    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        await CredentialStore(session, tenant.id).put("fake", "access_token", "token-de-teste")
        await session.commit()
    me = as_shopper(tenant, await signed_in(session_factory, tenant))
    return tenant, owner, me


async def carrinho_com_frete(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    tenant: Any,
    owner: dict[str, str],
    me: dict[str, str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Produto com medida → item no carrinho → endereço → cotação → escolha."""
    criado = await product(
        client,
        session_factory,
        tenant,
        owner,
        weight_grams=800,
        width_mm=150,
        height_mm=100,
        depth_mm=80,
    )
    variant_id = criado["variants"][0]["id"]
    await set_stock(session_factory, variant_id, 10)
    await client.post(f"{CART}/items", json={"variant_id": variant_id, "quantity": "1"}, headers=me)
    endereco = (await client.post("/api/v1/me/addresses", json=ENDERECO, headers=me)).json()
    cotacao = await client.post(
        f"{CART}/shipping/options", json={"address_id": endereco["id"]}, headers=me
    )
    assert cotacao.status_code == 200, cotacao.text
    return dict(cotacao.json()), endereco


async def pedido_pronto_para_despacho(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    tenant: Any,
    owner: dict[str, str],
    me: dict[str, str],
) -> str:
    opcoes, endereco = await carrinho_com_frete(client, session_factory, tenant, owner, me)
    assert opcoes["problem"] is None, opcoes
    escolhida = opcoes["options"][0]
    carrinho = (
        await client.put(
            f"{CART}/fulfillment",
            json={"type": "shipping", "address_id": endereco["id"], "shipping": escolhida},
            headers=me,
        )
    ).json()
    assert carrinho["quote"]["fulfillment"]["problems"] == [], carrinho["quote"]["fulfillment"]
    assert carrinho["quote"]["fulfillment"]["fee_cents"] == escolhida["price_cents"]
    resposta = await place(client, me, order_body(carrinho))
    assert resposta.status_code == 201, resposta.text
    order_id = resposta.json()["id"]
    await _aceitar(session_factory, tenant, order_id)
    return str(order_id)


async def _aceitar(
    session_factory: async_sessionmaker[AsyncSession], tenant: Any, order_id: str
) -> None:
    """Leva o pedido até `accepted` sem passar pelo pagamento (não é o que se testa aqui)."""
    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        pedido = await session.get(Order, order_id)
        assert pedido is not None
        pedido.status = OrderStatus.ACCEPTED
        pedido.paid_at = utcnow()
        await session.commit()


async def _despachar(
    session_factory: async_sessionmaker[AsyncSession], tenant: Any, order_id: str
) -> Any:
    async with session_factory() as session:
        contexto = await TenantResolver(session).resolve_by_id(tenant.id)
        bind_session_tenant(session, tenant.id)
        pedido = await session.get(Order, order_id)
        assert pedido is not None
        service = ShipmentService(session, contexto, Actor.system("teste"), utcnow())
        remessa = await service.dispatch(pedido, scopes=frozenset({"orders:transition"}))
        await session.commit()
        return remessa


async def test_cotacao_aparece_com_preco_e_prazo(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, owner, me = await loja(client, session_factory)
    opcoes, _ = await carrinho_com_frete(client, session_factory, tenant, owner, me)
    assert opcoes["problem"] is None
    assert len(opcoes["options"]) == 2
    primeira = opcoes["options"][0]
    assert primeira["price_cents"] > 0
    assert primeira["delivery_days"] == 8
    assert primeira["signature"]  # vai assinada: o preço volta do navegador no place


async def test_produto_sem_medida_nao_cota_e_diz_qual(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, owner, me = await loja(client, session_factory)
    criado = await product(client, session_factory, tenant, owner)  # sem peso nem dimensão
    variant_id = criado["variants"][0]["id"]
    await set_stock(session_factory, variant_id, 5)
    await client.post(f"{CART}/items", json={"variant_id": variant_id, "quantity": "1"}, headers=me)
    endereco = (await client.post("/api/v1/me/addresses", json=ENDERECO, headers=me)).json()
    resposta = await client.post(
        f"{CART}/shipping/options", json={"address_id": endereco["id"]}, headers=me
    )
    assert resposta.json()["problem"] == "missing_dimensions"
    assert resposta.json()["options"] == []


async def test_despacho_compra_etiqueta_e_envia_o_pedido(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, owner, me = await loja(client, session_factory)
    order_id = await pedido_pronto_para_despacho(client, session_factory, tenant, owner, me)
    remessa = await _despachar(session_factory, tenant, order_id)

    assert remessa.status == ShipmentStatus.PURCHASED
    assert remessa.tracking_code and remessa.tracking_code.startswith("FK")
    assert remessa.label_url
    assert remessa.cost_cents and remessa.cost_cents > 0
    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        pedido = await session.get(Order, order_id)
        assert pedido is not None
        assert pedido.status == OrderStatus.SHIPPED
        assert pedido.shipped_at is not None


async def test_clique_duplo_nao_compra_duas_etiquetas(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, owner, me = await loja(client, session_factory)
    order_id = await pedido_pronto_para_despacho(client, session_factory, tenant, owner, me)
    primeira = await _despachar(session_factory, tenant, order_id)
    segunda = await _despachar(session_factory, tenant, order_id)
    assert primeira.id == segunda.id
    assert primeira.provider_shipment_id == segunda.provider_shipment_id


async def test_rastreio_entregue_fecha_o_pedido(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, owner, me = await loja(client, session_factory)
    order_id = await pedido_pronto_para_despacho(client, session_factory, tenant, owner, me)
    remessa = await _despachar(session_factory, tenant, order_id)
    fake_shipping.advance(remessa.provider_shipment_id, "delivered")

    mudaram = await run_track_shipments(session_factory, utcnow())
    assert mudaram == 1
    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        pedido = await session.get(Order, order_id)
        assert pedido is not None
        assert pedido.status == OrderStatus.DELIVERED
        assert pedido.delivered_at is not None
