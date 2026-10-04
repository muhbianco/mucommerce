"""Envio sem nota fiscal: a declaração de conteúdo (DC-e) e a regra do Paraná (fato 8)."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import httpx
import pytest
import respx
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.shipping.declaration import OrderLine, line_name, order_declaration, parcel_declarations
from app.shipping.provider import (
    DeclaredItem,
    Parcel,
    ServiceInfo,
    ShipmentRequest,
    ShippingCredentials,
    ShippingParty,
    ShippingProviderError,
)
from app.shipping.providers import fake as fake_shipping
from app.shipping.providers.melhorenvio import SANDBOX_URL, MelhorEnvioProvider, products
from app.shipping.service import invoice_only
from app.tenancy.service import Actor, TenantService
from tests.test_catalog import base
from tests.test_checkout_place import order_body, place
from tests.test_shipping_flow import _aceitar, _despachar
from tests.test_shipping_v2_flow import CAIXA_P, escolher, loja, rabiolas


@pytest.fixture(autouse=True)
def fake_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "shipping_allowed_providers", "fake")
    fake_shipping.reset()


# ----------------------------------------------------------------- declaração (puro)


def test_nome_igual_ao_do_pedido() -> None:
    assert line_name("Camiseta", "P") == "Camiseta — P"
    assert line_name("Rabiola", "Padrão") == "Rabiola"
    assert line_name("Rabiola", None) == "Rabiola"
    assert len(line_name("x" * 200, None)) == 80


def test_pedido_inteiro_item_a_item() -> None:
    itens = order_declaration(
        [
            OrderLine("v1", "Rabiola 500 m", 3000, 3600),
            OrderLine("v2", "Queijo", 2500, 5000, by_weight=True, unit_label="kg"),
            OrderLine("v3", "Brinde", 1000, 0),
        ]
    )
    assert itens == (
        DeclaredItem("Rabiola 500 m", 3, 1200),
        # A peso: quantidade 1 e o peso no nome (a doc não diz se aceita fração).
        DeclaredItem("Queijo (2,5 kg)", 1, 5000),
    )


def test_cada_volume_declara_so_o_que_leva() -> None:
    linhas = [OrderLine("v1", "Rabiola", 8000, 12000), OrderLine("v2", "Carretel", 1000, 2500)]
    volumes = [
        {"items": [{"variant_id": "v1", "units": 6}]},
        {"items": [{"variant_id": "v1", "units": 2}, {"variant_id": "v2", "units": 1}]},
    ]
    primeiro, segundo = parcel_declarations(linhas, volumes)
    assert primeiro == (DeclaredItem("Rabiola", 6, 1500),)
    assert segundo == (DeclaredItem("Rabiola", 2, 1500), DeclaredItem("Carretel", 1, 2500))
    total = sum(i.quantity * i.unit_value_cents for decl in (primeiro, segundo) for i in decl)
    assert total == 12000 + 2500, "a soma das etiquetas é o que o cliente pagou"


# ----------------------------------------------------------------- Melhor Envio


def _pedido(**extra: Any) -> ShipmentRequest:
    parte = ShippingParty(
        name="Loja",
        postal_code="01001000",
        street="Praça da Sé",
        number="1",
        district="Sé",
        city="São Paulo",
        state="SP",
        document="46867029000176",
    )
    return ShipmentRequest(
        reference="remessa-1",
        service_code="1",
        sender=parte,
        recipient=parte,
        parcels=(
            Parcel(weight_grams=680, width_mm=150, height_mm=100, depth_mm=200, value_cents=6100),
        ),
        order_number=123,
        insurance_cents=6100,
        **extra,
    )


def test_products_item_a_item_em_texto_como_a_doc() -> None:
    pedido = _pedido(
        items=(DeclaredItem("Rabiola 500 m", 3, 1200), DeclaredItem("Carretel de linha", 1, 2500))
    )
    assert products(pedido) == [
        {"name": "Rabiola 500 m", "quantity": "3", "unitary_value": "12.00"},
        {"name": "Carretel de linha", "quantity": "1", "unitary_value": "25.00"},
    ]


def test_sem_itens_cai_na_linha_unica_em_vez_de_recusar() -> None:
    assert products(_pedido()) == [
        {"name": "Pedido 123", "quantity": "1", "unitary_value": "61.00"}
    ]


@respx.mock
async def test_carrinho_leva_os_itens_da_declaracao() -> None:
    rota = respx.post(f"{SANDBOX_URL}/api/v2/me/cart").mock(
        return_value=httpx.Response(422, json={"message": "recusado no teste"})
    )
    credenciais = ShippingCredentials(
        secrets={"access_token": "token-de-teste"}, public_config={}, sandbox=True
    )
    with pytest.raises(ShippingProviderError):  # interessa o corpo enviado
        await MelhorEnvioProvider().ship(
            credenciais, _pedido(items=(DeclaredItem("Rabiola", 2, 1500),))
        )
    corpo = json.loads(rota.calls.last.request.content)
    assert corpo["products"] == [{"name": "Rabiola", "quantity": "2", "unitary_value": "15.00"}]
    assert corpo["options"]["non_commercial"] is True
    assert "invoice" not in corpo["options"]


# ----------------------------------------------------------------- despacho


async def _pedido_aceito(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], servico: str
) -> tuple[Any, str]:
    tenant, owner, me = await loja(client, session_factory, caixas=(CAIXA_P,))
    _, cotacao, endereco = await rabiolas(client, session_factory, tenant, owner, me, 8)
    opcao = next(o for o in cotacao["options"] if o["service_code"] == servico)
    carrinho = await escolher(client, me, endereco, opcao)
    resposta = await place(client, me, order_body(carrinho))
    assert resposta.status_code == 201, resposta.text
    order_id = str(resposta.json()["id"])
    await _aceitar(session_factory, tenant, order_id)
    return tenant, order_id


async def test_etiqueta_por_volume_declara_o_conteudo_de_cada_volume(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """8 rabiolas em 2 Caixas P (6 + 2), uma etiqueta por volume: cada DACE lista só as dela."""
    tenant, order_id = await _pedido_aceito(client, session_factory, "fake_economico")
    await _despachar(session_factory, tenant, order_id)
    etiquetas = fake_shipping.SHIP_REQUESTS
    assert sorted(e.items[0].quantity for e in etiquetas) == [2, 6]
    for etiqueta in etiquetas:
        (item,) = etiqueta.items
        assert (item.name, item.unit_value_cents) == ("Rabiola", 1500)
        # A declaração é a do volume desta etiqueta: o peso dele bate com as unidades declaradas
        # (80 g da Caixa P + 150 g por rabiola).
        (volume,) = etiqueta.parcels
        assert volume.weight_grams == 80 + 150 * item.quantity


async def test_etiqueta_unica_declara_o_pedido_inteiro(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, order_id = await _pedido_aceito(client, session_factory, "fake_expresso")
    await _despachar(session_factory, tenant, order_id)
    (pedido,) = fake_shipping.SHIP_REQUESTS
    assert len(pedido.parcels) == 2, "multivolume: os dois volumes numa etiqueta"
    assert pedido.items == (DeclaredItem("Rabiola", 8, 1500),)


# ----------------------------------------------------------------- Paraná


def test_jadlog_saindo_do_parana_so_com_nota() -> None:
    assert invoice_only("Jadlog", "PR")
    assert invoice_only("JADLOG", " pr ")
    assert invoice_only("Jadlog", "SP") is None
    assert invoice_only("Correios", "PR") is None
    assert invoice_only("Jadlog", None) is None


async def _origem_no_parana(session_factory: async_sessionmaker[AsyncSession], tenant: Any) -> None:
    async with session_factory() as session:
        service = TenantService(session)
        row = await service.get_or_404(tenant.id)
        atual = dict((await service.repo.settings(tenant.id))["fulfillment"])
        atual["shipping"] = {
            **atual["shipping"],
            "origin": {**atual["shipping"]["origin"], "state": "PR", "city": "Curitiba"},
        }
        await service.set_setting(row, "fulfillment", atual, Actor.system("tests"))
        await session.commit()


async def test_loja_do_parana_nao_ve_jadlog_na_cotacao(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """O Expresso da fake vira "Jadlog": saindo do Paraná ele sai da cotação, com o motivo."""
    original = fake_shipping.FakeShippingProvider.quote

    async def como_jadlog(self: Any, credentials: Any, request: Any) -> Any:
        opcoes = await original(self, credentials, request)
        return tuple(
            replace(o, carrier="Jadlog") if o.service_code == "fake_expresso" else o for o in opcoes
        )

    monkeypatch.setattr(fake_shipping.FakeShippingProvider, "quote", como_jadlog)
    tenant, owner, me = await loja(client, session_factory, caixas=(CAIXA_P,))
    await _origem_no_parana(session_factory, tenant)
    _, cotacao, _ = await rabiolas(client, session_factory, tenant, owner, me, 4)
    assert [o["service_code"] for o in cotacao["options"]] == ["fake_economico"]


async def test_tela_de_servicos_marca_a_jadlog_no_parana(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def servicos(self: Any, credentials: Any) -> tuple[ServiceInfo, ...]:
        return (
            ServiceInfo("1", "PAC", "Correios", "normal", True),
            ServiceInfo("3", ".Package", "Jadlog", "normal", True, grouped_volumes=True),
        )

    monkeypatch.setattr(fake_shipping.FakeShippingProvider, "list_services", servicos)
    tenant, owner, _me = await loja(client, session_factory, caixas=(CAIXA_P,))
    await _origem_no_parana(session_factory, tenant)
    lido = (await client.get(f"{base(tenant)}/shipping/services", headers=owner)).json()
    por_codigo = {s["code"]: s for s in lido["services"]}
    assert por_codigo["1"]["available"] is True and por_codigo["1"]["blocked_reason"] is None
    assert por_codigo["3"]["available"] is False
    assert por_codigo["3"]["offered"] is False
    assert "Paraná" in por_codigo["3"]["blocked_reason"]
    gravar = await client.put(
        f"{base(tenant)}/shipping/services", json={"codes": ["1", "3"]}, headers=owner
    )
    assert gravar.status_code == 422
    assert gravar.json()["error"]["details"]["reason"] == "service_unavailable"
