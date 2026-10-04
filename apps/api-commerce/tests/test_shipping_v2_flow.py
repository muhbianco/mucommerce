"""Frete v2, F3a: do carrinho à etiqueta com o plano de volumes congelado.

O que custa dinheiro aqui: o preço que o cliente viu é o do plano cotado, o pedido guarda esse
plano, e a etiqueta sai com ele — mesmo que alguém mexa na medida do produto depois. Plano que
mudou entre a cotação e o pedido não passa (o cliente recota), e plano adulterado não confere.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.integrations.credentials import CredentialStore
from app.orders.models import Order
from app.shipping.providers import fake as fake_shipping
from app.tenancy.context import bind_session_tenant
from tests.shoppers import as_shopper, selling_store, signed_in
from tests.test_catalog import base, member_headers
from tests.test_checkout_place import order_body, place
from tests.test_pricing import product
from tests.test_shipping_flow import CART, ENDERECO, FULFILLMENT, _aceitar, _despachar
from tests.test_storefront_catalog import set_stock

PLANO = re.compile(r"^[0-9a-f]{32}:(single|multi_volume)$")
RABIOLA = {"weight_grams": 150, "width_mm": 100, "height_mm": 50, "depth_mm": 100}
CAIXA_P = {
    "name": "Caixa P",
    "inner_length_mm": 200,
    "inner_width_mm": 150,
    "inner_height_mm": 100,
    "empty_weight_grams": 80,
}
CAIXA_M = {
    "name": "Caixa M",
    "inner_length_mm": 300,
    "inner_width_mm": 200,
    "inner_height_mm": 150,
    "empty_weight_grams": 180,
}
#: Distância fingida do provedor fake entre 01001 (origem) e 20000 (destino), em centavos.
DISTANCIA = abs(20000 - 1001) // 10


@pytest.fixture(autouse=True)
def fake_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "shipping_allowed_providers", "fake")
    fake_shipping.reset()


async def loja(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    v2: bool = True,
    caixas: tuple[dict[str, Any], ...] = (CAIXA_P, CAIXA_M),
    packing: dict[str, Any] | None = None,
) -> tuple[Any, dict[str, str], dict[str, str]]:
    fulfillment = FULFILLMENT
    if packing:
        fulfillment = {**FULFILLMENT, "shipping": {**FULFILLMENT["shipping"], "packing": packing}}
    tenant = await selling_store(
        session_factory,
        flags={"pickup": True, "shipping.packing_v2": v2},
        settings={"fulfillment": fulfillment},
    )
    owner = await member_headers(client, session_factory, tenant)
    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        await CredentialStore(session, tenant.id).put("fake", "access_token", "token-de-teste")
        await session.commit()
    for caixa in caixas:
        criada = await client.post(f"{base(tenant)}/shipping/packages", json=caixa, headers=owner)
        assert criada.status_code == 201, criada.text
    me = as_shopper(tenant, await signed_in(session_factory, tenant))
    return tenant, owner, me


async def rabiolas(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    tenant: Any,
    owner: dict[str, str],
    me: dict[str, str],
    quantidade: int,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Produto medido → carrinho → endereço → cotação. Devolve (produto, cotação, endereço)."""
    criado = await product(client, session_factory, tenant, owner, name="Rabiola", **RABIOLA)
    variant_id = criado["variants"][0]["id"]
    await set_stock(session_factory, variant_id, 100)
    resposta = await client.post(
        f"{CART}/items", json={"variant_id": variant_id, "quantity": str(quantidade)}, headers=me
    )
    assert resposta.status_code in (200, 201), resposta.text
    endereco = (await client.post("/api/v1/me/addresses", json=ENDERECO, headers=me)).json()
    cotacao = await client.post(
        f"{CART}/shipping/options", json={"address_id": endereco["id"]}, headers=me
    )
    assert cotacao.status_code == 200, cotacao.text
    return criado, dict(cotacao.json()), endereco


async def escolher(
    client: AsyncClient, me: dict[str, str], endereco: dict[str, Any], opcao: dict[str, Any]
) -> dict[str, Any]:
    carrinho = await client.put(
        f"{CART}/fulfillment",
        json={"type": "shipping", "address_id": endereco["id"], "shipping": opcao},
        headers=me,
    )
    assert carrinho.status_code == 200, carrinho.text
    return dict(carrinho.json())


async def test_cotacao_assina_o_plano_e_cobra_o_volume_do_plano(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, owner, me = await loja(client, session_factory)
    _, cotacao, _ = await rabiolas(client, session_factory, tenant, owner, me, 4)
    assert cotacao["problem"] is None, cotacao
    por_servico = {o["service_code"]: o for o in cotacao["options"]}
    assert set(por_servico) == {"fake_economico", "fake_expresso"}
    for opcao in por_servico.values():
        assert PLANO.match(opcao["plan"]), opcao["plan"]
    economico = por_servico["fake_economico"]
    # 4 rabiolas numa Caixa P: 80 g de caixa + 600 g de produto = 680 g num volume só.
    assert economico["price_cents"] == 1500 + 680 * 2 + DISTANCIA
    assert economico["plan"].endswith(":single")
    assert (economico["delivery_min"], economico["delivery_max"]) == (6, 8)


async def test_pedido_congela_o_plano_e_a_etiqueta_sai_com_ele(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """O teste que importa: mudar a medida do produto depois do pedido não muda a etiqueta."""
    tenant, owner, me = await loja(client, session_factory)
    criado, cotacao, endereco = await rabiolas(client, session_factory, tenant, owner, me, 4)
    economico = next(o for o in cotacao["options"] if o["service_code"] == "fake_economico")
    carrinho = await escolher(client, me, endereco, economico)
    assert carrinho["quote"]["fulfillment"]["problems"] == []
    resposta = await place(client, me, order_body(carrinho))
    assert resposta.status_code == 201, resposta.text
    order_id = resposta.json()["id"]

    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        pedido = await session.get(Order, order_id)
        assert pedido is not None
        plano = (pedido.fulfillment or {})["parcel_plan"]
    assert plano["hash"] == economico["plan"].split(":")[0]
    assert plano["label_mode"] == "single"
    (volume,) = plano["parcels"]
    assert volume["package_name"] == "Caixa P"
    assert volume["weight_grams"] == 680
    assert volume["outer_mm"] == [208, 158, 108]  # de fora = de dentro + parede (4 mm por lado)
    assert [(i["name"], i["units"]) for i in volume["items"]] == [("Rabiola", 4)]

    # Depois do pedido, a loja muda a medida do produto: a etiqueta ignora e usa o plano.
    mudou = await client.patch(
        f"{base(tenant)}/products/{criado['id']}",
        json={"weight_grams": 900, "width_mm": 400},
        headers=owner,
    )
    assert mudou.status_code == 200, mudou.text
    await _aceitar(session_factory, tenant, order_id)
    remessa = await _despachar(session_factory, tenant, order_id)
    assert remessa.parcels == [
        {
            "weight_grams": 680,
            "width_mm": 158,
            "height_mm": 108,
            "depth_mm": 208,
            "value_cents": 4 * 1500,
        }
    ]


async def test_plano_que_mudou_entre_cotacao_e_pedido_obriga_a_recotar(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, owner, me = await loja(client, session_factory)
    criado, cotacao, endereco = await rabiolas(client, session_factory, tenant, owner, me, 4)
    carrinho = await escolher(client, me, endereco, cotacao["options"][0])
    await client.patch(
        f"{base(tenant)}/products/{criado['id']}", json={"weight_grams": 200}, headers=owner
    )
    resposta = await place(client, me, order_body(carrinho))
    assert resposta.status_code == 409, resposta.text
    erro = resposta.json()["error"]
    assert erro["code"] == "fulfillment_invalid"
    assert erro["details"]["problems"] == ["quote_expired"]


async def test_plano_adulterado_nao_confere(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, owner, me = await loja(client, session_factory)
    _, cotacao, endereco = await rabiolas(client, session_factory, tenant, owner, me, 4)
    opcao = dict(cotacao["options"][0])
    opcao["plan"] = "0" * 32 + ":single"
    carrinho = await escolher(client, me, endereco, opcao)
    assert carrinho["quote"]["fulfillment"]["problems"] == ["quote_invalid"]


async def test_varios_volumes_so_onde_a_etiqueta_aceita(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """8 rabiolas só com a Caixa P: 2 volumes. O Econômico (uma etiqueta por volume, como os
    Correios) some até a etiqueta por volume existir; o Expresso (multivolume) fica."""
    tenant, owner, me = await loja(client, session_factory, caixas=(CAIXA_P,))
    _, cotacao, _ = await rabiolas(client, session_factory, tenant, owner, me, 8)
    assert [o["service_code"] for o in cotacao["options"]] == ["fake_expresso"]
    expresso = cotacao["options"][0]
    assert expresso["plan"].endswith(":multi_volume")
    # Remessa inteira: 2 caixas (160 g) + 8 rabiolas (1200 g) = 1360 g, cobrada em dobro.
    assert expresso["price_cents"] == (1500 + 1360 * 2 + DISTANCIA) * 2


async def test_teto_de_volumes_da_loja(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, owner, me = await loja(
        client, session_factory, caixas=(CAIXA_P,), packing={"max_parcels": 1}
    )
    _, cotacao, _ = await rabiolas(client, session_factory, tenant, owner, me, 8)
    assert cotacao["problem"] == "too_many_parcels"


async def test_flag_desligada_segue_pelo_v1(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, owner, me = await loja(client, session_factory, v2=False)
    _, cotacao, _ = await rabiolas(client, session_factory, tenant, owner, me, 4)
    assert cotacao["problem"] is None
    assert all(o["plan"] == "" for o in cotacao["options"])


async def test_v2_sem_embalagem_ativa_cai_no_v1(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, owner, me = await loja(client, session_factory, caixas=())
    _, cotacao, _ = await rabiolas(client, session_factory, tenant, owner, me, 4)
    assert cotacao["problem"] is None
    assert all(o["plan"] == "" for o in cotacao["options"])
