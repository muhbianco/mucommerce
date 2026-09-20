from __future__ import annotations

import uuid

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.audit.models import AuditLog, OutboxEvent
from app.tenancy.context import CROSS_TENANT_OPTION


async def test_create_tenant_is_idempotent_and_audited(
    client: AsyncClient,
    operator_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    key = str(uuid.uuid4())
    payload = {"slug": "lunares", "name": "Lunares Brownies"}
    headers = {**operator_headers, "Idempotency-Key": key}

    first = await client.post("/api/v1/ops/tenants", json=payload, headers=headers)
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["status"] == "draft"
    assert len(body["public_key"]) == 32

    replay = await client.post("/api/v1/ops/tenants", json=payload, headers=headers)
    assert replay.status_code == 201
    assert replay.headers.get("Idempotent-Replayed") == "true"
    assert replay.json()["id"] == body["id"]

    reused = await client.post(
        "/api/v1/ops/tenants", json={"slug": "outra", "name": "Outra"}, headers=headers
    )
    assert reused.status_code == 422
    assert reused.json()["error"]["code"] == "idempotency_key_reused"

    missing = await client.post("/api/v1/ops/tenants", json=payload, headers=operator_headers)
    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "idempotency_key_required"

    duplicate_slug = await client.post(
        "/api/v1/ops/tenants",
        json=payload,
        headers={**operator_headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert duplicate_slug.status_code == 409

    async with session_factory() as session:
        audits = (
            (await session.execute(select(AuditLog).where(AuditLog.action == "tenant.created")))
            .scalars()
            .all()
        )
        assert len(audits) == 1
        assert audits[0].actor.startswith("admin:")
        events = (
            (
                await session.execute(
                    select(OutboxEvent)
                    .where(OutboxEvent.event_type == "tenant.created")
                    .execution_options(**{CROSS_TENANT_OPTION: True})
                )
            )
            .scalars()
            .all()
        )
        assert len(events) == 1
        assert events[0].tenant_id == body["id"]


async def test_features_settings_status_and_domains(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    created = await client.post(
        "/api/v1/ops/tenants",
        json={"slug": "lunares", "name": "Lunares"},
        headers={**operator_headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    tenant_id = created.json()["id"]

    flags = await client.get(f"/api/v1/ops/tenants/{tenant_id}/features", headers=operator_headers)
    assert flags.json()["storefront"] is True and flags.json()["manufacturing"] is False

    updated = await client.put(
        f"/api/v1/ops/tenants/{tenant_id}/features",
        json={"flags": {"manufacturing": True, "payments.mercadopago": True}},
        headers=operator_headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["manufacturing"] is True

    bad_flag = await client.put(
        f"/api/v1/ops/tenants/{tenant_id}/features",
        json={"flags": {"teleport": True}},
        headers=operator_headers,
    )
    assert bad_flag.status_code == 422

    setting = await client.put(
        f"/api/v1/ops/tenants/{tenant_id}/settings/storefront",
        json={"value": {"access_mode": "public", "currency": "BRL"}},
        headers=operator_headers,
    )
    assert setting.status_code == 200

    activated = await client.post(
        f"/api/v1/ops/tenants/{tenant_id}/status",
        json={"status": "active"},
        headers=operator_headers,
    )
    assert activated.status_code == 200 and activated.json()["status"] == "active"

    invalid = await client.post(
        f"/api/v1/ops/tenants/{tenant_id}/status",
        json={"status": "draft"},
        headers=operator_headers,
    )
    assert invalid.status_code == 409

    context = await client.get("/api/v1/storefront/context", headers={"host": "lunares.loja.test"})
    assert context.status_code == 200
    assert context.json()["access_mode"] == "public"

    domain = await client.post(
        f"/api/v1/ops/tenants/{tenant_id}/domains",
        json={"hostname": "Lunares.com.br", "role": "primary"},
        headers=operator_headers,
    )
    assert domain.status_code == 201, domain.text
    body = domain.json()
    assert body["hostname"] == "lunares.com.br"
    assert body["kind"] == "custom_apex"
    assert body["status"] == "pending_dns"
    assert body["instructions"]["txt_name"] == "_muhbianco-verify.lunares.com.br"
    assert body["instructions"]["a_records"] == ["203.0.113.10"]

    # Unverified domain is not served and not in the edge config.
    not_yet = await client.get("/api/v1/storefront/context", headers={"host": "lunares.com.br"})
    assert not_yet.status_code == 404
    edge = await client.get(
        "/api/v1/internal/edge/traefik",
        headers={"host": "api.test", "X-Internal-Token": "traefik-token-test"},
    )
    assert "Host(`lunares.com.br`)" not in [
        r["rule"] for r in edge.json()["http"]["routers"].values()
    ]

    listing = await client.get(f"/api/v1/ops/tenants/{tenant_id}/domains", headers=operator_headers)
    hosts = {d["hostname"]: d for d in listing.json()}
    assert hosts["lunares.loja.test"]["role"] == "alias"  # demoted when the apex became primary
    assert hosts["lunares.com.br"]["role"] == "primary"

    duplicate = await client.post(
        f"/api/v1/ops/tenants/{tenant_id}/domains",
        json={"hostname": "lunares.com.br"},
        headers=operator_headers,
    )
    assert duplicate.status_code == 409

    reserved = await client.post(
        f"/api/v1/ops/tenants/{tenant_id}/domains",
        json={"hostname": "painel.test"},
        headers=operator_headers,
    )
    assert reserved.status_code == 422

    cannot_disable_primary = await client.delete(
        f"/api/v1/ops/tenants/{tenant_id}/domains/{body['id']}", headers=operator_headers
    )
    assert cannot_disable_primary.status_code == 409


async def test_tenant_staff_cannot_use_ops_routes(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    from tests.conftest import create_admin, login

    await create_admin(session_factory, "staff@tenant.test")
    headers = await login(client, "staff@tenant.test")
    response = await client.post(
        "/api/v1/ops/tenants",
        json={"slug": "x-tenant", "name": "X"},
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 403
