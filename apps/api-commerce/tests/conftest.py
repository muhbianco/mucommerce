from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator

os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("RUN_MIGRATIONS_ON_STARTUP", "false")
os.environ.setdefault("DATABASE_URL_OVERRIDE", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("JWT_SECRET", "test-secret-" + "x" * 40)
os.environ.setdefault("INTERNAL_TOKEN_WEB", "web-token-test")
os.environ.setdefault("INTERNAL_TOKEN_TRAEFIK", "traefik-token-test")
os.environ.setdefault("INTERNAL_TOKEN_AGENTS", "agents-token-test")
os.environ.setdefault("CREDENTIALS_MASTER_KEY", "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=")
os.environ.setdefault("PLATFORM_BASE_DOMAIN", "loja.test")
os.environ.setdefault("PANEL_HOST", "painel.test")
os.environ.setdefault("API_PUBLIC_HOST", "api.test")
os.environ.setdefault("EDGE_CNAME_TARGET", "edge.test")
os.environ.setdefault("EDGE_PUBLIC_IPS", "203.0.113.10")
os.environ.setdefault("LOGIN_RATE_LIMIT_ATTEMPTS", "100")
os.environ.setdefault("REDIS_URL", "")
os.environ.setdefault("CELERY_BROKER_URL", "")
os.environ.setdefault("METRICS_ENABLED", "false")

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from starlette.routing import Mount

from app.core.config import settings
from app.core.database import get_session, mariadb_engine_options
from app.core.rate_limit import rate_limiter
from app.core.scopes import PlatformRole, TenantRole
from app.customers import oidc
from app.identity.models import AdminUser
from app.identity.service import AdminAuthService
from app.main import app
from app.models.all import Base
from app.tenancy.models import Tenant, TenantStatus
from app.tenancy.resolver import host_cache
from app.tenancy.service import Actor, TenantService

TEST_PASSWORD = "senha-de-teste-123456"


def _mounted_apps() -> Iterator[FastAPI]:
    yield app
    for route in app.routes:
        if isinstance(route, Mount) and isinstance(route.app, FastAPI):
            yield route.app


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """In-memory SQLite by default; TEST_DATABASE_URL points it at a real MariaDB (CI runs the
    leak suite there, where row locks, collations and SQL rendering are the production ones)."""
    external_url = os.environ.get("TEST_DATABASE_URL")
    if external_url:
        engine = create_async_engine(external_url, **mariadb_engine_options())
    else:
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
    async with engine.begin() as connection:
        if external_url:
            await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    # Same session options as production (app.core.database.SessionFactory).
    factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )
    host_cache.clear_memory()
    rate_limiter._memory.clear()
    yield factory
    if external_url:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def client(session_factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncClient]:
    async def override_get_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    for mounted in _mounted_apps():
        mounted.dependency_overrides[get_session] = override_get_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://loja.test") as async_client:
        yield async_client

    for mounted in _mounted_apps():
        mounted.dependency_overrides.clear()


async def create_tenant(
    session_factory: async_sessionmaker[AsyncSession], slug: str, *, active: bool = True
) -> Tenant:
    async with session_factory() as session:
        tenant = await TenantService(session).create(
            slug=slug, name=slug.title(), actor=Actor.system("tests")
        )
        if active:
            tenant.status = TenantStatus.ACTIVE
        await session.commit()
        await session.refresh(tenant)
        return tenant


async def create_admin(
    session_factory: async_sessionmaker[AsyncSession],
    email: str,
    *,
    platform_role: PlatformRole | None = None,
    memberships: dict[str, TenantRole] | None = None,
) -> AdminUser:
    async with session_factory() as session:
        service = AdminAuthService(session)
        user = await service.create_user(
            email=email,
            full_name="Teste",
            password=TEST_PASSWORD,
            platform_role=str(platform_role) if platform_role else None,
            actor="system:tests",
        )
        for tenant_id, role in (memberships or {}).items():
            await service.add_membership(
                user=user, tenant_id=tenant_id, role=str(role), actor="system:tests"
            )
        await session.commit()
        await session.refresh(user)
        return user


async def login(client: AsyncClient, email: str) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/token",
        data={"username": email, "password": TEST_PASSWORD},
        headers={"host": "painel.test"},
    )
    assert response.status_code == 200, response.text
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}", "host": "painel.test"}


@pytest.fixture
async def operator_headers(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> dict[str, str]:
    await create_admin(session_factory, "ops@muhbianco.test", platform_role=PlatformRole.OPERATOR)
    return await login(client, "ops@muhbianco.test")


# ----------------------------------------------------------------------------- store customers
@pytest.fixture
def customer_login_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Google client of the stores configured; the cached Google key set is forgotten."""
    monkeypatch.setattr(settings, "google_customer_client_id", CUSTOMER_CLIENT_ID)
    monkeypatch.setattr(settings, "google_customer_client_secret", SecretStr("s"))
    oidc.reset_key_cache()


CUSTOMER_CLIENT_ID = "store-client.apps.googleusercontent.com"
