"""Painel por loja (ADR 0014): <slug>.painel.* nasce com a loja, vira router do edge, e só
responde como painel daquela loja. painel.muhbianco.com.br fica para a equipe."""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.tenancy.context import CROSS_TENANT_OPTION
from app.tenancy.edge import build_traefik_config
from app.tenancy.models import (
    DomainKind,
    DomainPurpose,
    DomainRole,
    DomainStatus,
    TenantDomain,
    TenantStatus,
)
from app.tenancy.service import Actor, TenantService
from tests.conftest import create_tenant
from tests.test_store_provisioning import AGENTS, BASE, purchase

WEB = {"X-Internal-Token": "web-token-test"}
ACTOR = Actor.system("test")


async def _panel_domain(
    session_factory: async_sessionmaker[AsyncSession], tenant_id: str
) -> TenantDomain:
    async with session_factory() as session:
        stmt = (
            select(TenantDomain)
            .where(TenantDomain.tenant_id == tenant_id)
            .where(TenantDomain.purpose == DomainPurpose.PANEL)
            .execution_options(**{CROSS_TENANT_OPTION: True})
        )
        return (await session.execute(stmt)).scalars().one()


async def test_loja_nasce_com_o_painel_dela(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tenant = await create_tenant(session_factory, "padaria")
    panel = await _panel_domain(session_factory, tenant.id)
    assert panel.hostname == "padaria.painel.test"
    assert panel.kind == DomainKind.PLATFORM_SUBDOMAIN
    assert panel.role == DomainRole.PRIMARY
    assert panel.status == DomainStatus.ACTIVE


async def test_web_descobre_a_loja_pelo_host_do_painel(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant = await create_tenant(session_factory, "padaria")
    found = await client.get(
        "/api/v1/internal/panel/context",
        headers={**WEB, "X-Tenant-Host": "Padaria.Painel.Test."},
    )
    assert found.status_code == 200, found.text
    assert found.json() == {
        "tenant_id": tenant.id,
        "slug": "padaria",
        "name": tenant.name,
        "host": "padaria.painel.test",
    }

    # A vitrine da loja não é painel, e sem o token do web não há resposta.
    store_host = await client.get(
        "/api/v1/internal/panel/context", headers={**WEB, "X-Tenant-Host": "padaria.loja.test"}
    )
    assert store_host.status_code == 404
    no_token = await client.get(
        "/api/v1/internal/panel/context", headers={"X-Tenant-Host": "padaria.painel.test"}
    )
    assert no_token.status_code in {401, 403}


async def test_loja_arquivada_perde_o_painel(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant = await create_tenant(session_factory, "padaria")
    async with session_factory() as session:
        service = TenantService(session)
        row = await service.get_or_404(tenant.id)
        row.status = TenantStatus.ARCHIVED  # o caminho de arquivar não importa aqui
        await session.commit()
    resp = await client.get(
        "/api/v1/internal/panel/context", headers={**WEB, "X-Tenant-Host": "padaria.painel.test"}
    )
    assert resp.status_code == 404


async def test_login_da_conta_muhbianco_confere_o_host_do_painel(client: AsyncClient) -> None:
    body = purchase(slug="padaria")
    assert (await client.post(f"{BASE}/reserve", json=body, headers=AGENTS)).status_code == 201
    ok = await client.get(f"{BASE}/panel-hosts/padaria.painel.test", headers=AGENTS)
    assert ok.status_code == 200, ok.text
    assert ok.json()["slug"] == "padaria"
    assert (
        await client.get(f"{BASE}/panel-hosts/evil.example.com", headers=AGENTS)
    ).status_code == 404
    assert (await client.get(f"{BASE}/panel-hosts/padaria.painel.test")).status_code in {401, 403}


async def test_ninguem_registra_um_host_da_zona_do_painel(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tenant = await create_tenant(session_factory, "padaria")
    async with session_factory() as session:
        service = TenantService(session)
        row = await service.get_or_404(tenant.id)
        try:
            await service.register_domain(
                row,
                hostname="outra.painel.test",
                purpose=DomainPurpose.STOREFRONT,
                role=DomainRole.ALIAS,
                actor=ACTOR,
            )
        except Exception as exc:
            assert "reservado" in str(exc).lower()
        else:
            raise AssertionError("host da zona do painel foi aceito")


async def test_painel_da_plataforma_nao_se_desativa(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tenant = await create_tenant(session_factory, "padaria")
    panel = await _panel_domain(session_factory, tenant.id)
    async with session_factory() as session:
        service = TenantService(session)
        row = await service.get_or_404(tenant.id)
        domain = await service.repo.get_domain(tenant.id, panel.id)
        assert domain is not None
        try:
            await service.disable_domain(row, domain, ACTOR)
        except Exception as exc:
            assert "painel" in str(exc).lower()
        else:
            raise AssertionError("o endereço do painel saiu")


async def test_dominio_proprio_de_painel_entra_pelo_fluxo_de_dns(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tenant = await create_tenant(session_factory, "padaria")
    async with session_factory() as session:
        service = TenantService(session)
        row = await service.get_or_404(tenant.id)
        domain, instructions = await service.register_domain(
            row,
            hostname="painel.padaria.com.br",
            purpose=DomainPurpose.PANEL,
            role=DomainRole.ALIAS,
            actor=ACTOR,
        )
        await session.commit()
    assert domain.status == DomainStatus.PENDING_DNS
    assert instructions.txt_name == "_muhbianco-verify.painel.padaria.com.br"


def test_edge_manda_o_painel_da_loja_so_para_o_web() -> None:
    tenant = "0192a1b2-0000-7000-8000-000000000001"
    domains = [
        TenantDomain(
            tenant_id=tenant,
            hostname="padaria.painel.muhbianco.com.br",
            kind="platform_subdomain",
            purpose=DomainPurpose.PANEL,
            role=DomainRole.PRIMARY,
            status=DomainStatus.ACTIVE,
        ),
        TenantDomain(
            tenant_id=tenant,
            hostname="painel.padaria.com.br",
            kind="custom_subdomain",
            purpose=DomainPurpose.PANEL,
            role=DomainRole.ALIAS,
            status=DomainStatus.ACTIVE,
        ),
    ]
    routers = build_traefik_config(domains)["http"]["routers"]
    assert len(routers) == 2
    for name, router in routers.items():
        assert name.endswith("-panel")
        assert router["service"] == "commerce-web"
        assert router["rule"].startswith("Host(`") and "PathPrefix" not in router["rule"]
        assert "middlewares" not in router  # alias de painel não redireciona para a vitrine
