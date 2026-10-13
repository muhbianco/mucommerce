"""Liberar pelo Chatwoot: o lojista marca "Liberado na loja" no contato (docs/06 §3).

O contato chega com o telefone; aqui ele vira o cliente desta loja e a decisão passa pelo mesmo
serviço do painel. Desmarcar revoga quem estava liberado. A credencial do robô de vendas não
chega nesta rota.
"""

# ruff: noqa: F811
from __future__ import annotations

from typing import Any

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.audit.models import AuditLog
from app.identity.models import CustomerTenantAccess
from app.tenancy.context import CROSS_TENANT_OPTION
from tests.test_agent_links import como_agente, conectar
from tests.test_checkout_place import shop  # noqa: F401
from tests.test_internal_agent import AGENT, account_of
from tests.test_lead_gate import fechar, gate

ACCESS = f"{AGENT}/customers/access"
CROSS = {CROSS_TENANT_OPTION: True}
FONE = "+55 11 95555-0101"


async def liberar(client: AsyncClient, dono: dict[str, str], **body: Any) -> Any:
    resposta = await client.post(
        ACCESS, json={"telefone": FONE, "liberar": True, **body}, headers=dono
    )
    assert resposta.status_code == 200, resposta.text
    return resposta.json()


async def test_marcar_no_chatwoot_libera_quem_escreveu_no_whatsapp(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], shop: Any
) -> None:
    tenant, _, _ = shop
    await fechar(session_factory, tenant)
    dono = await account_of(session_factory, tenant)
    lead = await gate(client, dono, "11955550101")
    assert lead["responder"] is False

    feito = await liberar(client, dono, chatwoot_contato_id=321, quem="ana@loja.test")
    assert feito == {"cliente_id": lead["cliente_id"], "situacao": "approved", "mudou": True}
    assert (await gate(client, dono, "11955550101"))["responder"] is True

    async with session_factory() as session:
        row = (
            await session.execute(
                select(CustomerTenantAccess)
                .where(CustomerTenantAccess.customer_id == feito["cliente_id"])
                .execution_options(**CROSS)
            )
        ).scalar_one()
        assert row.source == "chatwoot" and row.chatwoot_contact_id == 321
        quem = (
            await session.execute(
                select(AuditLog.actor)
                .where(AuditLog.action == "customer.access.approved")
                .execution_options(**CROSS)
            )
        ).scalar_one()
        assert quem == "chatwoot:ana@loja.test"

    # Marcar de novo não muda nada (o Chatwoot manda o mesmo contato várias vezes).
    assert (await liberar(client, dono))["mudou"] is False


async def test_numero_que_a_loja_nunca_viu_entra_liberado(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], shop: Any
) -> None:
    tenant, _, _ = shop
    await fechar(session_factory, tenant)
    dono = await account_of(session_factory, tenant)
    feito = await liberar(client, dono, nome="Bia")
    assert feito["situacao"] == "approved" and feito["mudou"] is True
    assert (await gate(client, dono, "11955550101"))["responder"] is True


async def test_desmarcar_revoga_quem_estava_liberado_e_ignora_o_resto(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], shop: Any
) -> None:
    tenant, _, _ = shop
    await fechar(session_factory, tenant)
    dono = await account_of(session_factory, tenant)

    nada = await client.post(ACCESS, json={"telefone": FONE, "liberar": False}, headers=dono)
    assert nada.json() == {"cliente_id": "", "situacao": "none", "mudou": False}

    await liberar(client, dono)
    tirado = await client.post(ACCESS, json={"telefone": FONE, "liberar": False}, headers=dono)
    assert tirado.json()["situacao"] == "revoked" and tirado.json()["mudou"] is True
    assert (await gate(client, dono, "11955550101"))["responder"] is False


async def test_credencial_do_robo_de_vendas_nao_libera_ninguem(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], shop: Any
) -> None:
    tenant, owner, _ = shop
    vinculo = await conectar(client, tenant, owner)
    resposta = await client.post(
        ACCESS,
        json={"telefone": FONE, "liberar": True},
        headers=como_agente(vinculo["token"]),
    )
    assert resposta.status_code == 403, resposta.text


async def test_telefone_invalido_e_recusado(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], shop: Any
) -> None:
    tenant, _, _ = shop
    dono = await account_of(session_factory, tenant)
    resposta = await client.post(
        ACCESS, json={"telefone": "12345678", "liberar": True}, headers=dono
    )
    assert resposta.status_code == 422
