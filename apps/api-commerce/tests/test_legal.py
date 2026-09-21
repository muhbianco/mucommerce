"""Store legal documents (versioned) and the consents recorded when customers sign in."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
import respx
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.customers.legal_models import Consent
from app.tenancy.context import CROSS_TENANT_OPTION
from app.tenancy.models import Tenant
from tests.test_catalog import base, member_headers
from tests.test_customer_login import (
    BINDING,
    callback,
    id_token,
    make_store,
    mock_google,
    store_headers,
)

CROSS = {CROSS_TENANT_OPTION: True}
pytestmark = pytest.mark.usefixtures("customer_login_configured")
TERMS = "Termos de uso da loja: pedidos, pagamentos, entregas e trocas."
PRIVACY = "Política de privacidade: usamos seu e-mail e telefone para os pedidos."


@pytest.fixture
async def store(session_factory: async_sessionmaker[AsyncSession]) -> Tenant:
    return await make_store(session_factory, "alpha")


async def publish(
    client: AsyncClient, tenant: Tenant, headers: dict[str, str], kind: str, text: str
) -> dict[str, Any]:
    response = await client.post(
        f"{base(tenant)}/legal-documents", json={"kind": kind, "content": text}, headers=headers
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


async def test_versions_are_immutable_and_the_storefront_shows_the_latest(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], store: Tenant
) -> None:
    owner = await member_headers(client, session_factory, store)
    first = await publish(client, store, owner, "terms", TERMS)
    same = await publish(client, store, owner, "terms", TERMS + "  ")
    second = await publish(client, store, owner, "terms", TERMS + " Atualizado.")
    assert (first["version"], same["version"], second["version"]) == (1, 1, 2)

    overview = (await client.get(f"{base(store)}/legal-documents", headers=owner)).json()
    assert overview["terms"]["version"] == 2 and overview["privacy"] is None
    assert [h["version"] for h in overview["history"]] == [2, 1]

    shown = await client.get("/api/v1/storefront/policies", headers=store_headers(store))
    assert shown.json()["terms"]["version"] == 2 and shown.json()["privacy"] is None
    text = await client.get("/api/v1/storefront/policies/terms", headers=store_headers(store))
    assert text.json()["content"].endswith("Atualizado.") and "created_by_actor" in text.json()
    assert text.json()["created_by_actor"] is None  # authors are not public
    missing = await client.get("/api/v1/storefront/policies/privacy", headers=store_headers(store))
    assert missing.status_code == 404

    other = await make_store(session_factory, "beta")
    assert (
        await client.get("/api/v1/storefront/policies", headers=store_headers(other))
    ).json() == {"terms": None, "privacy": None}
    short = await client.post(
        f"{base(store)}/legal-documents", json={"kind": "terms", "content": "curto"}, headers=owner
    )
    assert short.status_code == 422


async def test_sign_in_records_the_versions_shown(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], store: Tenant
) -> None:
    owner = await member_headers(client, session_factory, store)
    await publish(client, store, owner, "terms", TERMS)
    await publish(client, store, owner, "privacy", PRIVACY)

    started = await client.post(
        "/api/v1/internal/customer-auth/google/start",
        json={"return_to": "/", "binding": BINDING, "terms_version": 1, "privacy_version": 7},
        headers=store_headers(store) | {"user-agent": "Browser/1.0"},
    )
    query = {k: v[0] for k, v in parse_qs(urlsplit(started.json()["authorize_url"]).query).items()}
    with respx.mock(assert_all_called=False) as router:
        mock_google(router, lambda form: id_token(query["nonce"]))
        answer = await callback(client, query["state"])
    hc = parse_qs(urlsplit(answer.headers["location"]).query)["hc"][0]
    done = await client.post(
        "/api/v1/internal/customer-auth/complete",
        json={"hc": hc, "binding": BINDING},
        headers=store_headers(store) | {"user-agent": "Browser/1.0"},
    )
    assert done.status_code == 200, done.text

    async with session_factory() as session:
        consents = (
            (await session.execute(select(Consent).execution_options(**CROSS))).scalars().all()
        )
    # Privacy v7 does not exist: only the terms version really shown is recorded.
    assert [(c.kind, c.document_version, c.user_agent) for c in consents] == [
        ("terms", 1, "Browser/1.0")
    ]
    assert consents[0].tenant_id == store.id and consents[0].ip
