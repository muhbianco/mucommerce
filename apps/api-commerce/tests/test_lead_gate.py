"""Robô de vendas só para cliente liberado, na loja fechada (docs/14-pedidos-lunares.md §5).

O número de quem escreve é a chave: lead novo vira cliente pendente e o robô fica quieto; o
lojista libera a pessoa (em Clientes ou no pedido) e a próxima mensagem já cai no robô. O WhatsApp
do checkout liga o número à pessoa, para quem comprou pela senha da loja também poder ser liberado.
"""

# ruff: noqa: F811
from __future__ import annotations

from typing import Any

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.customers.models import CustomerPhone
from app.customers.phones import remember_phone
from app.identity.models import AccessStatus, Customer, CustomerTenantAccess
from app.tenancy.context import CROSS_TENANT_OPTION, bind_session_tenant
from app.tenancy.models import Tenant
from app.tenancy.service import Actor, TenantService
from tests.shoppers import signed_in
from tests.test_checkout_place import order_body, place, ready_cart, shop  # noqa: F401
from tests.test_internal_agent import AGENT, account_of
from tests.test_pricing import product
from tests.test_storefront_catalog import set_stock

CROSS = {CROSS_TENANT_OPTION: True}
GATE = f"{AGENT}/sales/leads/gate"
FONE = "11955551234"


async def fechar(session_factory: async_sessionmaker[AsyncSession], tenant: Tenant) -> None:
    async with session_factory() as session:
        service = TenantService(session)
        await service.set_setting(
            await service.get_or_404(tenant.id),
            "storefront",
            {"access_mode": "whitelist"},
            Actor.system("tests"),
        )
        await session.commit()


async def gate(client: AsyncClient, dono: dict[str, str], telefone: str = FONE) -> Any:
    resposta = await client.post(GATE, json={"telefone": telefone, "nome": "Lia"}, headers=dono)
    assert resposta.status_code == 200, resposta.text
    return resposta.json()


def panel(tenant: Tenant) -> str:
    return f"/api/v1/admin/tenants/{tenant.id}/customers"


async def test_loja_aberta_o_robo_responde_todo_mundo(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], shop: Any
) -> None:
    tenant, _, _ = shop
    dono = await account_of(session_factory, tenant)
    assert await gate(client, dono) == {"responder": True, "motivo": "public", "cliente_id": None}


async def test_lead_novo_cai_para_humano_ate_o_lojista_liberar(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], shop: Any
) -> None:
    tenant, owner, _ = shop
    await fechar(session_factory, tenant)
    dono = await account_of(session_factory, tenant)

    novo = await gate(client, dono)
    assert novo["responder"] is False and novo["motivo"] == "new"
    de_novo = await gate(client, dono, "+55 (11) 95555-1234")
    assert de_novo == {"responder": False, "motivo": "pending", "cliente_id": novo["cliente_id"]}

    # Aparece em Clientes → Pendentes, com o nome do WhatsApp e o telefone.
    pendentes = (await client.get(f"{panel(tenant)}?status=pending", headers=owner)).json()
    [lead] = pendentes["items"]
    assert lead["customer_id"] == novo["cliente_id"]
    assert lead["name"] == "Lia" and lead["phone_e164"] == f"55{FONE}"

    liberado = await client.post(
        f"{panel(tenant)}/{novo['cliente_id']}/access", json={"status": "approved"}, headers=owner
    )
    assert liberado.status_code == 200, liberado.text
    assert (await gate(client, dono))["responder"] is True

    bloqueado = await client.post(
        f"{panel(tenant)}/{novo['cliente_id']}/access",
        json={"status": "blocked", "note": "trote"},
        headers=owner,
    )
    assert bloqueado.status_code == 200
    assert (await gate(client, dono)) == {
        "responder": False,
        "motivo": "blocked",
        "cliente_id": novo["cliente_id"],
    }


async def test_quem_comprou_pela_senha_e_liberado_pelo_whatsapp_do_checkout(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], shop: Any
) -> None:
    tenant, owner, _ = shop
    dono = await account_of(session_factory, tenant)
    # Compra na loja ainda aberta, deixando o WhatsApp no checkout (o caminho de quem entrou
    # pela senha: tem sessão, não tem linha de acesso).
    token = await signed_in(session_factory, tenant, access=None)
    me = {"X-Internal-Token": "web-token-test", "X-Tenant-Host": f"{tenant.slug}.loja.test"}
    me["X-Customer-Session"] = token
    brownie = await product(client, session_factory, tenant, owner)
    variante = brownie["variants"][0]["id"]
    await set_stock(session_factory, variante, 5)
    cart = await ready_cart(client, me, variante)
    placed = await place(
        client, me, order_body(cart, contact={"name": "Lia", "phone": "(11) 95555-1234"})
    )
    assert placed.status_code == 201, placed.text
    customer_id = placed.json().get("customer_id")

    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        [linha] = (await session.execute(select(CustomerPhone))).scalars().all()
    assert linha.source == "checkout" and linha.phone_e164 == f"55{FONE}"
    customer_id = customer_id or linha.customer_id

    await fechar(session_factory, tenant)
    assert (await gate(client, dono))["motivo"] == "pending"  # a pessoa do checkout, pendente
    liberado = await client.post(
        f"{panel(tenant)}/{customer_id}/access", json={"status": "approved"}, headers=owner
    )
    assert liberado.status_code == 200, liberado.text
    assert (await gate(client, dono)) == {
        "responder": True,
        "motivo": "approved",
        "cliente_id": customer_id,
    }


async def test_liberar_sem_passagem_pela_loja_nao_existe(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], shop: Any
) -> None:
    tenant, owner, _ = shop
    async with session_factory() as session:
        estranho = Customer(email_normalized="estranho@example.com", status="active")
        session.add(estranho)
        await session.commit()
    resposta = await client.post(
        f"{panel(tenant)}/{estranho.id}/access", json={"status": "approved"}, headers=owner
    )
    assert resposta.status_code == 404

    # Com sessão na loja (entrou pela senha e nunca pediu acesso), liberar cria a linha.
    token_customer = await signed_in(session_factory, tenant, access=None)
    del token_customer
    async with session_factory() as session:
        sem_linha = (
            await session.execute(
                select(Customer.id)
                .where(
                    ~Customer.id.in_(
                        select(CustomerTenantAccess.customer_id).execution_options(**CROSS)
                    )
                )
                .where(Customer.id != estranho.id)
                .execution_options(**CROSS)
            )
        ).scalar_one()
    criado = await client.post(
        f"{panel(tenant)}/{sem_linha}/access", json={"status": "approved"}, headers=owner
    )
    assert criado.status_code == 200, criado.text
    assert criado.json()["status"] == AccessStatus.APPROVED


async def test_o_mapa_de_telefones_e_por_loja(
    session_factory: async_sessionmaker[AsyncSession], shop: Any
) -> None:
    tenant, _, _ = shop
    async with session_factory() as session:
        pessoa = Customer(phone_e164=f"55{FONE}", status="active")
        session.add(pessoa)
        await session.flush()
        bind_session_tenant(session, tenant.id)
        for _ in range(2):  # repetido não duplica nem dá erro de chave
            await remember_phone(
                session,
                tenant_id=tenant.id,
                customer_id=pessoa.id,
                phone="(11) 95555-1234",
                source="checkout",
            )
        await session.commit()
        linhas = (await session.execute(select(CustomerPhone))).scalars().all()
    assert len(linhas) == 1 and linhas[0].first_seen_at <= linhas[0].last_seen_at


async def test_aprovado_com_o_telefone_na_conta_ja_e_atendido(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], shop: Any
) -> None:
    """O número está na conta de quem a loja já aprovou, mas ainda não no mapa da loja: a
    primeira mensagem já cai no robô (antes, saía como lead novo e ficava calada)."""
    tenant, _, _ = shop
    await fechar(session_factory, tenant)
    dono = await account_of(session_factory, tenant)
    async with session_factory() as session:
        pessoa = Customer(phone_e164=f"55{FONE}", full_name="Lia", status="active")
        session.add(pessoa)
        await session.flush()
        bind_session_tenant(session, tenant.id)
        session.add(
            CustomerTenantAccess(
                tenant_id=tenant.id,
                customer_id=pessoa.id,
                status=AccessStatus.APPROVED,
                source="panel",
            )
        )
        await session.commit()
    assert await gate(client, dono) == {
        "responder": True,
        "motivo": "approved",
        "cliente_id": pessoa.id,
    }
