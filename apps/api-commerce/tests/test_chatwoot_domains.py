"""Painel do Chatwoot no endereço do cliente (ADR 0013): provisionamento e rota no edge."""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.tenancy.edge import build_traefik_config
from app.tenancy.models import DomainPurpose, DomainRole, DomainStatus, TenantDomain
from tests.test_store_provisioning import AGENTS, BASE, purchase


async def _active_store(client: AsyncClient, **overrides: str) -> str:
    body = purchase(**overrides)
    reserved = await client.post(f"{BASE}/reserve", json=body, headers=AGENTS)
    assert reserved.status_code == 201, reserved.text
    ref = body["subscription_ref"]
    activated = await client.post(f"{BASE}/{ref}/activate", headers=AGENTS)
    assert activated.status_code == 200, activated.text
    return ref


async def test_ligar_o_chatwoot_da_o_endereco_da_plataforma_na_hora(client: AsyncClient) -> None:
    ref = await _active_store(client, slug="lunares")
    resp = await client.post(f"{BASE}/{ref}/chatwoot", json={}, headers=AGENTS)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["platform_host"] == "lunares.chatwoot.muhbianco.com.br"
    assert data["dashboard_url"] == "https://lunares.chatwoot.muhbianco.com.br"
    assert data["custom_domain"] is None
    assert data["hosts"] == ["lunares.chatwoot.muhbianco.com.br"]

    again = await client.post(f"{BASE}/{ref}/chatwoot", json={}, headers=AGENTS)
    assert again.json()["hosts"] == ["lunares.chatwoot.muhbianco.com.br"]


async def test_dominio_proprio_entra_com_as_instrucoes_de_dns(client: AsyncClient) -> None:
    ref = await _active_store(client, slug="lunares")
    resp = await client.post(
        f"{BASE}/{ref}/chatwoot",
        json={"custom_domain": "Chatwoot.Lunares.com.br."},
        headers=AGENTS,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    custom = data["custom_domain"]
    assert custom["hostname"] == "chatwoot.lunares.com.br"
    assert custom["status"] == DomainStatus.PENDING_DNS
    assert custom["txt_name"] == "_muhbianco-verify.chatwoot.lunares.com.br"
    assert custom["cname_target"] == settings.edge_cname_target
    assert data["hosts"] == ["chatwoot.lunares.com.br", "lunares.chatwoot.muhbianco.com.br"]


async def test_trocar_o_dominio_desliga_o_anterior(client: AsyncClient) -> None:
    ref = await _active_store(client, slug="lunares")
    await client.post(
        f"{BASE}/{ref}/chatwoot", json={"custom_domain": "chatwoot.lunares.com.br"}, headers=AGENTS
    )
    resp = await client.post(
        f"{BASE}/{ref}/chatwoot",
        json={"custom_domain": "atendimento.lunares.com.br"},
        headers=AGENTS,
    )
    assert resp.json()["hosts"] == [
        "atendimento.lunares.com.br",
        "lunares.chatwoot.muhbianco.com.br",
    ]


async def test_endereco_da_plataforma_nao_se_registra_a_mao(client: AsyncClient) -> None:
    ref = await _active_store(client, slug="lunares")
    resp = await client.post(
        f"{BASE}/{ref}/chatwoot",
        json={"custom_domain": "outra-loja.chatwoot.muhbianco.com.br"},
        headers=AGENTS,
    )
    assert resp.status_code == 422, resp.text


async def test_chatwoot_so_liga_com_a_loja_no_ar(client: AsyncClient) -> None:
    body = purchase(slug="lunares")
    await client.post(f"{BASE}/reserve", json=body, headers=AGENTS)
    resp = await client.post(f"{BASE}/{body['subscription_ref']}/chatwoot", json={}, headers=AGENTS)
    assert resp.status_code == 409, resp.text


async def test_desligar_tira_os_hosts_do_ar(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    ref = await _active_store(client, slug="lunares")
    await client.post(
        f"{BASE}/{ref}/chatwoot", json={"custom_domain": "chatwoot.lunares.com.br"}, headers=AGENTS
    )
    off = await client.delete(f"{BASE}/{ref}/chatwoot", headers=AGENTS)
    assert off.status_code == 200, off.text
    assert off.json()["hosts"] == []
    state = await client.get(f"{BASE}/{ref}/chatwoot", headers=AGENTS)
    assert state.json()["hosts"] == []

    # Religar volta o endereço da plataforma na hora e o domínio próprio para verificação.
    on = await client.post(
        f"{BASE}/{ref}/chatwoot", json={"custom_domain": "chatwoot.lunares.com.br"}, headers=AGENTS
    )
    assert on.json()["hosts"] == ["chatwoot.lunares.com.br", "lunares.chatwoot.muhbianco.com.br"]
    assert on.json()["custom_domain"]["status"] == DomainStatus.PENDING_DNS


async def test_chatwoot_precisa_do_token_dos_agentes(client: AsyncClient) -> None:
    resp = await client.post(f"{BASE}/sub-qualquer-coisa/chatwoot", json={})
    assert resp.status_code in {401, 403}


def test_edge_manda_o_host_para_o_chatwoot_sem_a_administracao() -> None:
    tenant = "0192a1b2-0000-7000-8000-000000000001"
    domains = [
        TenantDomain(
            tenant_id=tenant,
            hostname="lunares.chatwoot.muhbianco.com.br",
            kind="platform_subdomain",
            purpose=DomainPurpose.CHATWOOT,
            role=DomainRole.PRIMARY,
            status=DomainStatus.ACTIVE,
        ),
        TenantDomain(
            tenant_id=tenant,
            hostname="chatwoot.lunares.com.br",
            kind="custom_subdomain",
            purpose=DomainPurpose.CHATWOOT,
            role=DomainRole.ALIAS,
            status=DomainStatus.ACTIVE,
        ),
    ]
    http = build_traefik_config(domains)["http"]
    routers = {k: v for k, v in http["routers"].items() if k.endswith("-chatwoot")}
    assert len(routers) == 2
    for router in routers.values():
        assert router["service"] == "chatwoot"
        assert router["middlewares"] == ["chatwoot-headers"]
        assert router["tls"] == {"certResolver": "letsencryptresolver"}
        assert "!PathRegexp(`^/(super_admin|platform|sidekiq|monitoring)`)" in router["rule"]
    assert http["services"]["chatwoot"]["loadBalancer"]["servers"] == [
        {"url": "http://chatwoot_rails:3000"}
    ]
    assert (
        http["middlewares"]["chatwoot-headers"]["headers"]["referrerPolicy"]
        == "strict-origin-when-cross-origin"
    )
    # Host de Chatwoot não vira vitrine.
    assert not any(k.endswith("-web") or k.endswith("-api") for k in http["routers"])


def test_edge_sem_chatwoot_nao_declara_o_servico() -> None:
    assert "chatwoot" not in build_traefik_config([])["http"]["services"]
