"""O vínculo agente ↔ loja: credencial própria, com o que ela pode e o que ela não pode.

A razão de existir é uma só: no modo expor o agente atende clientes finais, e falar pela conta
do dono significa carregar o papel do dono — uma conversa torta pausando produto, mexendo em
estoque ou cancelando pedido. A credencial de vendas vende e lê; não controla.

Os testes seguem essa ordem: primeiro o que a credencial recusa, depois o ciclo do código.
"""

# A fixture `shop` é importada por nome (é assim que o pytest a encontra) e usada como parâmetro.
# ruff: noqa: F811
from __future__ import annotations

import uuid
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.links import CODE_TTL, SCOPES
from app.agent.models import AgentLinkCode
from app.core.scopes import Scope
from app.models.base import utcnow
from app.tenancy.context import CROSS_TENANT_OPTION
from tests.test_checkout_place import shop  # noqa: F401
from tests.test_internal_agent import AGENT, TOKEN, account_of
from tests.test_internal_agent_sales import FONE, cliente_de, retirada_de, um_produto

CROSS = {CROSS_TENANT_OPTION: True}


def painel(tenant: Any) -> str:
    return f"/api/v1/admin/tenants/{tenant.id}/agent-links"


async def conectar(
    client: AsyncClient, tenant: Any, owner: dict[str, str], tipo: str = "sales"
) -> dict[str, Any]:
    """O caminho inteiro: o lojista gera, a plataforma resgata, o agente fica com o token."""
    gerado = await client.post(f"{painel(tenant)}/codes", json={"tipo": tipo}, headers=owner)
    assert gerado.status_code == 201, gerado.text
    resgatado = await client.post(
        f"{AGENT}/links/redeem",
        json={"codigo": gerado.json()["codigo"], "conta_ref": "conta-de-teste"},
        headers=TOKEN,
    )
    assert resgatado.status_code == 201, resgatado.text
    return dict(resgatado.json())


def como_agente(token: str) -> dict[str, str]:
    return TOKEN | {"X-Agent-Token": token}


# ------------------------------------------------------------ o que a credencial não pode


async def test_credencial_de_vendas_vende_mas_nao_controla(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], shop: Any
) -> None:
    """É a razão de o vínculo existir: o agente do expor não herda o papel do dono."""
    tenant, owner, _ = shop
    variante, preco = await um_produto(client, session_factory, shop)
    vinculo = await conectar(client, tenant, owner)
    agente = como_agente(vinculo["token"])

    # Vende.
    cliente = await cliente_de(client, agente)
    retirada = await retirada_de(client, agente, variante)
    pedido = await client.post(
        f"{AGENT}/sales/orders",
        json={
            "cliente_id": cliente["cliente_id"],
            "itens": [{"variante_id": variante, "quantidade": "1"}],
            "total_esperado_cents": preco,
            "contato_nome": "Maria",
            "contato_telefone": FONE,
            "referencia": f"msg-{uuid.uuid4()}",
            "retirada_id": retirada,
        },
        headers=agente,
    )
    assert pedido.status_code == 201, pedido.text

    # E não controla: nem estoque, nem vitrine, nem o pedido que acabou de criar.
    estoque = await client.post(
        f"{AGENT}/stock",
        json={"variante_id": variante, "quantidade": "10", "tipo": "entrada", "motivo": "x"},
        headers=agente,
    )
    assert estoque.status_code == 403

    produto_id = (await client.get(f"{AGENT}/products", headers=agente)).json()[0]["id"]
    pausa = await client.post(f"{AGENT}/products/{produto_id}/pause", json={}, headers=agente)
    assert pausa.status_code == 403

    mover = await client.post(
        f"{AGENT}/orders/{pedido.json()['pedido_id']}/advance",
        json={"para": "cancelled"},
        headers=agente,
    )
    assert mover.status_code == 403


async def test_a_credencial_nao_precisa_da_conta_e_nao_pede_qual_loja(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], shop: Any
) -> None:
    """O vínculo já diz a loja: não há o que escolher nem o que adivinhar."""
    tenant, owner, _ = shop
    vinculo = await conectar(client, tenant, owner)
    lida = await client.get(f"{AGENT}/store", headers=como_agente(vinculo["token"]))
    assert lida.status_code == 200, lida.text
    assert lida.json()["tenant_id"] == tenant.id
    assert lida.json()["role"] == "agent:sales"


async def test_sem_credencial_nenhuma_a_porta_nao_abre(client: AsyncClient) -> None:
    recusado = await client.get(f"{AGENT}/store", headers=TOKEN)
    assert recusado.status_code == 422
    assert recusado.json()["error"]["code"] == "credential_required"


async def test_credencial_revogada_para_de_valer_na_hora(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], shop: Any
) -> None:
    tenant, owner, _ = shop
    vinculo = await conectar(client, tenant, owner)
    agente = como_agente(vinculo["token"])
    assert (await client.get(f"{AGENT}/store", headers=agente)).status_code == 200

    revogado = await client.delete(f"{painel(tenant)}/{vinculo['vinculo_id']}", headers=owner)
    assert revogado.status_code == 200, revogado.text
    assert revogado.json()["revogado_em"] is not None
    assert (await client.get(f"{AGENT}/store", headers=agente)).status_code == 404


async def test_token_inventado_nao_abre_loja_nenhuma(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], shop: Any
) -> None:
    await conectar(client, shop[0], shop[1])
    negado = await client.get(f"{AGENT}/store", headers=como_agente("nao-e-um-token"))
    assert negado.status_code == 404


# ----------------------------------------------------------------------- o ciclo do código


async def test_o_codigo_morre_no_primeiro_uso(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], shop: Any
) -> None:
    tenant, owner, _ = shop
    codigo = (
        await client.post(f"{painel(tenant)}/codes", json={"tipo": "sales"}, headers=owner)
    ).json()["codigo"]
    primeiro = await client.post(f"{AGENT}/links/redeem", json={"codigo": codigo}, headers=TOKEN)
    assert primeiro.status_code == 201, primeiro.text
    segundo = await client.post(f"{AGENT}/links/redeem", json={"codigo": codigo}, headers=TOKEN)
    assert segundo.status_code == 422
    assert segundo.json()["error"]["code"] == "link_code_invalid"


async def test_codigo_vencido_e_codigo_errado_recusam_igual(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], shop: Any
) -> None:
    """Diferenciar ajudaria quem tenta adivinhar: "vencido" confirma que o código existiu."""
    tenant, owner, _ = shop
    codigo = (
        await client.post(f"{painel(tenant)}/codes", json={"tipo": "sales"}, headers=owner)
    ).json()["codigo"]
    async with session_factory() as session:
        linha = (
            (await session.execute(select(AgentLinkCode).execution_options(**CROSS)))
            .scalars()
            .one()
        )
        linha.created_at = utcnow() - CODE_TTL * 2
        await session.commit()

    vencido = await client.post(f"{AGENT}/links/redeem", json={"codigo": codigo}, headers=TOKEN)
    errado = await client.post(f"{AGENT}/links/redeem", json={"codigo": "ZZZZZZZZ"}, headers=TOKEN)
    assert vencido.status_code == errado.status_code == 422
    # O `request_id` muda a cada chamada; o que não pode diferir é o resto.
    sem_id = lambda erro: {k: v for k, v in erro["error"].items() if k != "request_id"}  # noqa: E731
    assert sem_id(vencido.json()) == sem_id(errado.json())


async def test_o_codigo_e_ditavel_e_aceita_o_que_a_pessoa_digita(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], shop: Any
) -> None:
    tenant, owner, _ = shop
    codigo = (
        await client.post(f"{painel(tenant)}/codes", json={"tipo": "sales"}, headers=owner)
    ).json()["codigo"]
    # Sem letras que se confundem ao ditar.
    assert not (set(codigo) & set("IO01"))
    # Minúsculo, com espaço e hífen no meio, funciona.
    digitado = f"{codigo[:4].lower()} - {codigo[4:].lower()}"
    resgatado = await client.post(f"{AGENT}/links/redeem", json={"codigo": digitado}, headers=TOKEN)
    assert resgatado.status_code == 201, resgatado.text


async def test_a_lista_do_painel_mostra_quem_esta_conectado(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], shop: Any
) -> None:
    tenant, owner, _ = shop
    vinculo = await conectar(client, tenant, owner)
    (linha,) = (await client.get(painel(tenant), headers=owner)).json()
    assert linha["id"] == vinculo["vinculo_id"]
    assert linha["tipo"] == "sales"
    assert linha["conta"] == "conta-de-teste"
    assert str(Scope.ORDERS_WRITE) in linha["permissoes"]
    assert str(Scope.INVENTORY_ADJUST) not in linha["permissoes"]


async def test_a_loja_so_enxerga_os_proprios_vinculos(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], shop: Any
) -> None:
    from tests.shoppers import selling_store
    from tests.test_catalog import member_headers

    tenant, owner, _ = shop
    await conectar(client, tenant, owner)
    outra = await selling_store(session_factory, "beta")
    dono_da_outra = await member_headers(client, session_factory, outra)
    assert (await client.get(painel(outra), headers=dono_da_outra)).json() == []


async def test_as_permissoes_de_cada_tipo_sao_fixas_no_codigo() -> None:
    """Permissão que se edita por UPDATE é permissão que ninguém revisa."""
    assert Scope.ORDERS_WRITE in SCOPES["sales"]
    assert Scope.INVENTORY_ADJUST not in SCOPES["sales"]
    assert Scope.ORDERS_TRANSITION not in SCOPES["sales"]
    assert Scope.CATALOG_WRITE not in SCOPES["sales"]
    # O assistente do lojista mantém o que já faz hoje falando pela conta — inclusive cancelar.
    # Trocar a conta pela credencial não é hora de tirar função; quem segura o cancelamento é o
    # bilhete de confirmação, que exige um turno da pessoa.
    assert SCOPES["operator"] >= {
        Scope.CATALOG_WRITE,
        Scope.INVENTORY_ADJUST,
        Scope.ORDERS_TRANSITION,
        Scope.ORDERS_CANCEL,
    }
    # E o de vendas segue sem nenhuma dessas alavancas.
    assert not (
        SCOPES["sales"] & {Scope.CATALOG_WRITE, Scope.INVENTORY_ADJUST, Scope.ORDERS_CANCEL}
    )


# ------------------------------------------------- conectar a própria loja, sem código


async def test_a_conta_lista_as_proprias_lojas(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], shop: Any
) -> None:
    """Pedir um código a quem já é dono é cerimônia: o servidor sabe quais lojas são dela."""
    from tests.shoppers import selling_store

    tenant, _, _ = shop
    outra = await selling_store(session_factory, "beta")
    dono = await account_of(session_factory, tenant)
    await account_of(session_factory, outra, conta=dono["X-Account-Id"])

    listadas = await client.get(f"{AGENT}/stores", headers=dono)
    assert listadas.status_code == 200, listadas.text
    assert {loja["slug"] for loja in listadas.json()} == {tenant.slug, "beta"}
    assert all(loja["papel"] == "owner" for loja in listadas.json())


async def test_a_conta_nao_ve_loja_de_outro_dono(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], shop: Any
) -> None:
    from tests.shoppers import selling_store

    tenant, _, _ = shop
    alheia = await selling_store(session_factory, "beta")
    await account_of(session_factory, alheia)
    dono = await account_of(session_factory, tenant)
    assert {
        loja["slug"] for loja in (await client.get(f"{AGENT}/stores", headers=dono)).json()
    } == {tenant.slug}


async def test_conectar_a_propria_loja_emite_credencial_sem_codigo(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], shop: Any
) -> None:
    tenant, owner, _ = shop
    dono = await account_of(session_factory, tenant)
    criado = await client.post(
        f"{AGENT}/links/self",
        json={"loja_id": tenant.id, "tipo": "operator", "nome": "meu assistente"},
        headers=dono,
    )
    assert criado.status_code == 201, criado.text
    assert criado.json()["loja_id"] == tenant.id
    assert criado.json()["tipo"] == "operator"

    # A credencial funciona, e o painel do lojista enxerga o vínculo.
    agente = como_agente(criado.json()["token"])
    assert (await client.get(f"{AGENT}/store", headers=agente)).json()["tenant_id"] == tenant.id
    (linha,) = (await client.get(painel(tenant), headers=owner)).json()
    assert linha["nome"] == "meu assistente"
    assert linha["conta"] == dono["X-Account-Id"]


async def test_nao_da_para_conectar_loja_que_nao_e_sua(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], shop: Any
) -> None:
    """E a recusa é a de loja inexistente: confirmar que ela existe já é contar demais."""
    from tests.shoppers import selling_store

    tenant, _, _ = shop
    alheia = await selling_store(session_factory, "beta")
    await account_of(session_factory, alheia)
    dono = await account_of(session_factory, tenant)

    negado = await client.post(f"{AGENT}/links/self", json={"loja_id": alheia.id}, headers=dono)
    assert negado.status_code == 404
    assert negado.json()["error"]["code"] == "no_store"


async def test_sem_conta_nao_lista_nem_conecta(client: AsyncClient) -> None:
    assert (await client.get(f"{AGENT}/stores", headers=TOKEN)).status_code == 422
    sem = await client.post(f"{AGENT}/links/self", json={"loja_id": "0" * 36}, headers=TOKEN)
    assert sem.status_code == 422
