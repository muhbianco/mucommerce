"""PagBank pelo Checkout, contra um dublê da API deles (httpx.MockTransport).

O que estes testes seguram é o que decide dinheiro: o total que vai para a página de pagamento,
o estado que volta da cobrança, e o fato de que o aviso é só uma dica — quem afirma que foi pago
é o `GET /charges`, com o token da loja.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

import httpx
import pytest

from app.payments.models import PaymentStatus
from app.payments.provider import (
    ChargeRequest,
    InboundWebhook,
    ProviderCredentials,
    ProviderError,
    ProviderRef,
    WebhookVerdict,
)
from app.payments.providers.pagbank import (
    PagBankProvider,
    checkout_body,
    first_charge,
    pay_link,
)

TOKEN = "tok-" + "a" * 30
CREDS = ProviderCredentials(secrets={"token": TOKEN}, public_config={}, sandbox=True)
PAY = "https://pagbank.test/checkout/CHEC_123"


def request(**overrides: Any) -> ChargeRequest:
    values: dict[str, Any] = {
        "payment_id": str(uuid.uuid4()),
        "provider_reference": "alpha-7-abc123",
        "order_number": 7,
        "amount_cents": 3000,
        "currency": "BRL",
        "method": "link",
        "description": "Pedido #7 — Alpha",
        "payer_email": "maria@cliente.test",
        "payer_name": "Maria",
        "expires_at": None,
        "notification_url": "https://api.test/api/v1/webhooks/pagbank/key",
        "return_url": "https://alpha.loja.test/conta/pedidos/o/retorno/p",
    }
    return ChargeRequest(**(values | overrides))


class FakePagBank:
    """Só o suficiente da API deles para os testes afirmarem coisas."""

    def __init__(self, charge: dict[str, Any] | None = None, status: int = 200) -> None:
        self.charge = charge or {}
        self.status = status
        self.seen: list[tuple[str, str, dict[str, Any] | None]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        corpo = json.loads(request.content) if request.content else None
        self.seen.append((request.method, request.url.path, corpo))
        if request.headers.get("authorization") != f"Bearer {TOKEN}":
            return httpx.Response(401, json={"error_messages": [{"description": "sem token"}]})
        if request.method == "POST" and request.url.path == "/checkouts":
            return httpx.Response(
                self.status,
                json={
                    "id": "CHEC_123",
                    "status": "ACTIVE",
                    "links": [
                        {"rel": "SELF", "href": "https://api.test/checkouts/CHEC_123"},
                        {"rel": "PAY", "href": PAY},
                    ],
                },
            )
        if request.method == "GET" and request.url.path.startswith("/charges/"):
            if not self.charge:
                return httpx.Response(404, json={"error_messages": [{"description": "não achei"}]})
            return httpx.Response(200, json=self.charge)
        return httpx.Response(404, json={})

    def provider(self) -> PagBankProvider:
        return PagBankProvider(httpx.MockTransport(self.handler))


# ------------------------------------------------------------------------------------ puro


def test_o_checkout_vai_com_uma_linha_e_o_total_do_pedido() -> None:
    """Mandar o carrinho detalhado faria o PagBank recalcular e discordar por centavo."""
    corpo = checkout_body(request(), [{"type": "PIX"}])
    assert corpo["reference_id"] == "alpha-7-abc123"
    assert len(corpo["items"]) == 1
    assert corpo["items"][0]["unit_amount"] == 3000
    assert corpo["items"][0]["quantity"] == 1
    assert corpo["payment_methods"] == [{"type": "PIX"}]
    assert corpo["customer"] == {"name": "Maria", "email": "maria@cliente.test"}
    # O mesmo endereço recebe o aviso do checkout e o do pagamento.
    assert corpo["notification_urls"] == corpo["payment_notification_urls"]


def test_o_link_de_pagamento_sai_do_rel_PAY() -> None:
    outro = {"rel": "SELF", "href": "https://x"}
    assert pay_link({"links": [outro, {"rel": "PAY", "href": PAY}]}) == PAY
    assert pay_link({"links": [outro]}) is None
    # Link que não é https não serve: o cliente vai ser mandado para lá.
    assert pay_link({"links": [{"rel": "PAY", "href": "http://inseguro"}]}) is None


def test_a_cobranca_e_achada_nos_dois_formatos_de_aviso() -> None:
    dentro = {"charges": [{"id": "CHAR_1", "status": "PAID", "amount": {"value": 3000}}]}
    assert first_charge(dentro) == dentro["charges"][0]
    solta = {"id": "CHAR_1", "status": "PAID", "amount": {"value": 3000}}
    assert first_charge(solta) == solta
    assert first_charge({"id": "CHEC_1", "status": "ACTIVE"}) is None


# ------------------------------------------------------------------------------- chamadas


async def test_cria_o_checkout_e_devolve_o_link() -> None:
    api = FakePagBank()
    resultado = await api.provider().create_charge(CREDS, request())
    assert resultado.status == PaymentStatus.REQUIRES_ACTION
    assert resultado.checkout_url == PAY
    assert resultado.provider_payment_id == "CHEC_123"
    assert api.seen[0][:2] == ("POST", "/checkouts")


async def test_token_errado_para_na_hora() -> None:
    api = FakePagBank()
    creds = ProviderCredentials(secrets={"token": "outro"}, public_config={}, sandbox=True)
    with pytest.raises(ProviderError) as erro:
        await api.provider().create_charge(creds, request())
    assert erro.value.http_status == 401
    assert erro.value.definitive is True


async def test_sem_aviso_ainda_o_pagamento_continua_esperando() -> None:
    """O checkout existe, o dinheiro não. Inventar 'pago' aqui seria liberar pedido de graça."""
    api = FakePagBank()
    resultado = await api.provider().fetch_status(CREDS, ProviderRef("CHEC_123", "alpha-7"))
    assert resultado.status == PaymentStatus.REQUIRES_ACTION
    assert [c for c in api.seen if c[0] == "GET"] == []  # nem chegou a perguntar


async def test_cobranca_paga_vira_aprovado_com_o_valor_pago() -> None:
    api = FakePagBank(
        charge={
            "id": "CHAR_9",
            "reference_id": "alpha-7-abc123",
            "status": "PAID",
            "amount": {"value": 3000, "summary": {"total": 3000, "paid": 3000, "refunded": 0}},
            "payment_method": {"type": "PIX"},
        }
    )
    ref = ProviderRef("CHEC_123", "alpha-7-abc123", {"charge_id": "CHAR_9"}, 3000)
    resultado = await api.provider().fetch_status(CREDS, ref)
    assert resultado.status == PaymentStatus.APPROVED
    assert resultado.paid_amount_cents == 3000
    assert resultado.provider_reference == "alpha-7-abc123"
    assert resultado.provider_status_detail == "PIX"


@pytest.mark.parametrize(
    ("bruto", "esperado"),
    [
        ("DECLINED", PaymentStatus.REJECTED),
        ("CANCELED", PaymentStatus.CANCELLED),
        ("IN_ANALYSIS", PaymentStatus.REQUIRES_ACTION),
        ("AUTHORIZED", PaymentStatus.REQUIRES_ACTION),
        ("COISA_NOVA", PaymentStatus.PENDING),
    ],
)
async def test_estados_da_cobranca(bruto: str, esperado: str) -> None:
    """`AUTHORIZED` é pré-autorização, não dinheiro — por isso não vira aprovado."""
    api = FakePagBank(charge={"id": "CHAR_9", "status": bruto, "amount": {"value": 3000}})
    ref = ProviderRef("CHEC_123", "alpha-7", {"charge_id": "CHAR_9"}, 3000)
    resultado = await api.provider().fetch_status(CREDS, ref)
    assert resultado.status == esperado
    assert resultado.paid_amount_cents is None


# -------------------------------------------------------------------------------- webhook


def inbound(body: dict[str, Any], *, assinado: bool = True) -> InboundWebhook:
    bruto = json.dumps(body).encode()
    headers = {}
    if assinado:
        headers["x-authenticity-token"] = hashlib.sha256(f"{TOKEN}-".encode() + bruto).hexdigest()
    return InboundWebhook(raw_body=bruto, headers=headers, query={})


def test_assinatura_confere_e_assinatura_torta_e_recusada() -> None:
    provider = PagBankProvider()
    corpo = {"id": "CHEC_123", "charges": [{"id": "CHAR_9", "status": "PAID"}]}
    assert provider.verify_webhook(CREDS, inbound(corpo)) is WebhookVerdict.VALID

    torto = inbound(corpo)
    adulterado = InboundWebhook(
        raw_body=torto.raw_body, headers={"x-authenticity-token": "0" * 64}, query={}
    )
    assert provider.verify_webhook(CREDS, adulterado) is WebhookVerdict.INVALID


def test_aviso_sem_header_nao_e_rejeitado_e_sim_tratado_como_dica() -> None:
    """Sandbox do PagBank não manda o header. Rejeitar travaria o teste da loja à toa —
    e não há risco: quem afirma o pagamento é o GET /charges com o token da loja."""
    provider = PagBankProvider()
    corpo = {"id": "CHEC_123", "charges": [{"id": "CHAR_9", "status": "PAID"}]}
    veredito = provider.verify_webhook(CREDS, inbound(corpo, assinado=False))
    assert veredito is WebhookVerdict.UNSUPPORTED


def test_o_aviso_entrega_a_cobranca_e_a_nossa_referencia() -> None:
    provider = PagBankProvider()
    corpo = {
        "id": "CHEC_123",
        "reference_id": "alpha-7-abc123",
        "charges": [{"id": "CHAR_9", "status": "PAID", "amount": {"value": 3000}}],
    }
    dica = provider.parse_webhook(inbound(corpo))
    assert dica.hints == {"charge_id": "CHAR_9"}
    assert dica.provider_reference == "alpha-7-abc123"
    assert dica.resource_id == "CHAR_9"
    # O id do checkout não é o do pagamento: quem paga é a cobrança.
    assert dica.provider_payment_id is None


def test_dois_avisos_iguais_tem_a_mesma_chave_e_avisos_diferentes_nao() -> None:
    provider = PagBankProvider()
    um = {"charges": [{"id": "CHAR_9", "status": "PAID"}]}
    outro = {"charges": [{"id": "CHAR_9", "status": "CANCELED"}]}

    def chave(corpo: dict[str, Any]) -> str | None:
        return provider.parse_webhook(inbound(corpo)).dedupe_key

    assert chave(um) == chave(um)
    assert chave(um) != chave(outro)


async def test_teste_de_credencial_nao_cria_nada_na_conta() -> None:
    api = FakePagBank()  # sem cobrança: responde 404, que é o "token aceito"
    resultado = await api.provider().test_credentials(CREDS)
    assert resultado.ok is True
    assert all(metodo == "GET" for metodo, _, _ in api.seen)

    ruim = ProviderCredentials(secrets={"token": "errado"}, public_config={}, sandbox=True)
    assert (await api.provider().test_credentials(ruim)).ok is False
