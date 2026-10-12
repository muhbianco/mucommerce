"""A conta da transportadora no painel (docs/14-pedidos-lunares.md §4): escolher a transportadora,
token conferido antes de guardar, último teste gravado, e o pedido fiel a quem cotou."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.integrations.credentials import CredentialStore
from app.shipping.providers import fake as fake_shipping
from app.tenancy.context import bind_session_tenant
from tests.test_catalog import base
from tests.test_shipping_flow import _despachar, loja, pedido_pronto_para_despacho


@pytest.fixture(autouse=True)
def fake_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "shipping_allowed_providers", "fake")
    fake_shipping.reset()


async def stored_token(session_factory: async_sessionmaker[AsyncSession], tenant: Any) -> Any:
    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        return await CredentialStore(session, tenant.id).get("fake", "access_token")


async def test_token_recusado_nao_vira_conectado(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, owner, _ = await loja(client, session_factory)
    url = f"{base(tenant)}/shipping"
    recusado = await client.put(
        f"{url}/credentials", json={"access_token": fake_shipping.REFUSED_TOKEN}, headers=owner
    )
    assert recusado.status_code == 422, recusado.text
    assert recusado.json()["error"]["details"]["reason"] == "token_refused"
    assert await stored_token(session_factory, tenant) == "token-de-teste"  # nada mudou

    aceito = await client.put(
        f"{url}/credentials", json={"access_token": "token-novo-da-loja"}, headers=owner
    )
    assert aceito.status_code == 200, aceito.text
    assert aceito.json()["last_test_ok"] is True and aceito.json()["last_test_at"]
    assert await stored_token(session_factory, tenant) == "token-novo-da-loja"

    # O resultado fica gravado: a tela mostra o último teste sem perguntar de novo.
    lido = (await client.get(url, headers=owner)).json()
    assert lido["last_test_ok"] is True and lido["connected"] is True
    assert [p["name"] for p in lido["providers"]] == ["fake"]
    assert lido["providers"][0]["active"] is True


async def test_testar_conta_grava_o_resultado(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, owner, _ = await loja(client, session_factory)
    url = f"{base(tenant)}/shipping"
    assert (await client.get(url, headers=owner)).json()["last_test_ok"] is None
    testado = await client.post(f"{url}/test", headers=owner)
    assert testado.status_code == 200 and testado.json()["last_test_ok"] is True
    assert (await client.get(url, headers=owner)).json()["last_test_ok"] is True


async def test_transportadora_nao_liberada_e_recusada(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, owner, _ = await loja(client, session_factory)
    resposta = await client.put(
        f"{base(tenant)}/shipping/provider", json={"provider": "melhorenvio"}, headers=owner
    )
    assert resposta.status_code == 422
    assert resposta.json()["error"]["details"]["reason"] == "provider_unavailable"


async def test_trocar_de_transportadora_limpa_os_servicos_e_o_pedido_segue_com_quem_cotou(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant, owner, me = await loja(client, session_factory)
    url = f"{base(tenant)}/shipping"
    escolhido = await client.put(
        f"{url}/services", json={"codes": ["fake_economico"]}, headers=owner
    )
    assert escolhido.status_code == 200, escolhido.text
    order_id = await pedido_pronto_para_despacho(client, session_factory, tenant, owner, me)

    monkeypatch.setattr(settings, "shipping_allowed_providers", "fake,melhorenvio")
    trocado = await client.put(f"{url}/provider", json={"provider": "melhorenvio"}, headers=owner)
    assert trocado.status_code == 200, trocado.text
    corpo = trocado.json()
    assert corpo["provider"] == "melhorenvio" and corpo["connected"] is False
    assert corpo["services"] == []  # os códigos de uma não existem na outra

    # O pedido foi cotado pela fake: despacha pela fake, não pela transportadora de agora.
    remessa = await _despachar(session_factory, tenant, order_id)
    assert remessa.provider == "fake" and remessa.status == "purchased"
