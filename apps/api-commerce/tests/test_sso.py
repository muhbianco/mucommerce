"""Panel sign-in with the MuhBianco account (api-agents) and store owners set by ops."""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
import respx
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.scopes import PlatformRole
from app.identity.accounts import REDEEM_PATH
from app.identity.models import AdminUser
from tests.conftest import create_admin, create_tenant

REDEEM_URL = settings.muhbianco_accounts_internal_url.rstrip("/") + REDEEM_PATH
CODE = "c" * 32
VERIFIER = "v" * 43
PANEL = {"host": "painel.test"}


def account(**overrides: Any) -> dict[str, Any]:
    return {
        "account_id": str(uuid.uuid4()),
        "email": "mear.mind@example.com",
        "full_name": "Murilo",
        "role": "admin",
        "email_verified": True,
    } | overrides


async def exchange(client: AsyncClient, purpose: str = "panel") -> httpx.Response:
    return await client.post(
        "/api/v1/auth/sso/exchange",
        json={"code": CODE, "code_verifier": VERIFIER, "purpose": purpose},
        headers=PANEL,
    )


@respx.mock
async def test_site_admin_signs_in_as_platform_superadmin(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    data = account()
    route = respx.post(REDEEM_URL).mock(return_value=httpx.Response(200, json=data))
    response = await exchange(client)
    assert response.status_code == 200, response.text
    assert route.calls.last.request.read() == (
        b'{"code":"' + CODE.encode() + b'","code_verifier":"' + VERIFIER.encode() + b'"}'
    )
    tokens = response.json()
    assert tokens["refresh_token"]

    me = await client.get(
        "/api/v1/auth/me", headers=PANEL | {"Authorization": f"Bearer {tokens['access_token']}"}
    )
    assert me.json()["platform_role"] == PlatformRole.SUPERADMIN
    assert me.json()["email"] == "mear.mind@example.com"

    # Demoted on the site → no platform role at the next sign-in.
    respx.post(REDEEM_URL).mock(return_value=httpx.Response(200, json=data | {"role": "viewer"}))
    await exchange(client)
    async with session_factory() as session:
        user = (
            await session.execute(
                select(AdminUser).where(AdminUser.external_account_id == data["account_id"])
            )
        ).scalar_one()
    assert user.platform_role is None


@respx.mock
async def test_bad_codes_and_outages(client: AsyncClient) -> None:
    respx.post(REDEEM_URL).mock(return_value=httpx.Response(400, json={"error": "invalid_code"}))
    assert (await exchange(client)).status_code == 401

    respx.post(REDEEM_URL).mock(side_effect=httpx.ConnectError("down"))
    down = await exchange(client)
    assert down.status_code == 502
    assert down.json()["error"]["code"] == "external_service_error"

    respx.post(REDEEM_URL).mock(return_value=httpx.Response(200, json={"weird": True}))
    assert (await exchange(client)).status_code == 502

    malformed = await client.post(
        "/api/v1/auth/sso/exchange",
        json={"code": "short", "code_verifier": "x"},
        headers=PANEL,
    )
    assert malformed.status_code == 422


@respx.mock
async def test_site_admin_tokens_are_access_only_and_admin_only(client: AsyncClient) -> None:
    respx.post(REDEEM_URL).mock(return_value=httpx.Response(200, json=account()))
    token = await exchange(client, "site_admin")
    assert token.status_code == 200
    assert "refresh_token" not in token.json()

    respx.post(REDEEM_URL).mock(return_value=httpx.Response(200, json=account(role="viewer")))
    assert (await exchange(client, "site_admin")).status_code == 403


@respx.mock
@pytest.mark.parametrize(("verified", "status"), [(True, 200), (False, 401)])
async def test_existing_email_is_linked_only_when_verified(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    verified: bool,
    status: int,
) -> None:
    await create_admin(session_factory, "mear.mind@example.com")  # break-glass local user
    respx.post(REDEEM_URL).mock(
        return_value=httpx.Response(200, json=account(email_verified=verified))
    )
    assert (await exchange(client)).status_code == status


@respx.mock
async def test_ops_sets_the_owner_by_account_before_first_sign_in(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    operator_headers: dict[str, str],
) -> None:
    tenant = await create_tenant(session_factory, "lunares")
    first = account(email="dona@lunares.test", role="viewer")
    owner_url = f"/api/v1/ops/tenants/{tenant.id}/owner"

    set_first = await client.put(
        owner_url,
        json={"account_id": first["account_id"], "email": first["email"], "full_name": "Dona"},
        headers=operator_headers,
    )
    assert set_first.status_code == 200, set_first.text
    assert [o["email"] for o in set_first.json()] == ["dona@lunares.test"]

    listed = (await client.get("/api/v1/ops/tenants", headers=operator_headers)).json()
    row = next(t for t in listed["items"] if t["id"] == tenant.id)
    assert row["primary_host"] == "lunares.loja.test"
    assert row["access_mode"] == "whitelist"
    assert [o["account_id"] for o in row["owners"]] == [first["account_id"]]

    # The owner signs in later with that account and lands on the store.
    respx.post(REDEEM_URL).mock(return_value=httpx.Response(200, json=first))
    tokens = (await exchange(client)).json()
    me = await client.get(
        "/api/v1/auth/me", headers=PANEL | {"Authorization": f"Bearer {tokens['access_token']}"}
    )
    assert [(m["tenant_slug"], m["role"]) for m in me.json()["memberships"]] == [
        ("lunares", "owner")
    ]
    assert me.json()["platform_role"] is None

    second = account(email="novo@lunares.test")
    changed = await client.put(
        owner_url,
        json={"account_id": second["account_id"], "email": second["email"]},
        headers=operator_headers,
    )
    assert [o["email"] for o in changed.json()] == ["novo@lunares.test"]
    me_again = await client.get(
        "/api/v1/auth/me", headers=PANEL | {"Authorization": f"Bearer {tokens['access_token']}"}
    )
    assert [m["role"] for m in me_again.json()["memberships"]] == ["admin"]  # demoted, not removed


async def test_only_platform_staff_set_owners(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    from tests.conftest import login

    tenant = await create_tenant(session_factory, "lunares")
    await create_admin(session_factory, "qualquer@lunares.test")
    headers = await login(client, "qualquer@lunares.test")
    denied = await client.put(
        f"/api/v1/ops/tenants/{tenant.id}/owner",
        json={"account_id": str(uuid.uuid4()), "email": "x@y.z"},
        headers=headers,
    )
    assert denied.status_code == 403
