"""Visibilidade da vitrine (docs/14-pedidos-lunares.md §3): três modos, senha da loja e produto
exposto na home da loja fechada.

- aberta: tudo para todos;
- fechada com a home aberta: a home mostra os produtos expostos; o resto pede senha ou aprovação;
- toda fechada: nem a home sai antes de entrar.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.identity.models import AccessStatus
from app.tenancy.models import Tenant
from tests.shoppers import as_shopper, selling_store, signed_in
from tests.test_cart import FULFILLMENT
from tests.test_catalog import base, member_headers
from tests.test_pricing import product
from tests.test_storefront_catalog import set_stock

PASSWORD = "lua2026"
CONTEXT = "/api/v1/storefront/context"
LANDING = "/api/v1/storefront/landing"
CATALOG = "/api/v1/storefront/catalog/products"
ENTER = "/api/v1/internal/storefront/password"


def panel(tenant: Tenant) -> str:
    return f"/api/v1/admin/tenants/{tenant.id}/storefront"


def with_pass(tenant: Tenant, pass_token: str, session: str | None = None) -> dict[str, str]:
    return as_shopper(tenant, session) | {"X-Store-Pass": pass_token}


async def closed_store(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    slug: str = "lunares",
    *,
    landing_locked: bool = False,
    password: str | None = PASSWORD,
) -> tuple[Tenant, dict[str, str]]:
    tenant = await selling_store(
        session_factory, slug, mode="whitelist", settings={"fulfillment": FULFILLMENT}
    )
    owner = await member_headers(client, session_factory, tenant)
    mode = await client.put(
        f"{panel(tenant)}/access",
        json={"access_mode": "whitelist", "landing_locked": landing_locked},
        headers=owner,
    )
    assert mode.status_code == 200, mode.text
    if password:
        saved = await client.put(
            f"{panel(tenant)}/password", json={"password": password}, headers=owner
        )
        assert saved.status_code == 200, saved.text
    return tenant, owner


async def enter(client: AsyncClient, tenant: Tenant, password: str = PASSWORD) -> Any:
    return await client.post(ENTER, json={"password": password}, headers=as_shopper(tenant, None))


async def test_a_senha_abre_a_vitrine_e_trocar_a_senha_tranca_de_novo(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, owner = await closed_store(client, session_factory)
    brownie = await product(client, session_factory, tenant, owner)
    context = (await client.get(CONTEXT, headers=as_shopper(tenant, None))).json()
    assert context["access_mode"] == "whitelist" and context["password_gate"] is True

    assert (await client.get(CATALOG, headers=as_shopper(tenant, None))).status_code == 401
    wrong = await enter(client, tenant, "errada")
    assert wrong.status_code == 401 and wrong.json()["error"]["code"] == "invalid_store_password"
    right = await enter(client, tenant, f"  {PASSWORD} ")
    assert right.status_code == 200, right.text
    pass_token = right.json()["pass_token"]
    listed = await client.get(CATALOG, headers=with_pass(tenant, pass_token))
    assert listed.status_code == 200
    assert [p["id"] for p in listed.json()["items"]] == [brownie["id"]]

    # Do navegador, o passe vem no cookie; o cabeçalho só vale com o token do web.
    browser = {"Host": "lunares.loja.test"}
    forged = await client.get(CATALOG, headers=browser | {"X-Store-Pass": pass_token})
    assert forged.status_code == 401
    cookie = {"Cookie": f"__Host-mb_pass={pass_token}"}
    assert (await client.get(CATALOG, headers=browser | cookie)).status_code == 200

    trocada = await client.put(
        f"{panel(tenant)}/password", json={"password": "sol2027"}, headers=owner
    )
    assert trocada.status_code == 200
    assert (await client.get(CATALOG, headers=with_pass(tenant, pass_token))).status_code == 401
    novo = (await enter(client, tenant, "sol2027")).json()["pass_token"]
    assert (await client.get(CATALOG, headers=with_pass(tenant, novo))).status_code == 200

    removed = await client.delete(f"{panel(tenant)}/password", headers=owner)
    assert removed.status_code == 204
    assert (await client.get(CATALOG, headers=with_pass(tenant, novo))).status_code == 401
    assert (await enter(client, tenant, "sol2027")).status_code == 404


async def test_o_lojista_ve_a_senha_so_quando_pede(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, owner = await closed_store(client, session_factory)
    lida = (await client.get(f"{panel(tenant)}/password", headers=owner)).json()
    assert lida["set"] is True and lida["password"] is None and lida["masked"]
    vista = (await client.get(f"{panel(tenant)}/password?ver=1", headers=owner)).json()
    assert vista["password"] == PASSWORD
    access = (await client.get(f"{panel(tenant)}/access", headers=owner)).json()
    assert access == {"access_mode": "whitelist", "landing_locked": False, "password_set": True}


async def test_o_passe_de_uma_loja_nao_abre_outra(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    lunares, _ = await closed_store(client, session_factory, "lunares")
    outra, _ = await closed_store(client, session_factory, "outra")
    pass_token = (await enter(client, lunares)).json()["pass_token"]
    assert (await client.get(CATALOG, headers=with_pass(outra, pass_token))).status_code == 401


async def test_loja_aberta_nao_tem_porta_de_senha(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant = await selling_store(session_factory, "aberta", mode="public")
    assert (await enter(client, tenant)).status_code == 404


async def test_bloqueado_nao_entra_nem_com_a_senha(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, _ = await closed_store(client, session_factory)
    blocked = await signed_in(session_factory, tenant, access=AccessStatus.BLOCKED)
    pass_token = (await enter(client, tenant)).json()["pass_token"]
    response = await client.get(CATALOG, headers=with_pass(tenant, pass_token, blocked))
    assert response.status_code == 403


async def test_toda_fechada_nem_a_home_sai_antes_de_entrar(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, _ = await closed_store(client, session_factory, landing_locked=True)
    context = (await client.get(CONTEXT, headers=as_shopper(tenant, None))).json()
    assert context["landing_locked"] is True
    assert (await client.get(LANDING, headers=as_shopper(tenant, None))).status_code == 401
    pending = await signed_in(session_factory, tenant, access=AccessStatus.PENDING)
    assert (await client.get(LANDING, headers=as_shopper(tenant, pending))).status_code == 403
    pass_token = (await enter(client, tenant)).json()["pass_token"]
    assert (await client.get(LANDING, headers=with_pass(tenant, pass_token))).status_code == 200
    approved = await signed_in(session_factory, tenant)
    assert (await client.get(LANDING, headers=as_shopper(tenant, approved))).status_code == 200


async def test_home_aberta_mostra_so_os_expostos(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, owner = await closed_store(client, session_factory, password=None)
    escondido = await product(client, session_factory, tenant, owner, name="Escondido")
    vitrine = await product(
        client, session_factory, tenant, owner, name="Vitrine", home_exposure="preview"
    )
    direto = await product(
        client, session_factory, tenant, owner, name="Direto", home_exposure="open"
    )
    blocks = (await client.get(LANDING, headers=as_shopper(tenant, None))).json()
    destaques = [b for b in blocks if b.get("type") == "featured_products"]
    assert len(destaques) == 1 and not destaques[0].get("locked")
    assert {p["id"] for p in destaques[0]["products"]} == {vitrine["id"], direto["id"]}

    # A página do produto exposto abre; a do escondido continua pedindo entrada.
    seen = await client.get(f"{CATALOG}/{vitrine['slug']}", headers=as_shopper(tenant, None))
    assert seen.status_code == 200 and seen.json()["buy_requires_access"] is True
    straight = await client.get(f"{CATALOG}/{direto['slug']}", headers=as_shopper(tenant, None))
    assert straight.status_code == 200 and straight.json()["buy_requires_access"] is False
    hidden = await client.get(f"{CATALOG}/{escondido['slug']}", headers=as_shopper(tenant, None))
    assert hidden.status_code == 401
    missing = await client.get(f"{CATALOG}/nao-existe", headers=as_shopper(tenant, None))
    assert missing.status_code == 401  # sem acesso, "não existe" responde igual a "escondido"
    # A lista da vitrine continua fechada: expor é na home, não abrir o catálogo.
    assert (await client.get(CATALOG, headers=as_shopper(tenant, None))).status_code == 401

    # Quem tem acesso vê a home como o lojista montou (nenhum bloco de destaques aqui).
    approved = await signed_in(session_factory, tenant)
    full = (await client.get(LANDING, headers=as_shopper(tenant, approved))).json()
    assert not [b for b in full if b.get("type") == "featured_products"]


async def test_sem_acesso_so_compra_o_que_vai_direto_ao_carrinho(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, owner = await closed_store(client, session_factory, password=None)
    vitrine = await product(
        client, session_factory, tenant, owner, name="Vitrine", home_exposure="preview"
    )
    direto = await product(
        client, session_factory, tenant, owner, name="Direto", home_exposure="open"
    )
    for item in (vitrine, direto):
        await set_stock(session_factory, item["variants"][0]["id"], 5)
    me = as_shopper(tenant, await signed_in(session_factory, tenant, access=None))

    refused = await client.post(
        "/api/v1/cart/items",
        json={"variant_id": vitrine["variants"][0]["id"], "quantity": "1"},
        headers=me,
    )
    assert refused.status_code == 403, refused.text
    added = await client.post(
        "/api/v1/cart/items",
        json={"variant_id": direto["variants"][0]["id"], "quantity": "1"},
        headers=me,
    )
    assert added.status_code == 201, added.text

    # O lojista tira a compra direta com o produto já no carrinho: o pedido não fecha.
    changed = await client.patch(
        f"{base(tenant)}/products/{direto['id']}", json={"home_exposure": "preview"}, headers=owner
    )
    assert changed.status_code == 200
    cart = (await client.get("/api/v1/cart", headers=me)).json()
    placed = await client.post(
        "/api/v1/checkout/orders",
        json={
            "cart_id": cart["id"],
            "cart_version": cart["version"],
            "contact": {"name": "Lia", "phone": None},
            "expected_total_cents": cart["quote"]["total_cents"],
            "consent": {},
        },
        headers=me | {"Idempotency-Key": "pedido-1"},
    )
    assert placed.status_code == 403, placed.text


async def test_loja_toda_fechada_nao_vende_sem_acesso(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant, owner = await closed_store(client, session_factory, landing_locked=True, password=None)
    direto = await product(
        client, session_factory, tenant, owner, name="Direto", home_exposure="open"
    )
    me = as_shopper(tenant, await signed_in(session_factory, tenant, access=None))
    response = await client.post(
        "/api/v1/cart/items",
        json={"variant_id": direto["variants"][0]["id"], "quantity": "1"},
        headers=me,
    )
    assert response.status_code == 403
