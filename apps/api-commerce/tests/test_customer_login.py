"""Store customers: Google sign-in (start → central callback → handoff), sessions, access gate."""

from __future__ import annotations

import base64
import hashlib
import time
from collections.abc import AsyncIterator, Callable
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
import respx
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.customers.sessions import hash_token
from app.identity.models import (
    AccessStatus,
    Customer,
    CustomerIdentity,
    CustomerSession,
    CustomerTenantAccess,
)
from app.tenancy.context import CROSS_TENANT_OPTION
from app.tenancy.models import Tenant
from app.tenancy.service import Actor, TenantService
from tests.conftest import CUSTOMER_CLIENT_ID as CLIENT_ID
from tests.conftest import create_tenant
from tests.test_catalog import add_ready_image, base, create_product, member_headers

CROSS = {CROSS_TENANT_OPTION: True}
pytestmark = pytest.mark.usefixtures("customer_login_configured")
WEB = {"X-Internal-Token": "web-token-test"}
BINDING = "b" * 40
KID = "test-key-1"
_PRIVATE = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwk() -> dict[str, Any]:
    jwk: dict[str, Any] = jwt.algorithms.RSAAlgorithm.to_jwk(  # type: ignore[attr-defined]
        _PRIVATE.public_key(), as_dict=True
    )
    return jwk | {"kid": KID, "alg": "RS256", "use": "sig"}


def id_token(nonce: str | None, *, key: Any = _PRIVATE, alg: str = "RS256", **claims: Any) -> str:
    now = int(time.time())
    payload: dict[str, Any] = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "sub": "google-sub-1",
        "email": "Ana@Example.com",
        "email_verified": True,
        "name": "Ana Souza",
        "iat": now,
        "exp": now + 600,
    }
    if nonce is not None:
        payload["nonce"] = nonce
    return jwt.encode(payload | claims, key, algorithm=alg, headers={"kid": KID})


@pytest.fixture
async def store(session_factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[Tenant]:
    yield await make_store(session_factory, "alpha")


async def make_store(
    session_factory: async_sessionmaker[AsyncSession], slug: str, mode: str = "whitelist"
) -> Tenant:
    tenant = await create_tenant(session_factory, slug)
    async with session_factory() as session:
        service = TenantService(session)
        row = await service.get_or_404(tenant.id)
        await service.set_features(
            row, {"catalog": True, "customer_login": True}, Actor.system("t")
        )
        await service.set_setting(row, "storefront", {"access_mode": mode}, Actor.system("t"))
        await session.commit()
    return tenant


def store_headers(tenant: Tenant, session_token: str | None = None) -> dict[str, str]:
    headers = WEB | {"X-Tenant-Host": f"{tenant.slug}.loja.test"}
    if session_token:
        headers["X-Customer-Session"] = session_token
    return headers


async def start(client: AsyncClient, tenant: Tenant, return_to: str = "/loja") -> dict[str, str]:
    response = await client.post(
        "/api/v1/internal/customer-auth/google/start",
        json={"return_to": return_to, "binding": BINDING},
        headers=store_headers(tenant),
    )
    assert response.status_code == 200, response.text
    url = urlsplit(response.json()["authorize_url"])
    query = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert url.netloc == "accounts.google.com"
    assert query["client_id"] == CLIENT_ID
    assert query["code_challenge_method"] == "S256"
    assert query["redirect_uri"] == "https://api.test/api/v1/auth/google/callback"
    return query


def mock_google(
    router: respx.MockRouter, token_factory: Callable[[dict[str, str]], str]
) -> dict[str, list[str]]:
    """Token endpoint checks PKCE against the challenge it saw at authorize time."""
    seen: dict[str, list[str]] = {"verifiers": []}

    def token(request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        seen["verifiers"].append(form["code_verifier"])
        return httpx.Response(200, json={"id_token": token_factory(form)})

    router.post(settings.google_oidc_token_url).mock(side_effect=token)
    router.get(settings.google_oidc_jwks_url).mock(
        return_value=httpx.Response(
            200, json={"keys": [_jwk()]}, headers={"cache-control": "public, max-age=600"}
        )
    )
    return seen


def s256(verifier: str) -> str:
    return (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )


async def callback(client: AsyncClient, state: str, code: str = "google-code") -> httpx.Response:
    return await client.get(
        "/api/v1/auth/google/callback",
        params={"code": code, "state": state},
        headers={"host": "api.test"},
    )


async def sign_in(client: AsyncClient, tenant: Tenant, **claims: Any) -> dict[str, Any]:
    query = await start(client, tenant)
    with respx.mock(assert_all_called=False) as router:
        seen = mock_google(router, lambda form: id_token(query["nonce"], **claims))
        answer = await callback(client, query["state"])
    assert answer.status_code == 302, answer.text
    assert s256(seen["verifiers"][0]) == query["code_challenge"]
    target = urlsplit(answer.headers["location"])
    assert f"{target.scheme}://{target.netloc}" == f"https://{tenant.slug}.loja.test"
    assert target.path == "/auth/complete"
    hc = parse_qs(target.query)["hc"][0]
    done = await client.post(
        "/api/v1/internal/customer-auth/complete",
        json={"hc": hc, "binding": BINDING},
        headers=store_headers(tenant),
    )
    assert done.status_code == 200, done.text
    return dict(done.json()) | {"hc": hc}


async def catalog_status(client: AsyncClient, tenant: Tenant, token: str | None) -> tuple[int, str]:
    response = await client.get(
        "/api/v1/storefront/catalog/products", headers=store_headers(tenant, token)
    )
    code = response.json().get("error", {}).get("code", "") if response.status_code >= 400 else ""
    return response.status_code, code


# ----------------------------------------------------------------------------- happy path
async def test_sign_in_request_access_and_approval_open_the_catalog(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], store: Tenant
) -> None:
    done = await sign_in(client, store)
    assert done["return_to"] == "/loja"
    assert done["customer"]["email_masked"] == "a**@example.com"
    assert done["access_status"] is None
    token = done["session_token"]

    assert await catalog_status(client, store, None) == (401, "login_required")
    assert await catalog_status(client, store, token) == (403, "access_required")

    requested = await client.post(
        "/api/v1/me/access/request",
        json={"message": "Sou cliente"},
        headers=store_headers(store, token),
    )
    assert requested.status_code == 200, requested.text
    assert requested.json()["status"] == "pending"
    assert await catalog_status(client, store, token) == (403, "access_pending")

    async with session_factory() as session:
        await session.execute(
            update(CustomerTenantAccess)
            .values(status=AccessStatus.APPROVED)
            .execution_options(**CROSS)
        )
        await session.commit()
    assert await catalog_status(client, store, token) == (200, "")

    me = await client.get("/api/v1/me/session", headers=store_headers(store, token))
    assert me.json()["access_status"] == "approved"

    async with session_factory() as session:
        customer = (await session.execute(select(Customer))).scalar_one()
        link = (await session.execute(select(CustomerIdentity))).scalar_one()
        stored = (
            await session.execute(select(CustomerSession).execution_options(**CROSS))
        ).scalar_one()
    assert customer.email_normalized == "ana@example.com" and customer.email_verified_at
    assert link.subject == "google-sub-1" and set(link.raw_claims or {}) == {"name"}
    assert stored.token_hash != token and len(stored.token_hash) == 64  # only the hash is kept


async def test_second_sign_in_reuses_the_customer_and_rotates_the_session(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], store: Tenant
) -> None:
    first = await sign_in(client, store)
    query = await start(client, store)
    with respx.mock(assert_all_called=False) as router:
        mock_google(router, lambda form: id_token(query["nonce"]))
        answer = await callback(client, query["state"])
    hc = parse_qs(urlsplit(answer.headers["location"]).query)["hc"][0]
    second = await client.post(
        "/api/v1/internal/customer-auth/complete",
        json={"hc": hc, "binding": BINDING, "previous_session": first["session_token"]},
        headers=store_headers(store),
    )
    assert second.json()["customer"]["id"] == first["customer"]["id"]
    old = await client.get(
        "/api/v1/me/session", headers=store_headers(store, first["session_token"])
    )
    assert old.status_code == 401
    async with session_factory() as session:
        assert len((await session.execute(select(Customer))).all()) == 1


# ----------------------------------------------------------------------------- one-time codes
async def test_state_and_handoff_work_once_and_only_for_their_browser_and_store(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], store: Tenant
) -> None:
    other = await make_store(session_factory, "beta")
    query = await start(client, store)
    with respx.mock(assert_all_called=False) as router:
        mock_google(router, lambda form: id_token(query["nonce"]))
        first = await callback(client, query["state"])
        replay = await callback(client, query["state"])
    assert first.status_code == 302
    assert replay.status_code == 400 and "expirou" in replay.text

    hc = parse_qs(urlsplit(first.headers["location"]).query)["hc"][0]
    for headers, binding in (
        (store_headers(store), "x" * 40),  # another browser
        (store_headers(other), BINDING),  # another store
    ):
        wrong = await client.post(
            "/api/v1/internal/customer-auth/complete",
            json={"hc": hc, "binding": binding},
            headers=headers,
        )
        assert wrong.status_code == 400 and wrong.json()["error"]["code"] == "invalid_handoff"
    ok = await client.post(
        "/api/v1/internal/customer-auth/complete",
        json={"hc": hc, "binding": BINDING},
        headers=store_headers(store),
    )
    assert ok.status_code == 200
    again = await client.post(
        "/api/v1/internal/customer-auth/complete",
        json={"hc": hc, "binding": BINDING},
        headers=store_headers(store),
    )
    assert again.status_code == 400

    # The session of store alpha is worthless on store beta.
    token = ok.json()["session_token"]
    assert await catalog_status(client, other, token) == (401, "login_required")


async def test_callback_only_on_the_api_host_and_start_needs_the_web(
    client: AsyncClient, store: Tenant
) -> None:
    query = await start(client, store)
    elsewhere = await client.get(
        "/api/v1/auth/google/callback",
        params={"code": "c", "state": query["state"]},
        headers={"host": "alpha.loja.test"},
    )
    assert elsewhere.status_code == 404
    browser = await client.post(
        "/api/v1/internal/customer-auth/google/start",
        json={"return_to": "/", "binding": BINDING},
        headers={"X-Tenant-Host": "alpha.loja.test"},
    )
    assert browser.status_code == 401


# ----------------------------------------------------------------------------- id_token checks
def _other_key() -> Any:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.mark.parametrize(
    ("make_token", "reason"),
    [
        (lambda n: id_token("not-the-nonce"), "login_invalido"),
        (lambda n: id_token(None), "login_invalido"),
        (lambda n: id_token(n, aud="someone-else"), "login_invalido"),
        (lambda n: id_token(n, iss="https://evil.example"), "login_invalido"),
        (lambda n: id_token(n, exp=int(time.time()) - 3600), "login_invalido"),
        (lambda n: id_token(n, key=_other_key()), "login_invalido"),
        (lambda n: id_token(n, key="x" * 32, alg="HS256"), "login_invalido"),
        (lambda n: id_token(n, email_verified=False), "email_nao_verificado"),
    ],
)
async def test_bad_id_tokens_send_the_customer_back_with_a_reason(
    client: AsyncClient,
    store: Tenant,
    make_token: Callable[[str], str],
    reason: str,
) -> None:
    query = await start(client, store)
    with respx.mock(assert_all_called=False) as router:
        mock_google(router, lambda form: make_token(query["nonce"]))
        answer = await callback(client, query["state"])
    assert answer.status_code == 302
    target = urlsplit(answer.headers["location"])
    assert target.path == "/entrar"
    assert parse_qs(target.query) == {"erro": [reason], "next": ["/loja"]}


async def test_google_down_and_cancelled_consent(client: AsyncClient, store: Tenant) -> None:
    query = await start(client, store)
    with respx.mock(assert_all_called=False) as router:
        router.post(settings.google_oidc_token_url).mock(side_effect=httpx.ConnectTimeout("t"))
        answer = await callback(client, query["state"])
    assert parse_qs(urlsplit(answer.headers["location"]).query)["erro"] == ["google_indisponivel"]

    cancelled_state = (await start(client, store))["state"]
    cancelled = await client.get(
        "/api/v1/auth/google/callback",
        params={"error": "access_denied", "state": cancelled_state},
        headers={"host": "api.test"},
    )
    assert parse_qs(urlsplit(cancelled.headers["location"]).query)["erro"] == ["cancelado"]


@pytest.mark.parametrize(
    "unsafe", ["//evil.com/x", "https://evil.com", "/\\evil.com", "loja", "/auth/complete"]
)
async def test_return_to_stays_on_the_store(
    client: AsyncClient, store: Tenant, unsafe: str
) -> None:
    query = await start(client, store, return_to=unsafe)
    with respx.mock(assert_all_called=False) as router:
        mock_google(router, lambda form: id_token(query["nonce"]))
        answer = await callback(client, query["state"])
    hc = parse_qs(urlsplit(answer.headers["location"]).query)["hc"][0]
    done = await client.post(
        "/api/v1/internal/customer-auth/complete",
        json={"hc": hc, "binding": BINDING},
        headers=store_headers(store),
    )
    assert done.json()["return_to"] == "/"


async def test_store_suspended_or_flag_off_during_login(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], store: Tenant
) -> None:
    query = await start(client, store)
    async with session_factory() as session:
        service = TenantService(session)
        await service.set_features(
            await service.get_or_404(store.id), {"customer_login": False}, Actor.system("t")
        )
        await session.commit()
    with respx.mock(assert_all_called=False) as router:
        mock_google(router, lambda form: id_token(query["nonce"]))
        answer = await callback(client, query["state"])
    assert parse_qs(urlsplit(answer.headers["location"]).query)["erro"] == ["login_indisponivel"]

    blocked = await client.post(
        "/api/v1/internal/customer-auth/google/start",
        json={"return_to": "/", "binding": BINDING},
        headers=store_headers(store),
    )
    assert blocked.status_code == 403 and blocked.json()["error"]["code"] == "feature_disabled"


# ----------------------------------------------------------------------------- browser sessions
async def test_browser_cookie_session_and_logout_needs_the_store_origin(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], store: Tenant
) -> None:
    token = (await sign_in(client, store))["session_token"]
    browser = {"host": "alpha.loja.test", "cookie": f"__Host-mb_sess={token}"}

    me = await client.get("/api/v1/me/session", headers=browser)
    assert me.status_code == 200
    # A forwarded session header without the web token is ignored.
    spoofed = await client.get(
        "/api/v1/me/session", headers={"host": "alpha.loja.test", "X-Customer-Session": token}
    )
    assert spoofed.status_code == 401

    cross_site = await client.post("/api/v1/me/logout", json={}, headers=browser)
    assert cross_site.status_code == 403 and cross_site.json()["error"]["code"] == "csrf_origin"
    sibling = await client.post(
        "/api/v1/me/logout", json={}, headers=browser | {"origin": "https://evil.loja.test"}
    )
    assert sibling.status_code == 403
    out = await client.post(
        "/api/v1/me/logout", json={}, headers=browser | {"origin": "https://alpha.loja.test"}
    )
    assert out.status_code == 204
    assert (await client.get("/api/v1/me/session", headers=browser)).status_code == 401


async def test_blocked_customer_cannot_request_again_and_login_required_mode(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], store: Tenant
) -> None:
    token = (await sign_in(client, store))["session_token"]
    headers = store_headers(store, token)
    await client.post("/api/v1/me/access/request", json={}, headers=headers)
    again = await client.post("/api/v1/me/access/request", json={"message": "oi"}, headers=headers)
    assert again.json()["status"] == "pending"  # idempotent while pending

    async with session_factory() as session:
        await session.execute(
            update(CustomerTenantAccess)
            .values(status=AccessStatus.BLOCKED)
            .execution_options(**CROSS)
        )
        await session.commit()
    denied = await client.post("/api/v1/me/access/request", json={}, headers=headers)
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "access_blocked"
    assert await catalog_status(client, store, token) == (403, "access_blocked")

    open_store = await make_store(session_factory, "gamma", mode="login_required")
    other_token = (await sign_in(client, open_store))["session_token"]
    assert await catalog_status(client, open_store, other_token) == (200, "")


async def test_landing_shows_catalog_blocks_only_to_who_may_see_them(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], store: Tenant
) -> None:
    headers = await member_headers(client, session_factory, store)
    product = await create_product(client, store, headers)
    await add_ready_image(session_factory, store, product["id"])
    await client.post(f"{base(store)}/products/{product['id']}/publish", headers=headers)
    async with session_factory() as session:
        service = TenantService(session)
        await service.set_setting(
            await service.get_or_404(store.id),
            "landing",
            {
                "blocks": [
                    {
                        "type": "featured_products",
                        "title": "Destaques",
                        "product_ids": [product["id"]],
                    }
                ]
            },
            Actor.system("t"),
        )
        await session.commit()

    anonymous = await client.get("/api/v1/storefront/landing", headers=store_headers(store))
    token = (await sign_in(client, store))["session_token"]
    async with session_factory() as session:
        session.add(
            CustomerTenantAccess(
                tenant_id=store.id,
                customer_id=(await session.execute(select(Customer.id))).scalar_one(),
                status=AccessStatus.APPROVED,
            )
        )
        await session.commit()
    approved = await client.get("/api/v1/storefront/landing", headers=store_headers(store, token))
    assert anonymous.status_code == approved.status_code == 200
    assert anonymous.json() == []  # the featured products block is hidden from outsiders
    assert [block["type"] for block in approved.json()] == ["featured_products"]


async def test_purge_drops_old_flows_and_dead_sessions_only(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], store: Tenant
) -> None:
    from datetime import timedelta

    from app.customers.models import CustomerAuthFlow
    from app.customers.repository import purge_auth_flows, purge_sessions
    from app.models.base import utcnow

    live = (await sign_in(client, store))["session_token"]
    dead = (await sign_in(client, store))["session_token"]
    long_ago = utcnow() - timedelta(days=30)
    async with session_factory() as session:
        await session.execute(update(CustomerAuthFlow).values(created_at=long_ago))
        await session.execute(
            update(CustomerSession)
            .where(CustomerSession.token_hash == hash_token(dead))
            .values(expires_at=long_ago)
            .execution_options(**CROSS)
        )
        await session.commit()
    async with session_factory() as session:
        assert await purge_auth_flows(session, older_than=timedelta(days=1)) == 2
        assert await purge_sessions(session, older_than=timedelta(days=7)) == 1
        await session.commit()
    assert (
        await client.get("/api/v1/me/session", headers=store_headers(store, live))
    ).status_code == 200
