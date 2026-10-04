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
from app.shipping.service import QuoteLine
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


async def test_a_tela_de_envio_aponta_o_produto_que_trava_a_cotacao(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """A loja do Silvio: tudo configurado, tudo verde, e o frete não aparecia no carrinho.

    O que faltava era peso e medida no produto — dado que o lojista não tinha como adivinhar
    que faltava, porque nada na tela falava dele.
    """
    tenant, owner, _ = await loja(client, session_factory)
    base = f"/api/v1/admin/tenants/{tenant.id}"

    criado = await product(client, session_factory, tenant, owner, name="Rabiola sem medida")
    produto_id = criado["id"]

    status = await client.get(f"{base}/shipping", headers=owner)
    assert status.status_code == 200, status.text
    assert [p["name"] for p in status.json()["unmeasured"]] == ["Rabiola sem medida"]

    # Preenchido, some da lista: a tela só cobra o que ainda falta.
    await client.patch(
        f"{base}/products/{produto_id}",
        json={"weight_grams": 900, "width_mm": 200, "height_mm": 100, "depth_mm": 100},
        headers=owner,
    )
    depois = await client.get(f"{base}/shipping", headers=owner)
    assert depois.json()["unmeasured"] == []


def test_o_motivo_de_nao_caber_sai_em_metro_e_quilo() -> None:
    """5000 nos quatro campos: o peso fica certo (5 kg) e a caixa vira um cubo de 5 metros.

    Foi o cadastro real que derrubou o frete da loja. Dizer "5000 mm" de volta não ajuda quem
    digitou 5000 sem perceber a unidade; dizer "5,00 m" ajuda.
    """
    from app.api.v1.endpoints.admin_shipping import _oversize_detail

    assert _oversize_detail(5000, 5000, 5000, 5000) == (
        "largura de 5,00 m, altura de 5,00 m, profundidade de 5,00 m"
    )
    # Cada lado cabe, a soma não.
    assert _oversize_detail(1000, 900, 900, 900) == "os tres lados somam 2,70 m"
    # Medida boa, peso acima do que os Correios levam.
    assert _oversize_detail(45_000, 300, 200, 100) == "45,0 kg"
    # Caixa de sapato: nada a dizer.
    assert _oversize_detail(900, 300, 200, 100) is None


async def test_a_tela_de_envio_separa_sem_medida_de_medida_grande_demais(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, owner, _ = await loja(client, session_factory)
    base = f"/api/v1/admin/tenants/{tenant.id}"

    sem = await product(client, session_factory, tenant, owner, name="Sem medida")
    grande = await product(
        client,
        session_factory,
        tenant,
        owner,
        name="Cubo de cinco metros",
        weight_grams=5000,
        width_mm=5000,
        height_mm=5000,
        depth_mm=5000,
    )

    status = (await client.get(f"{base}/shipping", headers=owner)).json()
    assert [p["name"] for p in status["unmeasured"]] == [sem["name"]]
    assert [(p["name"], p["detail"]) for p in status["oversized"]] == [
        (grande["name"], "largura de 5,00 m, altura de 5,00 m, profundidade de 5,00 m")
    ]


async def test_caixa_padrao_grande_demais_aparece_na_tela(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """A caixa entra em toda cotação: grande demais, ela derruba o frete da loja inteira.

    Foi o segundo tropeço da mesma unidade na loja do Silvio — corrigidas as medidas do
    produto, a caixa continuou com 2 m por 1,5 m por 1 m e 10 kg de tara, e nada dizia isso.
    """
    tenant, owner, _ = await loja(client, session_factory)
    base = f"/api/v1/admin/tenants/{tenant.id}"

    # A caixa do `loja()` é de tamanho normal: nada a avisar.
    assert (await client.get(f"{base}/shipping", headers=owner)).json()["box_oversize"] is None

    atual = (await client.get(f"{base}/settings", headers=owner)).json()["fulfillment"]
    envio = dict(atual["shipping"])
    envio["box"] = {
        "width_mm": 2000,
        "height_mm": 1500,
        "depth_mm": 1000,
        "max_weight_grams": 30000,
        "empty_weight_grams": 10000,
    }
    salvo = await client.put(
        f"{base}/settings/fulfillment",
        json={"value": {**atual, "shipping": envio}},
        headers=owner,
    )
    assert salvo.status_code == 200, salvo.text

    status = (await client.get(f"{base}/shipping", headers=owner)).json()
    assert status["box_oversize"] == "largura de 2,00 m, altura de 1,50 m"


async def test_o_produto_viaja_na_embalagem_que_a_loja_escolheu(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Mais de uma embalagem cadastrada, e o produto aponta para a dele.

    É o caso da rabiola: a caixa padrão da loja é pequena, e um produto grande que apontasse
    para ela viraria um volume por unidade — dez unidades, dez fretes. Com a embalagem certa
    atrelada, as unidades enchem a caixa e só abre outra quando acaba o espaço.
    """
    from app.shipping.service import parcels_for
    from app.tenancy.settings_schemas import fulfillment_settings

    grande = {
        "name": "Caixa grande",
        "width_mm": 600,
        "height_mm": 400,
        "depth_mm": 400,
        "max_weight_grams": 30_000,
    }
    com_caixas = {
        **FULFILLMENT,
        "shipping": {**FULFILLMENT["shipping"], "boxes": [grande]},
    }
    tenant = await selling_store(
        session_factory, flags={"pickup": True}, settings={"fulfillment": com_caixas}
    )
    owner = await member_headers(client, session_factory, tenant)

    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        context = await TenantResolver(session).resolve_by_id(tenant.id)
        cfg = fulfillment_settings(context.settings).shipping
        box_id = cfg.boxes[0].id
    assert box_id, "o normalizador atribui o id da embalagem na escrita"

    # 250x200x200 mm: nao cabe na caixa padrao (300x200x200, por um lado so), cabe na grande.
    criado = await product(
        client,
        session_factory,
        tenant,
        owner,
        weight_grams=1000,
        width_mm=250,
        height_mm=200,
        depth_mm=200,
        shipping_box_id=box_id,
    )
    variant_id = criado["variants"][0]["id"]

    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        volumes = await parcels_for(session, [QuoteLine(variant_id, 4000)], cfg)

    # Quatro unidades numa caixa grande só, com as medidas dela — não quatro volumes soltos.
    assert len(volumes) == 1
    assert (volumes[0].width_mm, volumes[0].height_mm) == (600, 400)
    assert volumes[0].weight_grams == 4000


async def test_embalagem_apagada_cai_na_caixa_padrao_em_vez_de_recusar(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """A loja apagou a embalagem depois de o produto apontar para ela.

    Recusar a cotação puniria o cliente por uma edição da loja; cair na caixa padrão é o pior
    que pode acontecer e ainda é uma venda.
    """
    from app.catalog.models import Product
    from app.shipping.service import parcels_for
    from app.tenancy.settings_schemas import fulfillment_settings

    tenant, owner, _ = await loja(client, session_factory)
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
    # A API não aceita mais apontar para embalagem inexistente; aqui ela "sumiu" depois.
    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        linha = await session.get(Product, criado["id"])
        assert linha is not None
        linha.shipping_box_id = "01a00000-0000-7000-8000-000000000000"
        await session.commit()

    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        context = await TenantResolver(session).resolve_by_id(tenant.id)
        cfg = fulfillment_settings(context.settings).shipping
        volumes = await parcels_for(session, [QuoteLine(variant_id, 2000)], cfg)

    assert len(volumes) == 1
    assert (volumes[0].width_mm, volumes[0].height_mm) == (300, 200)


async def test_linha_que_nao_viaja_nao_trava_o_frete(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Um serviço sem medida no mesmo carrinho de um produto medido.

    Antes o serviço entrava no empacotador e a cotação inteira voltava `missing_dimensions` —
    por causa de uma linha que nem vai na caixa.
    """
    from app.shipping.service import parcels_for
    from app.tenancy.settings_schemas import fulfillment_settings

    tenant, owner, _ = await loja(client, session_factory)
    medido = await product(
        client,
        session_factory,
        tenant,
        owner,
        weight_grams=800,
        width_mm=150,
        height_mm=100,
        depth_mm=80,
    )
    servico = await product(client, session_factory, tenant, owner, name="Montagem", kind="service")

    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        context = await TenantResolver(session).resolve_by_id(tenant.id)
        cfg = fulfillment_settings(context.settings).shipping
        volumes = await parcels_for(
            session,
            [
                QuoteLine(medido["variants"][0]["id"], 1000),
                QuoteLine(servico["variants"][0]["id"], 1000),
            ],
            cfg,
        )

    assert len(volumes) == 1
    assert volumes[0].weight_grams == 800


async def test_quantidade_fracionada_arredonda_para_cima(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """2,5 unidades viram 3 peças na conta do frete, nunca 2 (o `round` bancário dava 2)."""
    from app.shipping.service import parcels_for
    from app.tenancy.settings_schemas import fulfillment_settings

    tenant, owner, _ = await loja(client, session_factory)
    criado = await product(
        client,
        session_factory,
        tenant,
        owner,
        weight_grams=1000,
        width_mm=100,
        height_mm=100,
        depth_mm=100,
    )
    variant_id = criado["variants"][0]["id"]

    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        context = await TenantResolver(session).resolve_by_id(tenant.id)
        cfg = fulfillment_settings(context.settings).shipping
        volumes = await parcels_for(session, [QuoteLine(variant_id, 2500)], cfg)

    assert sum(v.weight_grams for v in volumes) == 3000
