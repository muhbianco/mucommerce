"""O endereço da loja no painel do lojista: ele registra, vê o DNS e acompanha a ativação."""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.scopes import TenantRole
from app.tenancy.models import Tenant
from tests.conftest import create_tenant
from tests.test_catalog import base, member_headers


async def loja(session_factory: async_sessionmaker[AsyncSession], slug: str) -> Tenant:
    return await create_tenant(session_factory, slug)


async def test_o_lojista_registra_o_dominio_dele_e_ve_o_que_apontar(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant = await loja(session_factory, "padaria")
    headers = await member_headers(client, session_factory, tenant)

    listing = await client.get(f"{base(tenant)}/domains", headers=headers)
    assert listing.status_code == 200, listing.text
    platform = listing.json()[0]
    assert platform["hostname"] == f"{tenant.slug}.loja.test"
    assert platform["instructions"] is None  # endereço da plataforma não pede DNS de ninguém

    created = await client.post(
        f"{base(tenant)}/domains", json={"hostname": "Loja.Padaria.com.br"}, headers=headers
    )
    assert created.status_code == 201, created.text
    domain = created.json()
    assert domain["hostname"] == "loja.padaria.com.br"
    assert domain["kind"] == "custom_subdomain"
    assert domain["status"] == "pending_dns"
    assert domain["role"] == "alias"  # nunca nasce principal: ainda não responde
    assert domain["instructions"]["txt_name"] == "_muhbianco-verify.loja.padaria.com.br"
    assert domain["instructions"]["txt_value"].startswith("mb-verify=")
    assert domain["instructions"]["cname_target"]

    # Enquanto o DNS não entra, a vitrine segue respondendo no endereço da plataforma.
    not_yet = await client.get(
        "/api/v1/storefront/context", headers={"host": "loja.padaria.com.br"}
    )
    assert not_yet.status_code == 404
    serving = await client.get(
        "/api/v1/storefront/context", headers={"host": f"{tenant.slug}.loja.test"}
    )
    assert serving.status_code == 200


async def test_o_endereco_da_plataforma_nao_sai_do_ar(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant = await loja(session_factory, "quitanda")
    headers = await member_headers(client, session_factory, tenant)
    listing = await client.get(f"{base(tenant)}/domains", headers=headers)
    platform_id = listing.json()[0]["id"]

    refused = await client.delete(f"{base(tenant)}/domains/{platform_id}", headers=headers)
    assert refused.status_code == 422
    assert "reserva" in refused.json()["error"]["message"]


async def test_o_numero_de_enderecos_proprios_tem_teto(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant = await loja(session_factory, "confeitaria")
    headers = await member_headers(client, session_factory, tenant)
    for n in range(3):
        created = await client.post(
            f"{base(tenant)}/domains", json={"hostname": f"loja{n}.padaria.com.br"}, headers=headers
        )
        assert created.status_code == 201, created.text

    excess = await client.post(
        f"{base(tenant)}/domains", json={"hostname": "loja3.padaria.com.br"}, headers=headers
    )
    assert excess.status_code == 422
    assert "3 endereços" in excess.json()["error"]["message"]

    # Desativar um abre espaço de novo.
    listing = await client.get(f"{base(tenant)}/domains", headers=headers)
    custom = next(d for d in listing.json() if d["kind"] != "platform_subdomain")
    assert (
        await client.delete(f"{base(tenant)}/domains/{custom['id']}", headers=headers)
    ).status_code == 200

    # O host desativado continua sendo desta loja: ninguém o registra por cima.
    assert (
        await client.post(
            f"{base(tenant)}/domains", json={"hostname": custom["hostname"]}, headers=headers
        )
    ).status_code == 409

    again = await client.post(
        f"{base(tenant)}/domains", json={"hostname": "loja3.padaria.com.br"}, headers=headers
    )
    assert again.status_code == 201


async def test_dominio_de_outra_loja_e_papel_sem_permissao(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant = await loja(session_factory, "mercearia")
    owner = await member_headers(client, session_factory, tenant)
    assert (
        await client.post(
            f"{base(tenant)}/domains", json={"hostname": "loja.disputada.com.br"}, headers=owner
        )
    ).status_code == 201

    # Equipe de apoio não mexe em endereço.
    support = await member_headers(client, session_factory, tenant, role=TenantRole.SUPPORT)
    denied = await client.post(
        f"{base(tenant)}/domains", json={"hostname": "outra.padaria.com.br"}, headers=support
    )
    assert denied.status_code == 403

    # E o host já registrado por alguém não é roubado.
    clash = await client.post(
        f"{base(tenant)}/domains", json={"hostname": "loja.disputada.com.br"}, headers=owner
    )
    assert clash.status_code == 409
