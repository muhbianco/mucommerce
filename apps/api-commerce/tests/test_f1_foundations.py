"""Phase 1 slice 1 foundations: scopes, flags, validated settings, access gates, admin CLI."""

from __future__ import annotations

import io
import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.requests import Request

from app.api.deps import require_catalog_access, require_tenant_scopes
from app.audit.idempotency import idempotent
from app.audit.models import IdempotencyKey, OutboxEvent
from app.cli import admin_bootstrap, admin_grant, read_password
from app.core.exceptions import (
    FeatureDisabledError,
    LoginRequiredError,
    NotFoundError,
    ValidationError,
)
from app.core.scopes import PlatformRole, Scope, TenantRole, scopes_for_tenant_role
from app.identity.models import AdminUser, TenantMembership
from app.tenancy.context import CROSS_TENANT_OPTION, TenantContext
from app.tenancy.models import (
    DEFAULT_FEATURE_FLAGS,
    DEFAULT_SETTINGS,
    TenantFeatureFlag,
    TenantSetting,
)
from app.tenancy.service import Actor, TenantService
from app.tenancy.settings_schemas import SETTINGS_SCHEMAS, validate_setting
from tests.conftest import TEST_PASSWORD, create_admin, create_tenant

CROSS = {CROSS_TENANT_OPTION: True}


# ----------------------------------------------------------------------------- scopes / flags
def test_media_write_scope_goes_to_catalog_roles_only() -> None:
    assert Scope.MEDIA_WRITE in scopes_for_tenant_role(TenantRole.OWNER)
    assert Scope.MEDIA_WRITE in scopes_for_tenant_role(TenantRole.OPS)
    assert Scope.MEDIA_WRITE not in scopes_for_tenant_role(TenantRole.SUPPORT)


def test_catalog_and_inventory_flags_start_off() -> None:
    assert DEFAULT_FEATURE_FLAGS["catalog"] is False
    assert DEFAULT_FEATURE_FLAGS["inventory"] is False


def test_flags_without_code_start_off() -> None:
    # A module switched on in the site admin must do something; these ship in later phases.
    for key in ("events", "pickup", "chatwoot", "delivery", "coupons", "sales_agent"):
        assert DEFAULT_FEATURE_FLAGS[key] is False, key


# ----------------------------------------------------------------------------- settings
def test_every_default_setting_matches_its_schema() -> None:
    assert set(DEFAULT_SETTINGS) == set(SETTINGS_SCHEMAS)
    for key, value in DEFAULT_SETTINGS.items():
        assert validate_setting(key, value) == (1, value)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("storefront", {"access_mode": "aberta"}),
        ("storefront", {"access_mode": "public", "extra": 1}),
        ("branding", {"primary_color": "red"}),
        ("branding", {"logo_url": "http://inseguro.test/logo.png"}),
        ("seo", {"title": "x" * 71}),
        ("fulfillment", {"modes": []}),
        ("checkout", {"pix_ttl_minutes": 1}),
    ],
)
def test_invalid_settings_are_rejected(key: str, value: dict[str, Any]) -> None:
    with pytest.raises(ValidationError) as caught:
        validate_setting(key, value)
    assert caught.value.details["errors"]


async def test_setting_write_is_validated_versioned_and_emitted(
    client: AsyncClient,
    operator_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tenant = await create_tenant(session_factory, "modelo")
    url = f"/api/v1/ops/tenants/{tenant.id}/settings/storefront"

    bad = await client.put(url, json={"value": {"access_mode": "aberta"}}, headers=operator_headers)
    assert bad.status_code == 422
    assert bad.json()["error"]["details"]["errors"][0]["field"] == "access_mode"

    ok = await client.put(url, json={"value": {"access_mode": "public"}}, headers=operator_headers)
    assert ok.status_code == 200, ok.text

    async with session_factory() as session:
        row = (
            await session.execute(
                select(TenantSetting)
                .where(TenantSetting.tenant_id == tenant.id, TenantSetting.key == "storefront")
                .execution_options(**CROSS)
            )
        ).scalar_one()
        assert row.value == {"access_mode": "public", "currency": "BRL"}  # defaults filled in
        assert row.schema_version == 1
        events = (
            await session.execute(
                select(OutboxEvent.payload)
                .where(OutboxEvent.event_type == "tenant.settings_changed")
                .execution_options(**CROSS)
            )
        ).scalars()
        assert list(events) == [{"key": "storefront", "schema_version": 1}]


# ----------------------------------------------------------------------------- gates
def _context(**overrides: Any) -> TenantContext:
    base: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "slug": "modelo",
        "public_key": "k" * 32,
        "name": "Modelo",
        "status": "active",
        "timezone": "America/Sao_Paulo",
        "locale": "pt-BR",
        "currency": "BRL",
        "features": {"storefront": True, "catalog": True},
        "settings": {"storefront": {"access_mode": "public"}},
    }
    base.update(overrides)
    return TenantContext(**base)


async def test_catalog_access_gate() -> None:
    public = _context()
    assert await require_catalog_access(public) is public

    with pytest.raises(LoginRequiredError):
        await require_catalog_access(
            _context(settings={"storefront": {"access_mode": "whitelist"}})
        )
    with pytest.raises(LoginRequiredError):
        await require_catalog_access(_context(settings={}))  # missing = whitelist
    with pytest.raises(NotFoundError):
        await require_catalog_access(_context(features={"storefront": True}))
    with pytest.raises(NotFoundError):
        await require_catalog_access(_context(features={"catalog": True}))


async def test_panel_route_feature_gate(session_factory: async_sessionmaker[AsyncSession]) -> None:
    tenant = await create_tenant(session_factory, "modelo")
    operator = await create_admin(
        session_factory, "ops@muhbianco.test", platform_role=PlatformRole.OPERATOR
    )
    gate = require_tenant_scopes(Scope.CATALOG_READ, features=("catalog",))
    async with session_factory() as session:
        user = await session.get(AdminUser, operator.id)
        assert user is not None
        with pytest.raises(FeatureDisabledError) as caught:
            await gate(session=session, user=user, tenant_id=tenant.id)
        assert caught.value.details == {"features": ["catalog"]}

        tenant_row = await TenantService(session).get_or_404(tenant.id)
        await TenantService(session).set_features(
            tenant_row, {"catalog": True}, Actor.system("tests")
        )
        await session.commit()
    async with session_factory() as session:
        user = await session.get(AdminUser, operator.id)
        resolved = await gate(session=session, user=user, tenant_id=tenant.id)
        assert resolved.id == tenant.id


# ----------------------------------------------------------------------------- idempotency
def _request(headers: dict[str, str] | None = None) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {"type": "http", "method": "POST", "path": "/x", "headers": raw, "query_string": b""}

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"{}", "more_body": False}

    return Request(scope, receive)


async def test_optional_idempotency(session_factory: async_sessionmaker[AsyncSession]) -> None:
    calls: list[int] = []

    @idempotent("test.create", required=False)
    async def create(*, request: Request, session: AsyncSession) -> dict[str, int]:
        del request, session
        calls.append(1)
        return {"n": len(calls)}

    async with session_factory() as session:
        assert await create(request=_request(), session=session) == {"n": 1}
        assert await create(request=_request(), session=session) == {"n": 2}  # no key, no replay
        headers = {"Idempotency-Key": "k-1"}
        assert await create(request=_request(headers), session=session) == {"n": 3}
        replay = await create(request=_request(headers), session=session)
        assert replay.headers["Idempotent-Replayed"] == "true"
        stored = await session.scalar(select(func.count()).select_from(IdempotencyKey))
    assert calls == [1, 1, 1] and stored == 1


# ----------------------------------------------------------------------------- admin CLI
def test_password_from_stdin_never_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("senha-bem-longa-123\n"))
    assert read_password(from_stdin=True) == "senha-bem-longa-123"


async def test_admin_bootstrap_is_idempotent(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        assert await admin_bootstrap("Dono@Test.dev", "curta", "Dono", session) == 2
    async with session_factory() as session:
        assert await admin_bootstrap("Dono@Test.dev", "senha-bem-longa-123", "Dono", session) == 0
    async with session_factory() as session:
        assert await admin_bootstrap("dono@test.dev", "outra-senha-longa-1", "Dono", session) == 0
        users = (await session.execute(select(AdminUser))).scalars().all()
    assert [(u.email, u.platform_role) for u in users] == [("dono@test.dev", "superadmin")]


async def test_admin_grant(session_factory: async_sessionmaker[AsyncSession]) -> None:
    await create_tenant(session_factory, "modelo")

    async def grant(**kwargs: Any) -> int:
        async with session_factory() as session:
            return await admin_grant(session=session, **kwargs)

    base = {"email": "loja@test.dev", "tenant_slug": "modelo"}
    assert await grant(**base, role="owner") == 1  # user missing, no --create
    assert await grant(**base, role="owner", create=True, password="curta") == 2
    assert await grant(email="x@test.dev", tenant_slug="nada", role="owner") == 1
    assert await grant(**base, role="dono") == 2
    assert await grant(**base, role="owner", create=True, password=TEST_PASSWORD) == 0
    assert await grant(**base, role="owner") == 0  # no-op
    assert await grant(**base, role="ops") == 0  # role change

    async with session_factory() as session:
        rows = (await session.execute(select(TenantMembership.role))).scalars().all()
    assert rows == ["ops"]


async def test_ops_features_lists_every_known_flag(
    client: AsyncClient,
    operator_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A tenant created before a flag existed has no row for it; ops still sees it (off)."""
    tenant = await create_tenant(session_factory, "antiga")
    async with session_factory() as session:
        await session.execute(
            delete(TenantFeatureFlag)
            .where(TenantFeatureFlag.key == "catalog")
            .execution_options(**CROSS)
        )
        await session.commit()
    response = await client.get(
        f"/api/v1/ops/tenants/{tenant.id}/features", headers=operator_headers
    )
    assert response.status_code == 200
    assert set(response.json()) == set(DEFAULT_FEATURE_FLAGS)
    assert response.json()["catalog"] is False
