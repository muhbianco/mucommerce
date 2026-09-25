"""A store bought in the MuhBianco catalog: reserve → activate, or release when the debit fails."""

from __future__ import annotations

import uuid
from datetime import timedelta

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.scopes import TenantRole
from app.identity.models import AdminUser, TenantMembership
from app.models.base import utcnow
from app.provisioning.service import ORPHAN_RESERVATION_AGE, StoreProvisioningService
from app.tenancy.context import CROSS_TENANT_OPTION
from app.tenancy.models import Tenant, TenantStatus
from app.tenancy.service import Actor, TenantService

AGENTS = {"X-Internal-Token": "agents-token-test"}
BASE = "/api/v1/internal/provisioning/stores"


def purchase(**overrides: str) -> dict[str, str]:
    body = {
        "subscription_ref": f"sub-{uuid.uuid4()}",
        "account_id": str(uuid.uuid4()),
        "email": "ocimar@exemplo.test",
        "full_name": "Ocimar Ferreira",
        "slug": "padaria-do-sol",
        "name": "Padaria do Sol",
    }
    body.update(overrides)
    return body


async def tenant_by_ref(session_factory: async_sessionmaker[AsyncSession], ref: str) -> Tenant:
    async with session_factory() as session:
        stmt = (
            select(Tenant)
            .where(Tenant.subscription_ref == ref)
            .execution_options(**{CROSS_TENANT_OPTION: True})
        )
        return (await session.execute(stmt)).scalars().one()


async def test_reserve_then_activate_puts_the_store_in_the_air(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    body = purchase()
    reserved = await client.post(f"{BASE}/reserve", json=body, headers=AGENTS)
    assert reserved.status_code == 201, reserved.text
    store = reserved.json()
    assert store["status"] == "draft"  # nothing is served before the debit
    assert store["slug"] == "padaria-do-sol"
    assert store["storefront_url"].startswith("https://padaria-do-sol.")
    assert store["panel_url"].endswith(f"/t/{store['tenant_id']}")

    # The buyer is the owner, even though they never signed into the panel.
    async with session_factory() as session:
        owner = (
            (
                await session.execute(
                    select(AdminUser).where(AdminUser.external_account_id == body["account_id"])
                )
            )
            .scalars()
            .one()
        )
        membership = (
            (
                await session.execute(
                    select(TenantMembership).where(TenantMembership.tenant_id == store["tenant_id"])
                )
            )
            .scalars()
            .one()
        )
        assert owner.email == "ocimar@exemplo.test"
        assert membership.admin_user_id == owner.id
        assert membership.role == TenantRole.OWNER

    activated = await client.post(f"{BASE}/{body['subscription_ref']}/activate", headers=AGENTS)
    assert activated.status_code == 200, activated.text
    assert activated.json()["status"] == "active"

    # Both calls are idempotent: the catalog may retry either one.
    again = await client.post(f"{BASE}/reserve", json=body, headers=AGENTS)
    assert again.json()["tenant_id"] == store["tenant_id"]
    assert (
        await client.post(f"{BASE}/{body['subscription_ref']}/activate", headers=AGENTS)
    ).json()["status"] == "active"

    async with session_factory() as session:
        count = await session.scalar(
            select(Tenant.id)
            .where(Tenant.slug == "padaria-do-sol")
            .execution_options(**{CROSS_TENANT_OPTION: True})
        )
        assert count is not None


async def test_taken_slug_is_refused_before_anyone_is_charged(client: AsyncClient) -> None:
    first = await client.post(f"{BASE}/reserve", json=purchase(), headers=AGENTS)
    assert first.status_code == 201
    clash = await client.post(f"{BASE}/reserve", json=purchase(), headers=AGENTS)
    assert clash.status_code == 409
    assert clash.json()["error"]["code"] == "conflict"

    reserved_word = await client.post(
        f"{BASE}/reserve", json=purchase(slug="painel"), headers=AGENTS
    )
    assert reserved_word.status_code == 422


async def test_release_frees_the_slug_when_the_debit_fails(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    body = purchase()
    await client.post(f"{BASE}/reserve", json=body, headers=AGENTS)

    released = await client.request("DELETE", f"{BASE}/{body['subscription_ref']}", headers=AGENTS)
    assert released.status_code == 200, released.text
    assert released.json()["status"] == "archived"
    assert released.json()["slug"] != "padaria-do-sol"

    # The same person, buying again after topping up, takes the address back.
    retry = await client.post(
        f"{BASE}/reserve",
        json=purchase(account_id=body["account_id"], email=body["email"]),
        headers=AGENTS,
    )
    assert retry.status_code == 201
    assert retry.json()["slug"] == "padaria-do-sol"

    # An activated store is never released by this path.
    await client.post(f"{BASE}/{retry.json()['subscription_ref']}/activate", headers=AGENTS)
    refused = await client.request(
        "DELETE", f"{BASE}/{retry.json()['subscription_ref']}", headers=AGENTS
    )
    assert refused.status_code == 409


async def test_suspension_serves_the_storefront_until_the_grace_deadline(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    body = purchase(slug="loja-carencia")
    await client.post(f"{BASE}/reserve", json=body, headers=AGENTS)
    await client.post(f"{BASE}/{body['subscription_ref']}/activate", headers=AGENTS)
    host = {"X-Internal-Token": "web-token-test", "X-Tenant-Host": "loja-carencia.loja.test"}
    assert (
        await client.get("/api/v1/internal/storefront/context", headers=host)
    ).status_code == 200

    grace = utcnow() + timedelta(days=3)
    suspended = await client.post(
        f"{BASE}/{body['subscription_ref']}/billing",
        json={"state": "suspended", "grace_until": grace.isoformat(), "reason": "saldo"},
        headers=AGENTS,
    )
    assert suspended.status_code == 200, suspended.text
    assert suspended.json()["status"] == "suspended"
    still_up = await client.get("/api/v1/internal/storefront/context", headers=host)
    assert still_up.status_code == 200  # 3 days to top up

    past = await client.post(
        f"{BASE}/{body['subscription_ref']}/billing",
        json={"state": "suspended", "grace_until": (utcnow() - timedelta(minutes=1)).isoformat()},
        headers=AGENTS,
    )
    assert past.status_code == 200
    down = await client.get("/api/v1/internal/storefront/context", headers=host)
    assert down.status_code == 503
    assert down.json()["error"]["code"] == "tenant_suspended"

    # O painel do lojista precisa saber o prazo para avisar antes de a vitrine cair.
    async with session_factory() as session:
        tenant = (
            (
                await session.execute(
                    select(Tenant)
                    .where(Tenant.subscription_ref == body["subscription_ref"])
                    .execution_options(**{CROSS_TENANT_OPTION: True})
                )
            )
            .scalars()
            .one()
        )
        assert tenant.billing_grace_until is not None

    back = await client.post(
        f"{BASE}/{body['subscription_ref']}/billing", json={"state": "active"}, headers=AGENTS
    )
    assert back.json()["status"] == "active"
    assert back.json()["billing_grace_until"] is None
    assert (
        await client.get("/api/v1/internal/storefront/context", headers=host)
    ).status_code == 200


async def test_sweep_releases_reservations_the_catalog_forgot(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    fresh = purchase(slug="loja-nova")
    old = purchase(slug="loja-esquecida", email="outro@exemplo.test")
    await client.post(f"{BASE}/reserve", json=fresh, headers=AGENTS)
    await client.post(f"{BASE}/reserve", json=old, headers=AGENTS)

    stale = await tenant_by_ref(session_factory, old["subscription_ref"])
    async with session_factory() as session:
        tenant = await session.get(Tenant, stale.id)
        assert tenant is not None
        tenant.created_at = utcnow() - ORPHAN_RESERVATION_AGE - timedelta(minutes=1)
        await session.commit()

    async with session_factory() as session:
        released = await StoreProvisioningService(session).sweep_orphan_reservations()
        await session.commit()
    assert released == [stale.id]

    async with session_factory() as session:
        swept = await session.get(Tenant, stale.id)
        assert swept is not None
        assert swept.status == TenantStatus.ARCHIVED
        assert swept.subscription_ref is None
        assert swept.slug != "loja-esquecida"
    kept = await tenant_by_ref(session_factory, fresh["subscription_ref"])
    assert kept.status == TenantStatus.DRAFT


async def test_provisioning_needs_the_agents_token(client: AsyncClient) -> None:
    body = purchase()
    for headers in ({}, {"X-Internal-Token": "web-token-test"}):
        response = await client.post(f"{BASE}/reserve", json=body, headers=headers)
        assert response.status_code == 401, response.text
        assert response.json()["error"]["code"] == "authentication_failed"


async def test_dominio_do_cliente_entra_na_reserva_com_as_instrucoes_de_dns(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    body = purchase(slug="padaria-loja", custom_domain="loja.padaria.com.br")
    response = await client.post(f"{BASE}/reserve", json=body, headers=AGENTS)
    assert response.status_code == 201, response.text
    store = response.json()

    # O endereço da plataforma continua existindo: é por ele que a loja responde enquanto o
    # cliente não aponta o DNS.
    assert store["platform_host"] == "padaria-loja.loja.test"
    custom = store["custom_domain"]
    assert custom["hostname"] == "loja.padaria.com.br"
    assert custom["status"] == "pending_dns"
    assert custom["role"] == "alias"  # só vira principal depois de ativo
    assert custom["txt_name"] == "_muhbianco-verify.loja.padaria.com.br"
    assert custom["txt_value"].startswith("mb-verify=")
    assert custom["cname_target"]
    assert custom["apex"] is False
    assert {d["hostname"] for d in store["domains"]} == {
        "padaria-loja.loja.test",
        "loja.padaria.com.br",
    }

    # Reenviar a mesma compra não duplica o domínio.
    again = await client.post(f"{BASE}/reserve", json=body, headers=AGENTS)
    assert again.status_code == 201
    assert len(again.json()["domains"]) == 2

    # E o estado continua visível depois, para a fila de avisos acompanhar.
    state = await client.get(f"{BASE}/{body['subscription_ref']}", headers=AGENTS)
    assert state.json()["custom_domain"]["status"] == "pending_dns"


async def test_dominio_raiz_pede_os_ips_da_edge(client: AsyncClient) -> None:
    body = purchase(slug="padaria-apex", custom_domain="Padaria.com.BR")
    response = await client.post(f"{BASE}/reserve", json=body, headers=AGENTS)
    assert response.status_code == 201, response.text
    custom = response.json()["custom_domain"]
    assert custom["hostname"] == "padaria.com.br"  # normalizado
    assert custom["apex"] is True
    assert custom["a_records"] == settings.edge_public_ip_list


async def test_dominio_de_outra_loja_recusa_antes_de_cobrar(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    first = purchase(slug="loja-um", custom_domain="loja.disputada.com.br")
    assert (await client.post(f"{BASE}/reserve", json=first, headers=AGENTS)).status_code == 201

    second = purchase(
        slug="loja-dois",
        custom_domain="loja.disputada.com.br",
        email="outro@exemplo.test",
    )
    clash = await client.post(f"{BASE}/reserve", json=second, headers=AGENTS)
    assert clash.status_code == 409, clash.text
    # A loja do segundo comprador não fica meio criada.
    assert (await client.get(f"{BASE}/{second['subscription_ref']}", headers=AGENTS)).json() is None

    reserved_word = await client.post(
        f"{BASE}/reserve",
        json=purchase(slug="loja-tres", custom_domain="painel.muhbianco.com.br"),
        headers=AGENTS,
    )
    assert reserved_word.status_code in (409, 422)


async def test_liberar_a_reserva_devolve_o_dominio_do_cliente(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    body = purchase(slug="loja-efemera", custom_domain="loja.efemera.com.br")
    await client.post(f"{BASE}/reserve", json=body, headers=AGENTS)
    released = await client.request("DELETE", f"{BASE}/{body['subscription_ref']}", headers=AGENTS)
    assert released.status_code == 200
    assert released.json()["custom_domain"] is None

    # O endereço volta para o pool: outra pessoa consegue usar.
    retry = await client.post(
        f"{BASE}/reserve",
        json=purchase(
            slug="loja-nova-dona",
            custom_domain="loja.efemera.com.br",
            email="nova.dona@exemplo.test",
        ),
        headers=AGENTS,
    )
    assert retry.status_code == 201, retry.text
    assert retry.json()["custom_domain"]["hostname"] == "loja.efemera.com.br"


async def test_dominio_de_loja_arquivada_volta_para_o_pool(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Loja arquivada não pode segurar o domínio do cliente e impedi-lo de contratar de novo."""
    antiga = purchase(slug="loja-antiga", custom_domain="loja.recomprada.com.br")
    assert (await client.post(f"{BASE}/reserve", json=antiga, headers=AGENTS)).status_code == 201
    await client.post(f"{BASE}/{antiga['subscription_ref']}/activate", headers=AGENTS)

    tenant = await tenant_by_ref(session_factory, antiga["subscription_ref"])
    async with session_factory() as session:
        service = TenantService(session)
        linha = await service.get_or_404(tenant.id)
        await service.set_status(linha, TenantStatus.SUSPENDED, Actor.system("t"))
        await service.set_status(linha, TenantStatus.ARCHIVED, Actor.system("t"))
        await session.commit()

    # Com a loja arquivada, o mesmo endereço pode ser contratado outra vez.
    nova = purchase(
        slug="loja-nova-mesma-marca",
        custom_domain="loja.recomprada.com.br",
        email="outra.pessoa@exemplo.test",
    )
    resposta = await client.post(f"{BASE}/reserve", json=nova, headers=AGENTS)
    assert resposta.status_code == 201, resposta.text
    assert resposta.json()["custom_domain"]["hostname"] == "loja.recomprada.com.br"

    # Já o endereço da plataforma continua parado com a loja arquivada: esse é nosso.
    async with session_factory() as session:
        arquivada = await session.get(Tenant, tenant.id)
        assert arquivada is not None and arquivada.status == TenantStatus.ARCHIVED
