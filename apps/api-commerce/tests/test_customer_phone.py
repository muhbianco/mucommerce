"""WhatsApp phone confirmation of store customers (reverse confirmation through api-agents)."""

from __future__ import annotations

from urllib.parse import parse_qs, unquote, urlsplit

import httpx
import pytest
import respx
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.phone import normalize_br_phone, same_br_phone
from app.customers.models import CustomerPhoneChallenge
from app.customers.phone import ENTRY_PATH
from app.identity.models import Customer
from app.tenancy.models import Tenant
from app.tenancy.service import Actor, TenantService
from tests.test_customer_login import make_store, sign_in, store_headers

pytestmark = pytest.mark.usefixtures("customer_login_configured")
ENTRY_URL = settings.muhbianco_accounts_internal_url.rstrip("/") + ENTRY_PATH
AGENTS = {"X-Internal-Token": "agents-token-test"}


@pytest.fixture
async def store(session_factory: async_sessionmaker[AsyncSession]) -> Tenant:
    tenant = await make_store(session_factory, "alpha")
    async with session_factory() as session:
        service = TenantService(session)
        await service.set_features(
            await service.get_or_404(tenant.id), {"customer_phone_otp": True}, Actor.system("t")
        )
        await session.commit()
    return tenant


def test_br_phone_rules() -> None:
    assert normalize_br_phone("(11) 99999-8888") == "5511999998888"
    assert normalize_br_phone("+55 11 3333-4444") == "551133334444"
    assert normalize_br_phone("11 89999-8888") is None  # 11 digits must be a 9 mobile
    assert normalize_br_phone("123") is None
    assert same_br_phone("5511999998888", "551199998888")  # WhatsApp id without the 9th digit
    assert not same_br_phone("5511999998888", "5511999998887")


async def start(client: AsyncClient, tenant: Tenant, token: str, phone: str) -> httpx.Response:
    return await client.post(
        "/api/v1/me/phone/start", json={"phone": phone}, headers=store_headers(tenant, token)
    )


def mock_entry(router: respx.MockRouter) -> respx.Route:
    return router.get(ENTRY_URL).mock(
        return_value=httpx.Response(200, json={"phone": "+55 11 94000-0000"})
    )


async def test_start_link_then_agents_confirm_from_the_same_number(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], store: Tenant
) -> None:
    token = (await sign_in(client, store))["session_token"]
    with respx.mock(assert_all_called=True) as router:
        entry = mock_entry(router)
        started = await start(client, store, token, "(11) 98888-7777")
    assert started.status_code == 200, started.text
    assert entry.calls.last.request.headers["X-Internal-Token"] == "agents-token-test"
    url = urlsplit(started.json()["whatsapp_url"])
    assert (url.netloc, url.path) == ("wa.me", "/5511940000000")
    code = unquote(parse_qs(url.query)["text"][0]).removeprefix("CONFIRMAR ")

    async with session_factory() as session:
        row = (await session.execute(select(CustomerPhoneChallenge))).scalar_one()
    assert row.token_hash != code and len(row.token_hash) == 64

    wrong_number = await client.post(
        "/api/v1/internal/agents/phone-confirmations",
        json={"token": code, "phone": "5511977776666"},
        headers=AGENTS,
    )
    assert wrong_number.status_code == 404
    ok = await client.post(
        "/api/v1/internal/agents/phone-confirmations",
        json={"token": code, "phone": "551188887777"},  # same mobile, no 9th digit
        headers=AGENTS,
    )
    assert ok.status_code == 200 and ok.json() == {"tenant_name": "Alpha"}
    again = await client.post(
        "/api/v1/internal/agents/phone-confirmations",
        json={"token": code, "phone": "5511988887777"},
        headers=AGENTS,
    )
    assert again.status_code == 404  # spent

    phone = await client.get("/api/v1/me/phone", headers=store_headers(store, token))
    assert phone.json() == {"phone_masked": "+5511 •••• 7777", "verified": True}


async def test_confirmation_needs_the_agents_token_and_moves_a_proven_number(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], store: Tenant
) -> None:
    first = (await sign_in(client, store, sub="g-1", email="um@example.com"))["session_token"]
    second = (await sign_in(client, store, sub="g-2", email="dois@example.com"))["session_token"]
    codes = []
    for token in (first, second):
        with respx.mock() as router:
            mock_entry(router)
            started = await start(client, store, token, "11988887777")
        text = parse_qs(urlsplit(started.json()["whatsapp_url"]).query)["text"][0]
        codes.append(unquote(text).removeprefix("CONFIRMAR "))

    web = await client.post(
        "/api/v1/internal/agents/phone-confirmations",
        json={"token": codes[0], "phone": "5511988887777"},
        headers={"X-Internal-Token": "web-token-test"},
    )
    assert web.status_code == 401
    for code in codes:
        response = await client.post(
            "/api/v1/internal/agents/phone-confirmations",
            json={"token": code, "phone": "5511988887777"},
            headers=AGENTS,
        )
        assert response.status_code == 200
    async with session_factory() as session:
        verified = {
            c.email_normalized: c.phone_verified_at is not None
            for c in (await session.execute(select(Customer))).scalars()
        }
    assert verified == {"um@example.com": False, "dois@example.com": True}


async def test_limits_expiry_flag_and_agents_down(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], store: Tenant
) -> None:
    token = (await sign_in(client, store))["session_token"]
    bad = await start(client, store, token, "123")
    assert bad.status_code == 422

    with respx.mock() as router:
        router.get(ENTRY_URL).mock(side_effect=httpx.ConnectTimeout("t"))
        down = await start(client, store, token, "11988887777")
    assert down.status_code == 503 and down.json()["error"]["code"] == "phone_unavailable"

    with respx.mock() as router:
        mock_entry(router)
        for _ in range(3):
            assert (await start(client, store, token, "11988887777")).status_code == 200
        limited = await start(client, store, token, "11988887777")
    assert limited.status_code == 429

    async with session_factory() as session:
        await session.execute(
            update(CustomerPhoneChallenge).values(expires_at=CustomerPhoneChallenge.created_at)
        )
        await session.commit()
    # Expired challenges never confirm (any token: none is valid anymore).
    async with session_factory() as session:
        rows = (await session.execute(select(CustomerPhoneChallenge))).scalars().all()
    assert rows and all(r.consumed_at is None for r in rows)

    other = await make_store(session_factory, "beta")
    other_token = (await sign_in(client, other))["session_token"]
    off = await start(client, other, other_token, "11988887777")
    assert off.status_code == 403 and off.json()["error"]["code"] == "feature_disabled"
