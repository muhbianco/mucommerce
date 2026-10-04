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


async def test_plano_congelado_aparece_no_painel_e_nao_para_o_cliente(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """O plano de volumes traz nome de embalagem, custo de material e o hash da cotação: é da
    loja. O cliente vê o pedido sem ele; o painel, com ele (o "Como embalar" sai dali)."""
    tenant, owner, me = await loja(client, session_factory)
    _, cotacao, endereco = await rabiolas(client, session_factory, tenant, owner, me, 4)
    carrinho = await escolher(client, me, endereco, cotacao["options"][0])
    resposta = await place(client, me, order_body(carrinho))
    assert resposta.status_code == 201, resposta.text
    assert "parcel_plan" not in (resposta.json()["fulfillment"] or {})
    order_id = resposta.json()["id"]

    do_cliente = await client.get(f"/api/v1/me/orders/{order_id}", headers=me)
    assert do_cliente.status_code == 200, do_cliente.text
    assert "parcel_plan" not in (do_cliente.json()["fulfillment"] or {})
    assert do_cliente.json()["fulfillment"]["service_code"]

    do_painel = await client.get(f"{base(tenant)}/orders/{order_id}", headers=owner)
    assert do_painel.json()["order"]["fulfillment"]["parcel_plan"]["parcels"][0]["n"] == 1


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


async def test_varios_volumes_cada_servico_com_o_seu_modo_de_etiqueta(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """8 rabiolas só com a Caixa P: 2 volumes (6 + 2). O Econômico (como os Correios) compra uma
    etiqueta por volume e cobra a soma; o Expresso (multivolume) cobra a remessa numa só."""
    tenant, owner, me = await loja(client, session_factory, caixas=(CAIXA_P,))
    _, cotacao, _ = await rabiolas(client, session_factory, tenant, owner, me, 8)
    por_servico = {o["service_code"]: o for o in cotacao["options"]}
    economico, expresso = por_servico["fake_economico"], por_servico["fake_expresso"]
    assert economico["plan"].endswith(":per_volume")
    # Volume 1: 80 + 6 x 150 = 980 g; volume 2: 80 + 2 x 150 = 380 g.
    assert economico["price_cents"] == (1500 + 980 * 2 + DISTANCIA) + (1500 + 380 * 2 + DISTANCIA)
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


# ----------------------------------------------------------------------------- F3b

#: Caixa M grande demais para a cubagem grátis dos Correios (408 x 308 x 268 mm por fora > 30 L)
#: e pesada (300 g): a estratégia `consolidate` usa ela (1 volume), a `cubic_free` usa 2 Caixas P.
CAIXA_M_PESADA = {
    "name": "Caixa M",
    "inner_length_mm": 400,
    "inner_width_mm": 300,
    "inner_height_mm": 260,
    "empty_weight_grams": 300,
}


async def test_cada_servico_fica_com_a_sua_combinacao_mais_barata(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """8 rabiolas: 1 Caixa M (1500 g) ou 2 Caixas P (160 + 1200 = 1360 g). O Econômico cobra
    por volume e não junta volumes → fica com a M. O Expresso cobra a remessa pelo peso → fica
    com as 2 P, numa etiqueta só. Duas combinações, duas chamadas, cada serviço com a sua."""
    tenant, owner, me = await loja(client, session_factory, caixas=(CAIXA_P, CAIXA_M_PESADA))
    _, cotacao, _ = await rabiolas(client, session_factory, tenant, owner, me, 8)
    assert cotacao["problem"] is None, cotacao
    por_servico = {o["service_code"]: o for o in cotacao["options"]}
    economico, expresso = por_servico["fake_economico"], por_servico["fake_expresso"]
    assert economico["plan"].endswith(":single")
    assert economico["price_cents"] == 1500 + 1500 * 2 + DISTANCIA
    assert expresso["plan"].endswith(":multi_volume")
    assert expresso["price_cents"] == (1500 + 1360 * 2 + DISTANCIA) * 2
    assert economico["plan"].split(":")[0] != expresso["plan"].split(":")[0]
    assert len(fake_shipping.CALLS) == 2, "uma chamada por combinação distinta"


async def test_pedido_com_a_combinacao_de_outro_servico_congela_a_dele(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, owner, me = await loja(client, session_factory, caixas=(CAIXA_P, CAIXA_M_PESADA))
    _, cotacao, endereco = await rabiolas(client, session_factory, tenant, owner, me, 8)
    expresso = next(o for o in cotacao["options"] if o["service_code"] == "fake_expresso")
    carrinho = await escolher(client, me, endereco, expresso)
    resposta = await place(client, me, order_body(carrinho))
    assert resposta.status_code == 201, resposta.text
    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        pedido = await session.get(Order, resposta.json()["id"])
        assert pedido is not None
        plano = (pedido.fulfillment or {})["parcel_plan"]
    assert plano["label_mode"] == "multi_volume"
    assert [v["package_name"] for v in plano["parcels"]] == ["Caixa P", "Caixa P"]


def test_escolha_por_servico_tolera_combinacao_que_falhou() -> None:
    from app.shipping.packing.model import Dims, ParcelPlan, PlannedParcel
    from app.shipping.provider import ShippingOption
    from app.shipping.service import choose_offers

    def plano(nome: str, volumes: int, material: int = 0) -> ParcelPlan:
        parcela = PlannedParcel(
            package_id="p",
            package_name="P",
            kind="box",
            outer=Dims(200, 150, 100),
            inner=None,
            gross_g=500,
            tare_g=0,
            value_cents=0,
            material_cents=material,
            contents=(("v", 1),),
        )
        return ParcelPlan(nome, (parcela,) * volumes, False, nome * 4)

    def opcao(servico: str, preco: int, multi: int = 1) -> ShippingOption:
        return ShippingOption(servico, servico, "X", preco, 3, multi_volume_max=multi)

    um, dois = plano("aaaaaaaa", 1, material=300), plano("bbbbbbbb", 2)
    escolha = choose_offers(
        [um, dois, plano("cccccccc", 1)],
        [(opcao("pac", 2000),), (opcao("pac", 1500), opcao("jad", 1600, multi=5)), None],
        charge_material=True,
    )
    assert escolha.failed is True, "a terceira combinação falhou e mesmo assim há ofertas"
    por_servico = {o.option.service_code: o for o in escolha.offers}
    # PAC: a combinação de 2 volumes sai mais barata (1500 contra 2000 + 300 de material) e é
    # comprada com uma etiqueta por volume.
    assert por_servico["pac"].plan is dois and por_servico["pac"].mode == "per_volume"
    assert por_servico["pac"].charged_material_cents == 0
    assert por_servico["jad"].plan is dois and por_servico["jad"].mode == "multi_volume"

    vazia = choose_offers([um], [None], charge_material=False)
    assert (vazia.offers, vazia.failed) == ((), True)


async def test_simulador_com_cep_mostra_o_preco_real_e_a_vencedora_de_cada_servico(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, owner, me = await loja(client, session_factory, caixas=(CAIXA_P, CAIXA_M_PESADA))
    criado, _, _ = await rabiolas(client, session_factory, tenant, owner, me, 8)
    resposta = await client.post(
        f"{base(tenant)}/shipping/simulate",
        json={
            "lines": [{"variant_id": criado["variants"][0]["id"], "quantity_milli": 8000}],
            "postal_code": "20000-000",
        },
        headers=owner,
    )
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["quote_problem"] is None
    vencedoras = {
        (q["service_code"], tuple(v["package_name"] for v in plano["parcels"]))
        for plano in corpo["plans"]
        for q in plano["quotes"]
        if q["best"]
    }
    assert vencedoras == {
        ("fake_economico", ("Caixa M",)),
        ("fake_expresso", ("Caixa P", "Caixa P")),
    }
    duas_p = next(p for p in corpo["plans"] if len(p["parcels"]) == 2)
    economico_em_duas = next(q for q in duas_p["quotes"] if q["service_code"] == "fake_economico")
    assert economico_em_duas["mode"] == "per_volume"
    assert economico_em_duas["error"] is None
    assert economico_em_duas["price_cents"] > 0


# ----------------------------------------------------------------------------- F7


async def _pedido_por_volume(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> tuple[Any, dict[str, str], str]:
    """8 rabiolas, só a Caixa P, Econômico (uma etiqueta por volume): 2 etiquetas a comprar."""
    tenant, owner, me = await loja(client, session_factory, caixas=(CAIXA_P,))
    _, cotacao, endereco = await rabiolas(client, session_factory, tenant, owner, me, 8)
    economico = next(o for o in cotacao["options"] if o["service_code"] == "fake_economico")
    assert economico["plan"].endswith(":per_volume")
    carrinho = await escolher(client, me, endereco, economico)
    resposta = await place(client, me, order_body(carrinho))
    assert resposta.status_code == 201, resposta.text
    order_id = str(resposta.json()["id"])
    await _aceitar(session_factory, tenant, order_id)
    return tenant, owner, order_id


async def test_correios_compra_uma_etiqueta_por_volume(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, owner, order_id = await _pedido_por_volume(client, session_factory)
    remessa = await _despachar(session_factory, tenant, order_id)
    assert remessa.status == "purchased"
    assert [f"{remessa.id}.1", f"{remessa.id}.2"] == fake_shipping.SHIP_CALLS
    assert all(v["provider_shipment_id"] and v["label_url"] for v in remessa.parcels)
    assert remessa.cost_cents == sum(v["cost_cents"] for v in remessa.parcels)
    assert remessa.label_url == remessa.parcels[0]["label_url"]
    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        pedido = await session.get(Order, order_id)
        assert pedido is not None
        assert pedido.status == "shipped"

    lido = await client.get(f"{base(tenant)}/orders/{order_id}/shipment", headers=owner)
    assert [p["n"] for p in lido.json()["parcels"]] == [1, 2]
    assert all(p["label_url"] for p in lido.json()["parcels"])


async def test_falha_no_segundo_volume_nao_recompra_o_primeiro(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """O 1º volume foi pago e o 2º falhou: a 1ª etiqueta fica gravada (o erro desfaz a transação
    do pedido, não a compra) e a nova tentativa compra só o 2º."""
    from sqlalchemy import select

    from app.core.exceptions import ShippingUnavailableError
    from app.shipping.models import OrderShipment

    tenant, _owner, order_id = await _pedido_por_volume(client, session_factory)
    fake_shipping.FAIL_SUFFIXES.add(".2")
    with pytest.raises(ShippingUnavailableError):
        await _despachar(session_factory, tenant, order_id)
    fake_shipping.FAIL_SUFFIXES.clear()
    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        remessa = (
            await session.execute(select(OrderShipment).where(OrderShipment.order_id == order_id))
        ).scalar_one()
        remessa_id = remessa.id
        assert remessa.status == "failed"
        assert remessa.parcels[0]["provider_shipment_id"], "a etiqueta paga ficou gravada"
        assert not remessa.parcels[1].get("provider_shipment_id")
        assert "volume 2 de 2" in (remessa.last_error or "")

    fake_shipping.SHIP_CALLS.clear()
    remessa = await _despachar(session_factory, tenant, order_id)
    assert remessa.id == remessa_id
    assert remessa.status == "purchased"
    assert [f"{remessa.id}.2"] == fake_shipping.SHIP_CALLS, "só o volume que faltava"


async def test_despacho_por_volume_interrompido_so_retoma_depois_de_parado(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """O processo caiu no meio (linha em `creating` com a 1ª etiqueta gravada): enquanto ela é
    recente pode ser outra chamada comprando, e nada acontece; parada há mais de 10 minutos, a
    retomada compra só o volume que falta."""
    from datetime import timedelta

    from sqlalchemy import select

    from app.core.exceptions import ShipmentInProgressError, ShippingUnavailableError
    from app.models.base import utcnow
    from app.shipping.models import OrderShipment, ShipmentStatus

    tenant, _owner, order_id = await _pedido_por_volume(client, session_factory)
    fake_shipping.FAIL_SUFFIXES.add(".2")
    with pytest.raises(ShippingUnavailableError):
        await _despachar(session_factory, tenant, order_id)
    fake_shipping.FAIL_SUFFIXES.clear()

    async def parada_ha(minutos: int) -> None:
        async with session_factory() as session:
            bind_session_tenant(session, tenant.id)
            remessa = (
                await session.execute(
                    select(OrderShipment).where(OrderShipment.order_id == order_id)
                )
            ).scalar_one()
            remessa.status = ShipmentStatus.CREATING
            remessa.updated_at = utcnow() - timedelta(minutes=minutos)
            await session.commit()

    await parada_ha(5)
    fake_shipping.SHIP_CALLS.clear()
    with pytest.raises(ShipmentInProgressError):
        await _despachar(session_factory, tenant, order_id)
    assert fake_shipping.SHIP_CALLS == []

    await parada_ha(15)
    remessa = await _despachar(session_factory, tenant, order_id)
    assert remessa.status == "purchased"
    assert [f"{remessa.id}.2"] == fake_shipping.SHIP_CALLS


async def test_rastreio_por_volume_so_entrega_quando_todos_chegam(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    from datetime import timedelta

    from app.models.base import utcnow
    from app.shipping.jobs import run_track_shipments

    tenant, _owner, order_id = await _pedido_por_volume(client, session_factory)
    remessa = await _despachar(session_factory, tenant, order_id)
    primeiro, segundo = (v["provider_shipment_id"] for v in remessa.parcels)

    fake_shipping.advance(primeiro, "delivered")
    fake_shipping.advance(segundo, "in_transit")
    await run_track_shipments(session_factory, utcnow())
    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        pedido = await session.get(Order, order_id)
        assert pedido is not None
        assert pedido.status == "shipped", "falta um volume chegar"

    fake_shipping.advance(segundo, "delivered")
    await run_track_shipments(session_factory, utcnow() + timedelta(hours=5))
    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        pedido = await session.get(Order, order_id)
        assert pedido is not None
        assert pedido.status == "delivered"


def test_estado_da_remessa_com_varias_etiquetas() -> None:
    from app.shipping.jobs import combine_statuses
    from app.shipping.models import ShipmentStatus as S

    assert combine_statuses([S.DELIVERED, S.IN_TRANSIT]) == S.IN_TRANSIT
    assert combine_statuses([S.DELIVERED, S.DELIVERED]) == S.DELIVERED
    assert combine_statuses([S.POSTED, S.RETURNED]) == S.RETURNED
    assert combine_statuses([S.PURCHASED, S.POSTED]) == S.PURCHASED


async def test_previa_do_custo_da_etiqueta_antes_de_comprar(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, owner, order_id = await _pedido_por_volume(client, session_factory)
    previa = await client.get(f"{base(tenant)}/orders/{order_id}/shipment/preview", headers=owner)
    assert previa.status_code == 200, previa.text
    corpo = previa.json()
    assert corpo["available"] is True
    assert corpo["labels"] == 2
    esperado = (1500 + 980 * 2 + DISTANCIA) + (1500 + 380 * 2 + DISTANCIA)
    assert corpo["price_cents"] == esperado
    assert corpo["charged_cents"] == esperado  # loja de teste sem acréscimo
    assert corpo["increase_percent"] == 0
    assert corpo["needs_confirmation"] is False
    assert fake_shipping.SHIP_CALLS == [], "prévia não compra nada"

    # O checkout cotou mais barato do que a etiqueta sai hoje (o frete subiu depois): a compra
    # pede confirmação. Com frete grátis o cobrado é 0, e o que vale é o cotado, não o cobrado.
    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        pedido = await session.get(Order, order_id)
        assert pedido is not None
        pedido.fulfillment = {**(pedido.fulfillment or {}), "price_cents": esperado * 100 // 115}
        pedido.delivery_fee_cents = 0
        await session.commit()
    corpo = (
        await client.get(f"{base(tenant)}/orders/{order_id}/shipment/preview", headers=owner)
    ).json()
    assert corpo["charged_cents"] == 0
    assert corpo["increase_percent"] == 15
    assert corpo["needs_confirmation"] is True
