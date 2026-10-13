"""Entrega pela Uber, fase 1 (docs/15-entrega-uber-direct.md): quem entrega cada zona, o telefone
que a Uber exige e as chaves da conta da loja — conferidas na Uber antes de guardar.

A Uber aqui é falsa (`httpx.MockTransport`): o teste não pode pedir token de verdade, e a conta
self-service do Brasil só dá chaves de produção (docs/15, "Fatos verificados").
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.scopes import TenantRole
from app.courier import uber
from app.notifications.messages import status_label
from app.orders.models import Order
from app.tenancy.context import bind_session_tenant
from app.tenancy.models import Tenant, TenantIntegrationCredential
from tests.shoppers import as_shopper, selling_store, signed_in
from tests.test_catalog import base, member_headers
from tests.test_checkout_place import order_body, place
from tests.test_pricing import product
from tests.test_storefront_catalog import set_stock

CART = "/api/v1/cart"
CUSTOMER_ID = "cust-da-loja-0001"
CLIENT_ID = "client-da-loja-0001"
CLIENT_SECRET = "segredo-da-loja-0001"

ORIGIN = {
    "name": "Loja",
    "postal_code": "01310100",
    "address": "Av. Paulista",
    "number": "1000",
    "district": "Bela Vista",
    "city": "São Paulo",
    "state": "SP",
    "phone": "11999998888",
}

FULFILLMENT_UBER = {
    "pickup": {"enabled": True, "locations": [{"name": "Loja", "address": "Rua A, 1"}]},
    "delivery": {
        "enabled": True,
        "zones": [
            {
                "name": "Centro",
                "kind": "cep_ranges",
                "cep_ranges": [{"start": "01000000", "end": "01999999"}],
                "fee_cents": 800,
                "courier": "uber",
            },
            {
                "name": "Longe",
                "kind": "cep_ranges",
                "cep_ranges": [{"start": "02000000", "end": "02999999"}],
                "fee_cents": 1500,
            },
        ],
        "origin": ORIGIN,
    },
}

#: Instalar a fábrica falsa sobre outra falsa embrulharia a anterior (lição do teste do Chatwoot).
_REAL_CLIENT = httpx.AsyncClient


class FakeUber:
    """Responde token e listagem como a Uber respondeu à sonda de 13/10/2026."""

    def __init__(self, *, token_status: int = 200, list_status: int = 200) -> None:
        self.token_status = token_status
        self.list_status = list_status
        self.sem_rede = False
        self.pedidos: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.sem_rede:
            raise httpx.ConnectTimeout("sem rota")
        self.pedidos.append(request)
        if str(request.url) == settings.uber_direct_auth_url:
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": "invalid_client"})
            return httpx.Response(
                200, json={"access_token": "tok-falso", "expires_in": 2592000, "scope": uber.SCOPE}
            )
        if request.url.path == f"/v1/customers/{CUSTOMER_ID}/deliveries":
            return httpx.Response(self.list_status, json={"data": []})
        return httpx.Response(404, json={"code": "customer_not_found"})

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        transport = httpx.MockTransport(self.handler)

        def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
            kwargs["transport"] = transport
            return _REAL_CLIENT(*args, **kwargs)

        monkeypatch.setattr(uber.httpx, "AsyncClient", factory)


def chaves(**overrides: str) -> dict[str, str]:
    return {
        "customer_id": CUSTOMER_ID,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    } | overrides


async def _loja(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    fulfillment: dict[str, Any] | None = None,
) -> tuple[Tenant, dict[str, str], str]:
    tenant = await selling_store(
        session_factory,
        flags={"delivery": True},
        settings={"fulfillment": fulfillment or FULFILLMENT_UBER},
    )
    owner = await member_headers(client, session_factory, tenant)
    return tenant, owner, f"{base(tenant)}/delivery/uber"


async def _segredos(
    session_factory: async_sessionmaker[AsyncSession], tenant: Tenant
) -> uber.UberCredentials | None:
    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        return await uber.load_credentials(session, tenant.id)


# ---------------------------------------------------------------------------------- as chaves


async def test_chaves_conferidas_sao_guardadas_cifradas_e_voltam_mascaradas(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeUber()
    fake.install(monkeypatch)
    tenant, owner, url = await _loja(client, session_factory)

    antes = (await client.get(url, headers=owner)).json()
    assert antes["connected"] is False and antes["missing"] == ["chaves"]
    assert antes["origin_ready"] is True and antes["uber_zones"] == 1
    assert antes["last_test_ok"] is None

    salvo = await client.put(f"{url}/credentials", json=chaves(environment="test"), headers=owner)
    assert salvo.status_code == 200, salvo.text
    lido = salvo.json()
    assert lido["connected"] is True and lido["missing"] == []
    assert lido["environment"] == "test"
    assert lido["last_test_ok"] is True and lido["last_test_at"]
    # Nenhum segredo volta inteiro na resposta.
    assert set(lido["masked"]) == set(uber.SECRET_KEYS)
    for valor in (CUSTOMER_ID, CLIENT_ID, CLIENT_SECRET):
        assert valor not in salvo.text

    # A Uber foi perguntada do jeito que a sonda mediu: token com client_credentials e o escopo,
    # depois a listagem com o Customer ID no caminho e o token no cabeçalho.
    token, lista = fake.pedidos
    corpo = parse_qs(token.content.decode())
    assert corpo["grant_type"] == ["client_credentials"]
    assert corpo["scope"] == [uber.SCOPE]
    assert corpo["client_secret"] == [CLIENT_SECRET]
    assert lista.url.path == f"/v1/customers/{CUSTOMER_ID}/deliveries"
    assert lista.headers["authorization"] == "Bearer tok-falso"

    guardado = await _segredos(session_factory, tenant)
    assert guardado == uber.UberCredentials(CUSTOMER_ID, CLIENT_ID, CLIENT_SECRET)
    # Cifrado no banco: o texto puro não aparece na linha guardada nem na forma mascarada.
    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        linhas = (
            await session.scalars(
                select(TenantIntegrationCredential).where(
                    TenantIntegrationCredential.provider == uber.PROVIDER
                )
            )
        ).all()
    assert len(linhas) == 3
    for linha in linhas:
        assert CLIENT_SECRET.encode() not in linha.ciphertext
        assert CLIENT_SECRET not in linha.masked


@pytest.mark.parametrize(
    ("fake", "motivo"),
    [
        (FakeUber(token_status=401), "client"),
        (FakeUber(list_status=404), "customer"),
    ],
    ids=["token-recusado", "customer-de-outra-conta"],
)
async def test_chave_recusada_pela_uber_nao_vira_conectada(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    fake: FakeUber,
    motivo: str,
) -> None:
    fake.pedidos = []
    fake.install(monkeypatch)
    tenant, owner, url = await _loja(client, session_factory)

    recusado = await client.put(f"{url}/credentials", json=chaves(), headers=owner)
    assert recusado.status_code == 422, recusado.text
    assert recusado.json()["error"]["details"]["reason"] == f"uber_refused_{motivo}"
    assert await _segredos(session_factory, tenant) is None
    assert (await client.get(url, headers=owner)).json()["connected"] is False


async def test_uber_fora_do_ar_guarda_e_diz_que_nao_deu_para_conferir(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeUber()
    fake.sem_rede = True
    fake.install(monkeypatch)
    tenant, owner, url = await _loja(client, session_factory)

    salvo = await client.put(f"{url}/credentials", json=chaves(), headers=owner)
    assert salvo.status_code == 200, salvo.text
    assert salvo.json()["connected"] is True
    assert salvo.json()["last_test_ok"] is False
    assert "não respondeu" in salvo.json()["last_test_detail"]
    assert await _segredos(session_factory, tenant) is not None

    # Voltou: "Testar" confere de novo e grava o resultado.
    fake.sem_rede = False
    testado = await client.post(f"{url}/test", headers=owner)
    assert testado.status_code == 200, testado.text
    assert testado.json()["last_test_ok"] is True
    assert (await client.get(url, headers=owner)).json()["last_test_ok"] is True


async def test_desconectar_apaga_as_chaves_e_o_ultimo_teste(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeUber().install(monkeypatch)
    tenant, owner, url = await _loja(client, session_factory)
    sem_chaves = await client.post(f"{url}/test", headers=owner)
    assert sem_chaves.status_code == 422
    assert sem_chaves.json()["error"]["details"]["reason"] == "uber_not_connected"

    await client.put(f"{url}/credentials", json=chaves(), headers=owner)
    fora = await client.delete(f"{url}/credentials", headers=owner)
    assert fora.status_code == 200, fora.text
    assert fora.json()["connected"] is False and fora.json()["masked"] == {}
    assert fora.json()["last_test_ok"] is None
    assert "chaves" in fora.json()["missing"]
    assert await _segredos(session_factory, tenant) is None


async def test_so_o_dono_mexe_nas_chaves(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeUber().install(monkeypatch)
    tenant, _, url = await _loja(client, session_factory)
    admin = await member_headers(client, session_factory, tenant, TenantRole.ADMIN)
    assert (await client.get(url, headers=admin)).status_code == 403
    assert (await client.put(f"{url}/credentials", json=chaves(), headers=admin)).status_code == 403
    assert (await client.delete(f"{url}/credentials", headers=admin)).status_code == 403
    assert await _segredos(session_factory, tenant) is None


# ------------------------------------------------------------------------ configuração da loja


async def test_salvar_as_zonas_nao_apaga_a_coleta_nem_o_teste_das_chaves(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeUber().install(monkeypatch)
    tenant, owner, url = await _loja(client, session_factory)
    await client.put(f"{url}/credentials", json=chaves(environment="test"), headers=owner)

    # O formulário das zonas manda só `enabled` e `zones` e ainda tenta afirmar um teste e um
    # ambiente que a Uber nunca disse: as duas coisas voltam ao que a tela das chaves gravou.
    zonas = FULFILLMENT_UBER["delivery"]["zones"]  # type: ignore[index]
    valor = {
        "pickup": FULFILLMENT_UBER["pickup"],
        "delivery": {
            "enabled": True,
            "zones": zonas,
            "uber": {
                "auto_dispatch": False,
                "max_fee_cents": 2500,
                "environment": "production",
                "account_check": {"provider": "uber_direct", "ok": True, "detail": "x", "at": "y"},
            },
        },
    }
    gravado = await client.put(
        f"{base(tenant)}/settings/fulfillment", json={"value": valor}, headers=owner
    )
    assert gravado.status_code == 200, gravado.text
    entrega = gravado.json()["delivery"]
    assert entrega["origin"]["phone"] == ORIGIN["phone"]
    assert entrega["uber"]["auto_dispatch"] is False
    assert entrega["uber"]["max_fee_cents"] == 2500
    assert entrega["uber"]["environment"] == "test"
    assert entrega["uber"]["account_check"]["detail"] == "chaves conferidas"

    # Sem `uber` nenhum (formulário antigo): a política da Uber fica como estava.
    so_zonas = {"pickup": valor["pickup"], "delivery": {"enabled": True, "zones": zonas}}
    de_novo = await client.put(
        f"{base(tenant)}/settings/fulfillment", json={"value": so_zonas}, headers=owner
    )
    assert de_novo.json()["delivery"]["uber"]["max_fee_cents"] == 2500
    assert de_novo.json()["delivery"]["origin"] is not None

    # `null` explícito tira a coleta — e a tela passa a dizer que falta.
    sem_coleta = {"pickup": valor["pickup"], "delivery": so_zonas["delivery"] | {"origin": None}}
    await client.put(
        f"{base(tenant)}/settings/fulfillment", json={"value": sem_coleta}, headers=owner
    )
    assert (await client.get(url, headers=owner)).json()["missing"] == ["coleta"]


async def test_coleta_exige_telefone(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, owner, _ = await _loja(client, session_factory)
    sem_fone = {k: v for k, v in ORIGIN.items() if k != "phone"}
    valor = {
        "pickup": FULFILLMENT_UBER["pickup"],
        "delivery": FULFILLMENT_UBER["delivery"] | {"origin": sem_fone},  # type: ignore[operator]
    }
    resposta = await client.put(
        f"{base(tenant)}/settings/fulfillment", json={"value": valor}, headers=owner
    )
    assert resposta.status_code == 422, resposta.text


# --------------------------------------------------------------------------------- o checkout


async def _carrinho_com_entrega(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    tenant: Tenant,
    owner: dict[str, str],
    cep: str,
) -> tuple[dict[str, str], dict[str, Any]]:
    me = as_shopper(tenant, await signed_in(session_factory, tenant))
    bolo = await product(client, session_factory, tenant, owner)
    variante = bolo["variants"][0]["id"]
    await set_stock(session_factory, variante, 20)
    await client.post(f"{CART}/items", json={"variant_id": variante, "quantity": "1"}, headers=me)
    endereco = (
        await client.post(
            "/api/v1/me/addresses",
            json={
                "recipient_name": "Maria",
                "postal_code": cep,
                "street": "Rua B",
                "number": "10",
                "district": "Centro",
                "city": "São Paulo",
                "state": "SP",
            },
            headers=me,
        )
    ).json()
    carrinho = await client.put(
        f"{CART}/fulfillment",
        json={"type": "delivery", "address_id": endereco["id"]},
        headers=me,
    )
    assert carrinho.status_code == 200, carrinho.text
    return me, dict(carrinho.json())


async def test_zona_da_uber_pede_o_whatsapp_e_o_pedido_guarda_quem_entrega(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, owner, _ = await _loja(client, session_factory)
    me, carrinho = await _carrinho_com_entrega(client, session_factory, tenant, owner, "01310-100")
    assert carrinho["quote"]["fulfillment"]["snapshot"]["courier"] == "uber"

    # A loja não exige WhatsApp, mas a Uber exige um telefone de quem recebe.
    sem = await place(client, me, order_body(carrinho))
    assert sem.status_code == 422, sem.text
    assert sem.json()["error"]["code"] == "whatsapp_required"

    feito = await place(
        client, me, order_body(carrinho, contact={"name": "Maria", "phone": "(11) 98888-7777"})
    )
    assert feito.status_code == 201, feito.text
    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        pedido = await session.get(Order, feito.json()["id"])
        assert pedido is not None
        assert (pedido.fulfillment or {}).get("courier") == "uber"


async def test_zona_da_equipe_continua_sem_exigir_telefone(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, owner, _ = await _loja(client, session_factory)
    me, carrinho = await _carrinho_com_entrega(client, session_factory, tenant, owner, "02010-000")
    assert carrinho["quote"]["fulfillment"]["snapshot"]["courier"] == "own"
    feito = await place(client, me, order_body(carrinho))
    assert feito.status_code == 201, feito.text


def test_pronto_na_entrega_fala_do_entregador() -> None:
    assert status_label("ready_for_pickup", "delivery") == "pronto e aguardando o entregador"
    assert status_label("ready_for_pickup", "pickup") != status_label(
        "ready_for_pickup", "delivery"
    )
