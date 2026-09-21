"""Store panel: the whitelist (list customers, approve, block, revoke)."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.audit.models import AuditLog, OutboxEvent
from app.core.scopes import TenantRole
from app.tenancy.context import CROSS_TENANT_OPTION
from app.tenancy.models import Tenant
from tests.test_catalog import base, member_headers
from tests.test_customer_login import catalog_status, make_store, sign_in, store_headers

CROSS = {CROSS_TENANT_OPTION: True}
pytestmark = pytest.mark.usefixtures("customer_login_configured")


@pytest.fixture
async def store(session_factory: async_sessionmaker[AsyncSession]) -> Tenant:
    return await make_store(session_factory, "alpha")


async def customers(
    client: AsyncClient, tenant: Tenant, headers: dict[str, str], **params: Any
) -> dict[str, Any]:
    response = await client.get(f"{base(tenant)}/customers", params=params, headers=headers)
    assert response.status_code == 200, response.text
    return dict(response.json())


async def decide(
    client: AsyncClient,
    tenant: Tenant,
    headers: dict[str, str],
    customer_id: str,
    status: str,
    note: str | None = None,
) -> Any:
    return await client.post(
        f"{base(tenant)}/customers/{customer_id}/access",
        json={"status": status, "note": note},
        headers=headers,
    )


async def requested_customer(client: AsyncClient, tenant: Tenant, **claims: Any) -> dict[str, Any]:
    done = await sign_in(client, tenant, **claims)
    token = done["session_token"]
    response = await client.post(
        "/api/v1/me/access/request",
        json={"message": "Me libera?"},
        headers=store_headers(tenant, token),
    )
    assert response.status_code == 200
    return done


async def test_owner_approves_blocks_and_revokes(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], store: Tenant
) -> None:
    owner = await member_headers(client, session_factory, store)
    done = await requested_customer(client, store)
    customer_id, token = done["customer"]["id"], done["session_token"]

    page = await customers(client, store, owner, status="pending")
    assert [c["customer_id"] for c in page["items"]] == [customer_id]
    listed = page["items"][0]
    assert listed["email"] == "ana@example.com" and listed["request_message"] == "Me libera?"
    assert listed["source"] == "request"

    approved = await decide(client, store, owner, customer_id, "approved")
    assert approved.status_code == 200 and approved.json()["status"] == "approved"
    assert await catalog_status(client, store, token) == (200, "")
    again = await decide(client, store, owner, customer_id, "approved")
    assert again.status_code == 200  # no-op

    no_reason = await decide(client, store, owner, customer_id, "blocked")
    assert no_reason.status_code == 422
    blocked = await decide(client, store, owner, customer_id, "blocked", note="Calote")
    assert blocked.json()["status"] == "blocked" and blocked.json()["note"] == "Calote"
    # Blocking signs the customer out of this store at once.
    assert (
        await client.get("/api/v1/me/session", headers=store_headers(store, token))
    ).status_code == 401

    revoked = await decide(client, store, owner, customer_id, "revoked", note="Encerrado")
    assert revoked.json()["status"] == "revoked"

    async with session_factory() as session:
        actions = (
            await session.execute(
                select(AuditLog.action)
                .where(AuditLog.entity_type == "customer_tenant_access")
                .order_by(AuditLog.occurred_at)
                .execution_options(**CROSS)
            )
        ).scalars()
        events = (
            await session.execute(
                select(OutboxEvent.event_type)
                .where(OutboxEvent.aggregate_type == "customer_access")
                .execution_options(**CROSS)
            )
        ).scalars()
    assert list(actions) == [
        "customer.access.requested",
        "customer.access.approved",
        "customer.access.blocked",
        "customer.access.revoked",
    ]
    assert set(events) == {
        "customer.access.requested",
        "customer.access.approved",
        "customer.access.blocked",
        "customer.access.revoked",
    }


async def test_list_filters_search_and_pages(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], store: Tenant
) -> None:
    owner = await member_headers(client, session_factory, store)
    for n in range(3):
        await requested_customer(
            client, store, sub=f"google-{n}", email=f"cliente{n}@example.com", name=f"Cliente {n}"
        )
    first = await customers(client, store, owner, limit=2)
    assert len(first["items"]) == 2 and first["next_cursor"]
    second = await customers(client, store, owner, limit=2, cursor=first["next_cursor"])
    assert len(second["items"]) == 1 and second["next_cursor"] is None
    ids = [c["customer_id"] for c in first["items"] + second["items"]]
    assert len(set(ids)) == 3

    found = await customers(client, store, owner, q="cliente1")
    assert [c["email"] for c in found["items"]] == ["cliente1@example.com"]
    assert (await customers(client, store, owner, status="approved"))["items"] == []
    wildcard = await customers(client, store, owner, q="%_")
    assert wildcard["items"] == []  # LIKE wildcards are escaped


async def test_other_store_and_roles(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], store: Tenant
) -> None:
    done = await requested_customer(client, store)
    customer_id = done["customer"]["id"]
    other = await make_store(session_factory, "beta")
    other_owner = await member_headers(client, session_factory, other)
    assert (
        await client.get(f"{base(other)}/customers/{customer_id}", headers=other_owner)
    ).status_code == 404
    assert (await decide(client, other, other_owner, customer_id, "approved")).status_code == 404
    # Store beta's owner cannot reach store alpha's list at all.
    assert (await client.get(f"{base(store)}/customers", headers=other_owner)).status_code == 404

    ops = await member_headers(client, session_factory, store, TenantRole.OPS)
    assert (await customers(client, store, ops))["items"]  # ops can read
    denied = await decide(client, store, ops, customer_id, "approved")
    assert denied.status_code == 403  # but not approve
    support = await member_headers(client, session_factory, store, TenantRole.SUPPORT)
    assert (await decide(client, store, support, customer_id, "approved")).status_code == 200


async def test_pending_request_cannot_be_revoked(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], store: Tenant
) -> None:
    owner = await member_headers(client, session_factory, store)
    done = await requested_customer(client, store)
    response = await decide(client, store, owner, done["customer"]["id"], "revoked", note="x")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "invalid_transition"
