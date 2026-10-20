"""Frenet pela API whitelabel (docs/14 §4): cota volumes, compra a etiqueta com o saldo da conta
do lojista, e nunca compra duas vezes.

A Frenet aqui é de mentira e responde como a sonda mediu em 20/10/2026: camelCase, `Created` só
aceito como `YYYY-MM-DDTHH:MM:SSZ`, e-mail do remetente obrigatório, envio criado em situação 1 e
apagado em 9. O que a sonda **não** mediu (o pagamento) segue a documentação: erro 3000 é saldo
insuficiente e a situação 4 é "pago".
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import settings
from app.shipping import registry
from app.shipping.provider import (
    DeclaredItem,
    InsufficientBalanceError,
    Parcel,
    QuoteRequest,
    ShipmentRequest,
    ShippingCredentials,
    ShippingParty,
    ShippingProviderError,
)
from app.shipping.providers import frenet
from app.shipping.providers.frenet import FrenetProvider

CREDENCIAIS = ShippingCredentials(
    secrets={"access_token": "token-da-loja"}, public_config={}, sandbox=False
)
_REAL = httpx.AsyncClient

REMETENTE = ShippingParty(
    name="Loja",
    postal_code="01001-000",
    street="Praça da Sé",
    number="1",
    district="Sé",
    city="São Paulo",
    state="SP",
    email="loja@exemplo.test",
    document="60773549000140",
)
DESTINO = ShippingParty(
    name="Maria",
    postal_code="20040-020",
    street="Praça Pio X",
    number="1",
    district="Centro",
    city="Rio de Janeiro",
    state="RJ",
)
VOLUME = Parcel(weight_grams=500, width_mm=150, height_mm=100, depth_mm=200, value_cents=15000)


def _cotacoes() -> dict[str, Any]:
    return {
        "sessionId": "s1",
        "quotations": [
            {
                "shippingServiceCode": "03298",
                "shippingServiceName": "PAC",
                "carrier": "Correios",
                "carrierCode": "COR",
                "shippingPrice": 24.72,
                "platformShippingPrice": 24.72,
                "deliveryTime": 5,
                "error": False,
            },
            {
                "shippingServiceCode": "F_3",
                "shippingServiceName": "Jadlog Package",
                "carrier": "Jadlog",
                "carrierCode": "JAD",
                "shippingPrice": 18.48,
                "deliveryTime": 6,
                "error": False,
            },
            {
                "shippingServiceCode": "04227",
                "shippingServiceName": "Mini Envios",
                "carrier": "Correios",
                "error": True,
                "msg": "Erro: A soma das dimensões excede o limite",
            },
        ],
    }


class FrenetFalsa:
    def __init__(self) -> None:
        self.chamadas: list[httpx.Request] = []
        self.checkout: tuple[int, Any] = (200, {"statusBatch": "Processado", "items": []})
        self.situacao_depois = 4
        self.status_falha = False
        self.label_url = "https://frenet.test/etiqueta/1.pdf"
        self.criado: list[Any] = []
        self.apagados: list[str] = []
        self.checkout_rede = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.chamadas.append(request)
        assert request.headers["token"] == "token-da-loja"
        assert request.headers["x-partner-token"] == "parceiro-de-teste"
        caminho, metodo = request.url.path, request.method
        if caminho.endswith("/quotes"):
            return httpx.Response(200, json=_cotacoes())
        if caminho.endswith("/shipments/checkout"):
            if self.checkout_rede:
                raise httpx.ReadTimeout("sem resposta")
            return httpx.Response(self.checkout[0], json=self.checkout[1])
        if caminho.endswith("/shipments") and metodo == "POST":
            corpo = json.loads(request.content)
            self.criado.append(corpo)
            return httpx.Response(
                200,
                json={
                    "statusBatch": "Processado",
                    "items": [{"shipmentId": 777, "orderId": corpo[0]["Order"]["Id"]}],
                },
            )
        if caminho.endswith("/cancel"):
            return httpx.Response(204)
        if caminho.endswith("/label"):
            return httpx.Response(
                200,
                json={"shipmentId": 777, "labelUrl": self.label_url, "trackingUrl": "https://r/1"},
            )
        if re.search(r"/shipments/\d+$", caminho) and metodo == "DELETE":
            self.apagados.append(caminho.rsplit("/", 1)[1])
            return httpx.Response(204)
        if re.search(r"/shipments/\d+$", caminho):
            if self.status_falha:
                raise httpx.ConnectError("fora")
            return httpx.Response(
                200, json={"shipmentId": 777, "shipmentStatus": self.situacao_depois}
            )
        if caminho.endswith("/wallet"):
            return httpx.Response(200, json={"balance": 0, "labelLimit": 20})
        if caminho.endswith("/carriers/services"):
            return httpx.Response(
                200,
                json={
                    "shippingServices": [
                        {
                            "serviceCode": "03220",
                            "serviceDescription": "Sedex",
                            "carrier": "Correios",
                            "enabled": True,
                        },
                        {
                            "serviceCode": "LOG_DRPOFF",
                            "serviceDescription": "Loggi",
                            "carrier": "Loggi",
                            "enabled": False,
                        },
                    ]
                },
            )
        return httpx.Response(404, json={"message": None, "details": None})

    def paths(self) -> list[str]:
        return [f"{r.method} {r.url.path}" for r in self.chamadas]


@pytest.fixture
def falsa(monkeypatch: pytest.MonkeyPatch) -> FrenetFalsa:
    f = FrenetFalsa()
    transporte = httpx.MockTransport(f.handler)

    def fabrica(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transporte
        return _REAL(*args, **kwargs)

    monkeypatch.setattr(frenet.httpx, "AsyncClient", fabrica)
    monkeypatch.setattr(settings, "frenet_partner_token", SecretStr("parceiro-de-teste"))

    async def sem_espera(_: float) -> None:
        return None

    monkeypatch.setattr(frenet.asyncio, "sleep", sem_espera)
    return f


def _pedido(**extra: Any) -> ShipmentRequest:
    base: dict[str, Any] = {
        "reference": "remessa-1.1",
        "service_code": "03298",
        "sender": REMETENTE,
        "recipient": DESTINO,
        "parcels": (VOLUME,),
        "order_number": 12,
        "insurance_cents": 15000,
        "items": (DeclaredItem(name="Pipa", quantity=2, unit_value_cents=7500),),
    }
    return ShipmentRequest(**(base | extra))


async def test_cota_volumes_com_os_dois_tokens(falsa: FrenetFalsa) -> None:
    opcoes = await FrenetProvider().quote(
        CREDENCIAIS,
        QuoteRequest(
            origin_postal_code="01001-000", destination_postal_code="20040-020", parcels=(VOLUME,)
        ),
    )
    por_codigo = {o.service_code: o for o in opcoes}
    assert por_codigo["03298"].price_cents == 2472 and por_codigo["03298"].delivery_days == 5
    assert por_codigo["F_3"].carrier == "Jadlog"
    assert por_codigo["04227"].usable is False and "excede" in (por_codigo["04227"].error or "")
    # Um volume por envio: pedido com mais volumes vira uma etiqueta por volume.
    assert {o.multi_volume_max for o in opcoes} == {1}
    corpo = json.loads(falsa.chamadas[0].content)
    assert corpo["Volumes"] == [{"Weight": 0.5, "Length": 20, "Height": 10, "Width": 15}]
    assert corpo["SenderZipCode"] == "01001000" and corpo["ShipmentItemValue"] == 150.0


async def test_cota_so_os_servicos_que_a_loja_escolheu(falsa: FrenetFalsa) -> None:
    opcoes = await FrenetProvider().quote(
        CREDENCIAIS,
        QuoteRequest(
            origin_postal_code="01001000",
            destination_postal_code="20040020",
            parcels=(VOLUME,),
            services=("F_3",),
        ),
    )
    assert [o.service_code for o in opcoes] == ["F_3"]


async def test_cep_sem_atendimento_e_frete_indisponivel_nao_erro(
    falsa: FrenetFalsa, monkeypatch: pytest.MonkeyPatch
) -> None:
    def nada(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            json={
                "message": None,
                "details": [{"code": 1005, "message": "A cotação não retornou dados."}],
            },
        )

    transporte = httpx.MockTransport(nada)
    monkeypatch.setattr(
        frenet.httpx, "AsyncClient", lambda *a, **k: _REAL(*a, **(k | {"transport": transporte}))
    )
    opcoes = await FrenetProvider().quote(
        CREDENCIAIS,
        QuoteRequest(
            origin_postal_code="01001000", destination_postal_code="00000000", parcels=(VOLUME,)
        ),
    )
    assert opcoes == ()


async def test_compra_a_etiqueta_e_confere_o_pagamento(falsa: FrenetFalsa) -> None:
    resultado = await FrenetProvider().ship(CREDENCIAIS, _pedido())
    assert resultado.provider_shipment_id == "777"
    assert resultado.label_url == "https://frenet.test/etiqueta/1.pdf"
    assert resultado.cost_cents == 2472 and resultado.carrier == "Correios"
    assert resultado.raw["tracking_url"] == "https://r/1"
    assert falsa.apagados == []
    [lote] = falsa.criado
    pedido = lote[0]["Order"]
    # O formato que a Frenet aceitou (os outros deram "Formato de data inválido").
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", pedido["Created"])
    assert pedido["Id"] == "remessa-1.1"
    assert pedido["From"]["Email"] == "loja@exemplo.test"
    assert pedido["From"]["Document"] == "60773549000140"
    assert pedido["Items"] == [{"ItemId": "1", "ProductName": "Pipa", "Quantity": 2, "Price": 75.0}]
    assert lote[0]["Quotation"]["ShippingServiceCode"] == "03298"
    assert lote[0]["Volumes"]["Weight"] == 0.5
    assert falsa.paths().count("POST /v1/shipments/checkout") == 1


async def test_sem_saldo_apaga_o_envio_e_diz_saldo_insuficiente(falsa: FrenetFalsa) -> None:
    falsa.checkout = (
        400,
        {"message": None, "details": [{"code": 3000, "message": "Saldo insuficiente."}]},
    )
    with pytest.raises(InsufficientBalanceError):
        await FrenetProvider().ship(CREDENCIAIS, _pedido())
    assert falsa.apagados == ["777"]  # nada fica pendente na conta do lojista


async def test_pagamento_pendente_tambem_e_falta_de_saldo(falsa: FrenetFalsa) -> None:
    falsa.situacao_depois = 2  # PendentePagamento: a conta não tinha saldo
    with pytest.raises(InsufficientBalanceError):
        await FrenetProvider().ship(CREDENCIAIS, _pedido())
    assert falsa.apagados == ["777"]


async def test_resposta_do_pagamento_perdida_mas_pago_nao_compra_de_novo(
    falsa: FrenetFalsa,
) -> None:
    falsa.checkout_rede = True
    resultado = await FrenetProvider().ship(CREDENCIAIS, _pedido())
    assert resultado.provider_shipment_id == "777"
    assert falsa.apagados == []


async def test_pagamento_sem_confirmacao_nao_apaga_nem_finge_sucesso(falsa: FrenetFalsa) -> None:
    falsa.checkout_rede = True
    falsa.status_falha = True
    with pytest.raises(ShippingProviderError) as erro:
        await FrenetProvider().ship(CREDENCIAIS, _pedido())
    assert erro.value.definitive and "777" in (erro.value.reason or "")
    assert falsa.apagados == []  # pode estar pago: quem confere é a pessoa, no painel


async def test_etiqueta_paga_sem_link_ainda_nao_vira_erro(falsa: FrenetFalsa) -> None:
    falsa.label_url = ""
    resultado = await FrenetProvider().ship(CREDENCIAIS, _pedido())
    assert resultado.provider_shipment_id == "777" and resultado.label_url is None


async def test_recusa_antes_de_chamar_sem_email_do_remetente_ou_com_dois_volumes(
    falsa: FrenetFalsa,
) -> None:
    from dataclasses import replace

    with pytest.raises(ShippingProviderError) as erro:
        await FrenetProvider().ship(CREDENCIAIS, _pedido(sender=replace(REMETENTE, email=None)))
    assert "e-mail do remetente" in (erro.value.reason or "")
    with pytest.raises(ShippingProviderError):
        await FrenetProvider().ship(CREDENCIAIS, _pedido(parcels=(VOLUME, VOLUME)))
    assert falsa.chamadas == []


async def test_conta_saldo_servicos_rastreio_e_cancelamento(falsa: FrenetFalsa) -> None:
    provedor = FrenetProvider()
    teste = await provedor.test_credentials(CREDENCIAIS)
    assert teste.ok and teste.detail == "saldo R$ 0,00, limite de 20 etiquetas"
    servicos = {s.code: s for s in await provedor.list_services(CREDENCIAIS)}
    assert servicos["03220"].available and servicos["03220"].kind == "express"
    assert servicos["LOG_DRPOFF"].available is False  # desligado na conta: cota, mas não emite
    assert "whitelabel-configuration" in str(falsa.chamadas[-1].url)

    falsa.situacao_depois = 5
    assert (await provedor.track(CREDENCIAIS, "777")).status == "posted"
    falsa.situacao_depois = 7
    assert (await provedor.track(CREDENCIAIS, "777")).status == "cancelled"
    assert await provedor.cancel(CREDENCIAIS, "777") is True


async def test_token_da_conta_recusado(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "frenet_partner_token", SecretStr("parceiro-de-teste"))
    transporte = httpx.MockTransport(lambda r: httpx.Response(401))
    monkeypatch.setattr(
        frenet.httpx, "AsyncClient", lambda *a, **k: _REAL(*a, **(k | {"transport": transporte}))
    )
    teste = await FrenetProvider().test_credentials(CREDENCIAIS)
    assert teste.ok is False and teste.refused is True


def test_frenet_so_aparece_com_o_token_de_parceiro(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "shipping_allowed_providers", "melhorenvio,frenet")
    monkeypatch.setattr(settings, "frenet_partner_token", SecretStr(""))
    assert registry.allowed() == ["melhorenvio"]
    assert registry.get_provider("frenet") is None
    monkeypatch.setattr(settings, "frenet_partner_token", SecretStr("parceiro-de-teste"))
    assert registry.allowed() == ["melhorenvio", "frenet"]
    assert registry.get_provider("frenet") is not None
