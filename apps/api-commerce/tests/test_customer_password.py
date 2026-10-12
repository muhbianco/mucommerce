"""Conta com e-mail e senha, por loja (docs/14-pedidos-lunares.md §2).

O cadastro só vira conta no clique do link; a resposta nunca conta quem tem cadastro; o link não
fica guardado em lugar nenhum; a senha de uma loja não abre outra.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.customers.email_links import TOKEN_SLOT
from app.customers.models import CustomerEmailChallenge, CustomerPassword
from app.identity.models import Customer, CustomerSession
from app.models.base import utcnow
from app.notifications.jobs import run_send_notifications
from app.notifications.models import NotificationDelivery
from app.notifications.transport import N8nTransport
from app.tenancy.context import CROSS_TENANT_OPTION
from app.tenancy.models import Tenant
from tests.test_customer_login import make_store, store_headers

CROSS = {CROSS_TENANT_OPTION: True}
AUTH = "/api/v1/internal/customer-auth/password"
GOOD = "lua-cheia-2026"


@pytest.fixture(autouse=True)
def mail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "notify_n8n_url", "https://n8n.test/hook")
    monkeypatch.setattr(settings, "notify_n8n_secret", SecretStr("n" * 32))
    monkeypatch.setattr(settings, "jwt_secret", SecretStr("j" * 48))


class Outbox:
    """O n8n de mentira: guarda o que saiu."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.sent.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "msg"})

    async def flush(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> list[dict[str, Any]]:
        before = len(self.sent)
        await run_send_notifications(
            session_factory, utcnow(), N8nTransport(httpx.MockTransport(self.handler))
        )
        return self.sent[before:]


def link_of(mail: dict[str, Any]) -> dict[str, str]:
    for word in mail["text"].split():
        if "?k=" in word:
            challenge_id, _, token = parse_qs(urlsplit(word).query)["k"][0].partition(".")
            return {"challenge_id": challenge_id, "token": token}
    raise AssertionError("e-mail sem link")


async def signup(
    client: AsyncClient, tenant: Tenant, email: str = "Lia@Example.com", **body: Any
) -> httpx.Response:
    return await client.post(
        f"{AUTH}/signup",
        json={"email": email, "name": "Lia", "password": GOOD, "return_to": "/loja"} | body,
        headers=store_headers(tenant),
    )


async def login(
    client: AsyncClient, tenant: Tenant, email: str = "lia@example.com", password: str = GOOD
) -> httpx.Response:
    return await client.post(
        f"{AUTH}/login", json={"email": email, "password": password}, headers=store_headers(tenant)
    )


async def rows(session_factory: async_sessionmaker[AsyncSession], model: Any) -> list[Any]:
    async with session_factory() as session:
        return list((await session.execute(select(model).execution_options(**CROSS))).scalars())


async def signed_up(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    tenant: Tenant,
    outbox: Outbox,
    email: str = "Lia@Example.com",
) -> dict[str, Any]:
    assert (await signup(client, tenant, email)).status_code == 202
    [mail] = await outbox.flush(session_factory)
    done = await client.post(f"{AUTH}/confirm", json=link_of(mail), headers=store_headers(tenant))
    assert done.status_code == 200, done.text
    return dict(done.json())


async def test_cadastro_so_vira_conta_no_clique_e_o_link_nao_fica_guardado(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant = await make_store(session_factory, "lunares")
    outbox = Outbox()
    response = await signup(client, tenant)
    assert response.status_code == 202
    assert response.json() == {"status": "sent"}
    # Nada da pessoa existe antes do clique: é o que impede amarrar senha a e-mail alheio.
    assert await rows(session_factory, Customer) == []
    assert await rows(session_factory, CustomerPassword) == []
    [queued] = await rows(session_factory, NotificationDelivery)
    assert queued.template_key == "account_confirm" and queued.secret_ref
    assert TOKEN_SLOT in (queued.body_text or "")

    [mail] = await outbox.flush(session_factory)
    assert mail["to"] == "lia@example.com"
    assert TOKEN_SLOT not in mail["text"] and TOKEN_SLOT not in mail["html"]
    link = link_of(mail)
    assert "https://lunares.loja.test/entrar/confirmar?k=" in mail["text"]
    # O banco continua sem link que funcione depois do envio.
    [queued] = await rows(session_factory, NotificationDelivery)
    assert link["token"] not in (queued.body_text or "") + (queued.body_html or "")

    done = await client.post(f"{AUTH}/confirm", json=link, headers=store_headers(tenant))
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["return_to"] == "/loja" and body["session_token"]
    [customer] = await rows(session_factory, Customer)
    assert customer.email_normalized == "lia@example.com"
    assert customer.email_verified_at is not None and customer.full_name == "Lia"

    again = await client.post(f"{AUTH}/confirm", json=link, headers=store_headers(tenant))
    assert again.status_code == 400 and again.json()["error"]["code"] == "invalid_link"
    forged = await client.post(
        f"{AUTH}/confirm", json=link | {"token": "x" * 32}, headers=store_headers(tenant)
    )
    assert forged.status_code == 400


async def test_login_erra_igual_para_senha_errada_e_email_desconhecido(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant = await make_store(session_factory, "lunares")
    await signed_up(client, session_factory, tenant, Outbox())

    ok = await login(client, tenant, email=" LIA@example.com ")
    assert ok.status_code == 200, ok.text
    session_token = ok.json()["session_token"]
    me = await client.get("/api/v1/me/session", headers=store_headers(tenant, session_token))
    assert me.status_code == 200 and me.json()["customer"]["name"] == "Lia"

    wrong = await login(client, tenant, password="outra-senha-qualquer")
    unknown = await login(client, tenant, email="ninguem@example.com")
    for response in (wrong, unknown):
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "invalid_credentials"
    assert wrong.json()["error"]["message"] == unknown.json()["error"]["message"]


async def test_a_senha_de_uma_loja_nao_abre_outra(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    lunares = await make_store(session_factory, "lunares")
    outra = await make_store(session_factory, "outra")
    await signed_up(client, session_factory, lunares, Outbox())
    assert (await login(client, outra)).status_code == 401
    # Na outra loja, a mesma pessoa cria a própria senha e continua sendo uma pessoa só.
    await signed_up(client, session_factory, outra, Outbox())
    assert (await login(client, outra)).status_code == 200
    assert len(await rows(session_factory, Customer)) == 1
    assert len(await rows(session_factory, CustomerPassword)) == 2


async def test_o_link_de_uma_loja_nao_confirma_em_outra(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    lunares = await make_store(session_factory, "lunares")
    outra = await make_store(session_factory, "outra")
    outbox = Outbox()
    assert (await signup(client, lunares)).status_code == 202
    [mail] = await outbox.flush(session_factory)
    elsewhere = await client.post(
        f"{AUTH}/confirm", json=link_of(mail), headers=store_headers(outra)
    )
    assert elsewhere.status_code == 400
    home = await client.post(f"{AUTH}/confirm", json=link_of(mail), headers=store_headers(lunares))
    assert home.status_code == 200, home.text


async def test_cadastro_repetido_avisa_por_email_sem_contar_na_resposta(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant = await make_store(session_factory, "lunares")
    outbox = Outbox()
    await signed_up(client, session_factory, tenant, outbox)
    again = await signup(client, tenant, password="tentando-de-novo-9")
    assert again.status_code == 202 and again.json() == {"status": "sent"}
    [mail] = await outbox.flush(session_factory)
    assert mail["template"] == "account_exists"
    # A senha de quem já tinha conta não mudou por causa do cadastro repetido.
    assert (await login(client, tenant)).status_code == 200


async def test_senha_fraca_e_recusada_antes_de_mandar_e_mail(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant = await make_store(session_factory, "lunares")
    for password, reason in (
        ("curta1", "too_short"),
        ("12345678", "common"),
        ("aaaaaaaa", "common"),
    ):
        response = await signup(client, tenant, password=password)
        assert response.status_code == 422, response.text
        assert response.json()["error"]["details"]["reason"] == reason
    email_like = await signup(client, tenant, email="lia.souza@example.com", password="lia.souza")
    assert email_like.status_code == 422
    assert await rows(session_factory, NotificationDelivery) == []


async def test_links_por_hora_tem_teto_mas_a_resposta_nao_muda(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant = await make_store(session_factory, "lunares")
    for _ in range(5):
        assert (await signup(client, tenant)).status_code in (202, 429)
    assert len(await rows(session_factory, CustomerEmailChallenge)) <= 3


async def test_trocar_a_senha_derruba_as_outras_sessoes(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant = await make_store(session_factory, "lunares")
    outbox = Outbox()
    first = await signed_up(client, session_factory, tenant, outbox)

    unknown = await client.post(
        f"{AUTH}/forgot", json={"email": "ninguem@example.com"}, headers=store_headers(tenant)
    )
    assert unknown.status_code == 202 and await outbox.flush(session_factory) == []

    asked = await client.post(
        f"{AUTH}/forgot", json={"email": "lia@example.com"}, headers=store_headers(tenant)
    )
    assert asked.status_code == 202
    [mail] = await outbox.flush(session_factory)
    assert mail["template"] == "password_reset"
    link = link_of(mail)

    weak = await client.post(
        f"{AUTH}/reset", json=link | {"password": "12345678"}, headers=store_headers(tenant)
    )
    assert weak.status_code == 422  # senha recusada não queima o link
    done = await client.post(
        f"{AUTH}/reset", json=link | {"password": "nova-lua-2027"}, headers=store_headers(tenant)
    )
    assert done.status_code == 200, done.text

    old = await client.get(
        "/api/v1/me/session", headers=store_headers(tenant, first["session_token"])
    )
    assert old.status_code == 401
    new = await client.get(
        "/api/v1/me/session", headers=store_headers(tenant, done.json()["session_token"])
    )
    assert new.status_code == 200
    assert (await login(client, tenant)).status_code == 401
    assert (await login(client, tenant, password="nova-lua-2027")).status_code == 200
    reused = await client.post(
        f"{AUTH}/reset", json=link | {"password": "outra-lua-2028"}, headers=store_headers(tenant)
    )
    assert reused.status_code == 400
    revoked = [s for s in await rows(session_factory, CustomerSession) if s.revoked_at]
    assert {s.revoked_reason for s in revoked} == {"password_reset"}


async def test_rotas_so_respondem_ao_web_da_loja(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant = await make_store(session_factory, "lunares")
    without_token = await client.post(
        f"{AUTH}/login",
        json={"email": "lia@example.com", "password": GOOD},
        headers={"X-Tenant-Host": f"{tenant.slug}.loja.test"},
    )
    assert without_token.status_code in (401, 403)
