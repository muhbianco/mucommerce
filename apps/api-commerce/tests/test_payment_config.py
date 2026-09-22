"""Payment configuration (stage E, S9): owner-only writes, masked secrets, what ops may see."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.audit.models import AuditLog
from app.core.config import settings
from app.core.crypto import CredentialCipherError
from app.core.scopes import PlatformRole, TenantRole
from app.integrations.credentials import CredentialStore, mask
from app.payments import registry
from app.payments.provider import ProviderCapabilities
from app.payments.providers.fake import FakeProvider
from app.tenancy.context import CROSS_TENANT_OPTION, bind_session_tenant
from app.tenancy.models import Tenant, TenantIntegrationCredential
from tests.conftest import create_admin, login
from tests.shoppers import selling_store
from tests.test_catalog import member_headers

TOKEN = "APP_USR-" + "7" * 40  # a made-up access token


class Dummy(FakeProvider):
    """A provider that needs a token and a public key before it can be enabled."""

    name = "mercadopago"
    capabilities = ProviderCapabilities(
        mode="embedded",
        methods=("pix",),
        cancel=True,
        refunds=True,
        signed_webhooks=True,
        required_secrets=("access_token", "webhook_secret"),
        required_public=("public_key",),
    )


@pytest.fixture(autouse=True)
def providers(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A stand-in under Mercado Pago's name (its flag exists); restored after each test."""
    monkeypatch.setattr(settings, "payments_allowed_providers", "fake,mercadopago")
    monkeypatch.setitem(registry._PROVIDERS, "mercadopago", Dummy())
    yield


def url(tenant: Tenant, provider: str = "") -> str:
    base = f"/api/v1/admin/tenants/{tenant.id}/payments/providers"
    return f"{base}/{provider}" if provider else base


async def test_secrets_are_bound_to_their_store(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    alpha = await selling_store(session_factory, "alpha")
    beta = await selling_store(session_factory, "beta")
    async with session_factory() as session:
        bind_session_tenant(session, alpha.id)
        store = CredentialStore(session, alpha.id)
        assert await store.put("mercadopago", "access_token", TOKEN) == mask(TOKEN) == "****7777"
        assert await store.get("mercadopago", "access_token") == TOKEN
        await session.commit()
    async with session_factory() as session:
        row = (
            await session.execute(
                select(TenantIntegrationCredential).execution_options(**{CROSS_TENANT_OPTION: True})
            )
        ).scalar_one()
        assert TOKEN.encode() not in row.ciphertext
        bind_session_tenant(session, beta.id)
        session.add(
            TenantIntegrationCredential(
                provider="mercadopago",
                key_name="access_token",
                ciphertext=row.ciphertext,
                nonce=row.nonce,
                key_version=row.key_version,
                masked=row.masked,
            )
        )
        await session.flush()
        with pytest.raises(CredentialCipherError):  # copied to another store: useless
            await CredentialStore(session, beta.id).get("mercadopago", "access_token")


async def test_the_owner_configures_and_nobody_sees_the_secret(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant = await selling_store(session_factory, flags={"payments.mercadopago": True})
    owner = await member_headers(client, session_factory, tenant)
    body: dict[str, Any] = {
        "enabled": True,
        "is_default": True,
        "public_config": {"public_key": "APP_USR-pub"},
        "credentials": {"access_token": TOKEN},
    }
    missing = await client.put(url(tenant, "mercadopago"), json=body, headers=owner)
    assert missing.status_code == 422
    assert missing.json()["error"]["details"]["missing"] == ["secret:webhook_secret"]

    body["credentials"]["webhook_secret"] = "whsec-" + "s" * 20
    saved = await client.put(url(tenant, "mercadopago"), json=body, headers=owner)
    assert saved.status_code == 200, saved.text
    read = saved.json()
    assert read["enabled"] and read["missing"] == []
    assert {k: v["masked"] for k, v in read["secrets"].items()} == {
        "access_token": "****7777",
        "webhook_secret": "****ssss",
    }
    assert read["webhook_url"].endswith(f"/api/v1/webhooks/mercadopago/{tenant.public_key}")
    assert TOKEN not in saved.text

    # Saving without secrets keeps them.
    kept = await client.put(
        url(tenant, "mercadopago"),
        json={"enabled": True, "public_config": {"public_key": "APP_USR-pub"}},
        headers=owner,
    )
    assert kept.json()["missing"] == []
    listed = await client.get(url(tenant), headers=owner)
    assert TOKEN not in listed.text and "whsec" not in listed.text
    tested = await client.post(f"{url(tenant, 'mercadopago')}/test", headers=owner)
    assert tested.json()["last_test_ok"] is True

    async with session_factory() as session:
        audits = (
            (
                await session.execute(
                    select(AuditLog.after_json).where(AuditLog.action == "payment_config.saved")
                )
            )
            .scalars()
            .all()
        )
    assert audits and all(TOKEN not in str(a) for a in audits)
    assert audits[-1]["secrets_changed"] == {}  # the second save changed no secret

    unknown = await client.put(
        url(tenant, "mercadopago"), json={"public_config": {"evil": "x"}}, headers=owner
    )
    assert unknown.status_code == 422
    flag_off = await selling_store(session_factory, "semflag")
    other_owner = await member_headers(client, session_factory, flag_off)
    off = await client.put(url(flag_off, "mercadopago"), json={}, headers=other_owner)
    assert off.status_code == 403 and off.json()["error"]["code"] == "feature_disabled"


async def test_only_the_stores_owner_writes(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant = await selling_store(session_factory, flags={"payments.mercadopago": True})
    admin_role = await member_headers(client, session_factory, tenant, TenantRole.ADMIN)
    assert (await client.get(url(tenant), headers=admin_role)).status_code == 200
    refused = await client.put(url(tenant, "fake"), json={"enabled": True}, headers=admin_role)
    assert refused.status_code == 403

    await create_admin(
        session_factory, "staff@muhbianco.test", platform_role=PlatformRole.SUPERADMIN
    )
    staff = await login(client, "staff@muhbianco.test")
    assert (await client.get(url(tenant), headers=staff)).status_code == 200  # support reads
    blocked = await client.put(url(tenant, "fake"), json={"enabled": True}, headers=staff)
    assert blocked.status_code == 403

    owner = await member_headers(client, session_factory, tenant)
    assert (
        await client.put(url(tenant, "fake"), json={"enabled": True}, headers=owner)
    ).status_code == 200


async def test_ops_sees_whether_it_is_configured_never_the_secret(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    operator_headers: dict[str, str],
) -> None:
    tenant = await selling_store(session_factory, flags={"payments.mercadopago": True})
    owner = await member_headers(client, session_factory, tenant)
    await client.put(
        url(tenant, "mercadopago"),
        json={
            "enabled": True,
            "public_config": {"public_key": "APP_USR-pub"},
            "credentials": {"access_token": TOKEN, "webhook_secret": "whsec-" + "s" * 20},
        },
        headers=owner,
    )
    summary = await client.get(
        f"/api/v1/ops/tenants/{tenant.id}/payments", headers=operator_headers
    )
    assert summary.status_code == 200, summary.text
    rows = {r["provider"]: r for r in summary.json()}
    assert rows["mercadopago"]["configured"] and rows["mercadopago"]["enabled"]
    assert rows["fake"]["configured"] is False
    assert "****" not in summary.text and TOKEN not in summary.text
