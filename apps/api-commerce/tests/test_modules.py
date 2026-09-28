"""Módulos que o lojista liga sozinho (Fase 2.1) e quem vê a vitrine (2.2).

O que estes testes protegem é a allowlist do servidor: a tela esconde o toggle dos módulos
pagos, mas quem tem que recusar o POST forjado é a API. Ligar Chatwoot sem passar pela loja de
serviços seria entregar R$ 100/mês de graça.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.tenancy.modules import SELF_SERVICE
from tests.shoppers import selling_store
from tests.test_catalog import member_headers


def base(tenant: Any) -> str:
    return f"/api/v1/admin/tenants/{tenant.id}"


async def loja(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], **flags: bool
) -> tuple[Any, dict[str, str]]:
    tenant = await selling_store(session_factory, flags=flags or None)
    return tenant, await member_headers(client, session_factory, tenant)


def by_key(payload: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {item["key"]: item for item in payload}


async def test_lista_mostra_rotulo_e_por_que_o_pago_esta_travado(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, owner = await loja(client, session_factory)
    resposta = await client.get(f"{base(tenant)}/modules", headers=owner)
    assert resposta.status_code == 200, resposta.text
    modulos = by_key(resposta.json())

    assert modulos["catalog"]["label"] == "Catálogo"
    assert modulos["catalog"]["self_service"] is True
    assert modulos["shipping.melhorenvio"]["self_service"] is True  # incluso nos R$ 100
    chatwoot = modulos["chatwoot"]
    assert chatwoot["self_service"] is False
    assert "conta" in (chatwoot["locked_reason"] or "")  # diz onde contratar


async def test_lojista_liga_modulo_que_nao_cobra(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, owner = await loja(client, session_factory, catalog=True, checkout=True)
    resposta = await client.put(
        f"{base(tenant)}/modules",
        json={"flags": {"shipping.melhorenvio": True, "coupons": True}},
        headers=owner,
    )
    assert resposta.status_code == 200, resposta.text
    modulos = by_key(resposta.json())
    assert modulos["shipping.melhorenvio"]["enabled"] is True
    assert modulos["coupons"]["enabled"] is True


async def test_modulo_pago_nao_passa_nem_por_post_forjado(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, owner = await loja(client, session_factory)
    for chave in ("chatwoot", "sales_agent", "whatsapp_owned", "storefront"):
        resposta = await client.put(
            f"{base(tenant)}/modules", json={"flags": {chave: True}}, headers=owner
        )
        assert resposta.status_code == 403, f"{chave}: {resposta.text}"
        assert resposta.json()["error"]["code"] == "module_not_self_service"


async def test_a_allowlist_nao_deixa_passar_flag_paga_por_engano() -> None:
    """Se alguém marcar `self_service=True` num módulo pago, este teste cai."""
    assert "chatwoot" not in SELF_SERVICE
    assert "sales_agent" not in SELF_SERVICE
    assert "whatsapp_owned" not in SELF_SERVICE
    assert "storefront" not in SELF_SERVICE
    assert "shipping.melhorenvio" in SELF_SERVICE


async def test_modulo_que_depende_de_outro_cobra_a_ordem(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    # `selling_store` já vem com o checkout ligado; aqui a loja é uma que ainda não vende.
    tenant, owner = await loja(client, session_factory, checkout=False, coupons=False)
    resposta = await client.put(
        f"{base(tenant)}/modules", json={"flags": {"coupons": True}}, headers=owner
    )
    assert resposta.status_code == 422, resposta.text
    corpo = resposta.json()["error"]
    assert corpo["code"] == "module_requires"
    assert corpo["details"]["requires"] == ["checkout"]


async def test_nao_desliga_modulo_de_que_outro_depende(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, owner = await loja(client, session_factory, catalog=True, checkout=True, coupons=True)
    resposta = await client.put(
        f"{base(tenant)}/modules", json={"flags": {"checkout": False}}, headers=owner
    )
    assert resposta.status_code == 409, resposta.text
    corpo = resposta.json()["error"]
    assert corpo["code"] == "module_in_use"
    assert "coupons" in corpo["details"]["dependents"]


async def test_dono_escolhe_quem_ve_a_vitrine(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, owner = await loja(client, session_factory)
    resposta = await client.put(
        f"{base(tenant)}/storefront/access", json={"access_mode": "public"}, headers=owner
    )
    assert resposta.status_code == 200, resposta.text
    assert resposta.json()["access_mode"] == "public"

    contexto = await client.get(f"{base(tenant)}/context", headers=owner)
    if contexto.status_code == 200:
        assert contexto.json()["settings"]["storefront"]["access_mode"] == "public"


async def test_modo_de_acesso_invalido_e_recusado(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, owner = await loja(client, session_factory)
    resposta = await client.put(
        f"{base(tenant)}/storefront/access", json={"access_mode": "aberto"}, headers=owner
    )
    assert resposta.status_code == 422


async def test_vitrine_publica_sem_login_e_estado_que_nao_vende(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Regressão da loja do Silvio: catálogo aberto, checkout ligado, login desligado.

    O carrinho é de uma pessoa; sem login não há a quem pertencer, e a loja ficava visível e
    inviável ao mesmo tempo. Agora o checkout cobra o login, e o login é self-service.
    """
    tenant, owner = await loja(client, session_factory, checkout=False, customer_login=False)
    recusa = await client.put(
        f"{base(tenant)}/modules", json={"flags": {"checkout": True}}, headers=owner
    )
    assert recusa.status_code == 422, recusa.text
    assert recusa.json()["error"]["details"]["requires"] == ["customer_login"]

    # E o lojista consegue resolver sozinho: o login não depende de ninguém da plataforma.
    ligou = await client.put(
        f"{base(tenant)}/modules", json={"flags": {"customer_login": True}}, headers=owner
    )
    assert ligou.status_code == 200, ligou.text
    agora = await client.put(
        f"{base(tenant)}/modules", json={"flags": {"checkout": True}}, headers=owner
    )
    assert agora.status_code == 200, agora.text
    assert by_key(agora.json())["checkout"]["enabled"] is True
