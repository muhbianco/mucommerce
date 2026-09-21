"""api-commerce for the Playwright E2E suite (apps/web/e2e): SQLite file, seeded, on one port.

    python tests/e2e/server.py            # from apps/api-commerce; E2E_API_PORT (default 8791)

No Docker, MariaDB, Redis or MinIO: Celery runs eagerly, rate limits and caches fall back to
memory, and images are written as already processed. Stores (hosts resolve to 127.0.0.1 in
Chromium, `*.localhost` is a secure context so `__Host-` cookies work over http):

- `loja.localhost`           tenant `muhbianco`, public, with published products
- `fechada.loja.localhost`   tenant `fechada`, whitelist (catalog needs an approved customer)

Panel login goes through apps/web/e2e/fake-accounts.mjs, which plays the MuhBianco accounts
service (api-agents) at MUHBIANCO_ACCOUNTS_INTERNAL_URL. The file lives under tests/ so it never
ships in the image (.dockerignore).
"""

from __future__ import annotations

import asyncio
import base64
import os
import sys
import tempfile
from pathlib import Path

PORT = int(os.environ.get("E2E_API_PORT", "8791"))
DB_FILE = Path(os.environ.get("E2E_DB_FILE", Path(tempfile.gettempdir()) / "mucommerce-e2e.db"))

E2E_ENV = {
    "ENVIRONMENT": "development",
    "RUN_MIGRATIONS_ON_STARTUP": "false",
    "DATABASE_URL_OVERRIDE": f"sqlite+aiosqlite:///{DB_FILE.as_posix()}",
    "JWT_SECRET": "e2e-" + "x" * 40,
    "INTERNAL_TOKEN_WEB": "e2e-web-token",
    "INTERNAL_TOKEN_TRAEFIK": "e2e-traefik-token",
    "INTERNAL_TOKEN_AGENTS": "e2e-agents-token",
    # Test-only key, built here so no key-shaped literal lands in the repo (gitleaks scans HEAD).
    "CREDENTIALS_MASTER_KEY": base64.b64encode(b"0123456789abcdef" * 2).decode(),
    "PLATFORM_TENANT_SLUG": "muhbianco",
    "PLATFORM_BASE_DOMAIN": "loja.localhost",
    "PANEL_HOST": "painel.localhost",
    "API_PUBLIC_HOST": "api.localhost",
    "EDGE_CNAME_TARGET": "edge.localhost",
    "EDGE_PUBLIC_IPS": "127.0.0.1",
    "MUHBIANCO_ACCOUNTS_INTERNAL_URL": os.environ.get(
        "MUHBIANCO_ACCOUNTS_INTERNAL_URL", "http://127.0.0.1:8790"
    ),
    "STORAGE_PUBLIC_URL": "http://storage.localhost",
    "REDIS_URL": "",
    "CELERY_BROKER_URL": "",
    "METRICS_ENABLED": "false",
    "DOCS_ENABLED": "false",
    "LOG_LEVEL": "WARNING",
}
# Before any `app` import: settings are read once, at import time.
os.environ.update(E2E_ENV)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import uvicorn  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker  # noqa: E402

from app.catalog.schemas import ProductCreate  # noqa: E402
from app.catalog.service import CatalogService  # noqa: E402
from app.cli import _seed_platform_in_session  # noqa: E402
from app.core.database import create_app_engine  # noqa: E402
from app.media.models import MediaAsset, MediaStatus  # noqa: E402
from app.models.base import Base  # noqa: E402
from app.tenancy.context import bind_session_tenant  # noqa: E402
from app.tenancy.models import TenantStatus  # noqa: E402
from app.tenancy.orm_filter import register_tenant_filter  # noqa: E402
from app.tenancy.resolver import TenantResolver  # noqa: E402
from app.tenancy.service import Actor, TenantService  # noqa: E402

ACTOR = Actor.system("e2e")
PRODUCTS = [
    ("Brownie de chocolate", 1500),
    ("Cookie de aveia", 900),
    ("Bolo de cenoura", 4200),
]


async def _publish_products(session: AsyncSession, tenant_id: str) -> None:
    context = await TenantResolver(session).resolve_by_id(tenant_id)
    catalog = CatalogService(session, context, ACTOR)
    for name, price in PRODUCTS:
        view = await catalog.create_product(ProductCreate(name=name, base_price_cents=price))
        product = view.product
        bind_session_tenant(session, tenant_id)
        session.add(
            MediaAsset(
                owner_type="product",
                owner_id=product.id,
                status=MediaStatus.READY,
                declared_mime="image/png",
                declared_bytes=1,
                upload_key=f"incoming/{tenant_id}/{product.id}",
                width=600,
                height=600,
                renditions={
                    "w600": {
                        "key": f"tenants/{tenant_id}/{product.id}.webp",
                        "width": 600,
                        "height": 600,
                    }
                },
            )
        )
        await session.flush()
        await catalog.publish(product.id)


async def seed() -> None:
    engine = create_app_engine(E2E_ENV["DATABASE_URL_OVERRIDE"], pooled=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)

    async with factory() as session:
        await _seed_platform_in_session(session)
        service = TenantService(session)
        platform = await service.repo.get_by_slug("muhbianco")
        assert platform is not None
        store = await service.get_or_404(platform.id)  # loads domains (settings invalidate caches)
        await service.set_features(store, {"catalog": True, "inventory": True}, ACTOR)
        await service.set_setting(store, "storefront", {"access_mode": "public"}, ACTOR)
        await session.commit()
        await _publish_products(session, store.id)
        await session.commit()

    async with factory() as session:
        created = await TenantService(session).create(
            slug="fechada", name="Loja Fechada", actor=ACTOR
        )
        created.status = TenantStatus.ACTIVE
        closed_id = created.id
        await session.commit()
    async with factory() as session:  # fresh session: `domains` is loaded by get_or_404
        service = TenantService(session)
        await service.set_features(await service.get_or_404(closed_id), {"catalog": True}, ACTOR)
        await session.commit()
        await _publish_products(session, closed_id)
        await session.commit()
    await engine.dispose()


def main() -> None:
    register_tenant_filter()
    DB_FILE.unlink(missing_ok=True)
    asyncio.run(seed())
    uvicorn.run("app.main:app", host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
