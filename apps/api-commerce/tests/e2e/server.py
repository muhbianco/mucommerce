"""api-commerce for the Playwright E2E suite (apps/web/e2e): SQLite file, seeded, on one port.

    python tests/e2e/server.py            # from apps/api-commerce; E2E_API_PORT (default 8791)

No Docker, MariaDB, Redis or MinIO: Celery runs eagerly, rate limits and caches fall back to
memory, and images are written as already processed. Stores (hosts resolve to 127.0.0.1 in
Chromium, `*.localhost` is a secure context so `__Host-` cookies work over http):

- `loja.localhost`           tenant `muhbianco`, public, with published products (one with sizes
                             P/M/G, M paused, tagged `algodão`)
                             and a workshop event (`events` on) with two lots
- `fechada.loja.localhost`   tenant `fechada`, whitelist (catalog needs an approved customer);
                             customers sign in with Google (apps/web/e2e/fake-google.mjs); green
                             brand colour and serif font
- `suspensa.loja.localhost`  tenant `suspensa`, suspended (503)

`muhbianco` takes payments with the in-memory fake provider (Pix). Test-only routes, never in
the image (this file is not shipped):

- `POST /__e2e/payments/settle`  {"tenant", "status"}: the store's latest open payment is paid
  (or refused) at the fake provider, which then sends its signed webhook to the real endpoint;
- `POST /__e2e/tick`             {"minutes"}: runs the beat jobs (webhook sweep, reconciliation,
  order expiry, refunds, outbox consumers and e-mails) as if `minutes` had passed.

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
from datetime import UTC, datetime, timedelta
from pathlib import Path

PORT = int(os.environ.get("E2E_API_PORT", "8791"))
GOOGLE_PORT = int(os.environ.get("E2E_GOOGLE_PORT", "8792"))
WEB_PORT = int(os.environ.get("E2E_WEB_PORT", "3100"))
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
    # Store customers sign in against apps/web/e2e/fake-google.mjs.
    "GOOGLE_CUSTOMER_CLIENT_ID": "e2e-client",
    "GOOGLE_CUSTOMER_CLIENT_SECRET": "e2e-" + "x" * 20,  # the fake Google does not check it
    "GOOGLE_CUSTOMER_REDIRECT_URI": f"http://api.localhost:{PORT}/api/v1/auth/google/callback",
    "GOOGLE_OIDC_ISSUER": f"http://127.0.0.1:{GOOGLE_PORT}",
    "GOOGLE_OIDC_AUTHORIZE_URL": f"http://127.0.0.1:{GOOGLE_PORT}/o/oauth2/v2/auth",
    "GOOGLE_OIDC_TOKEN_URL": f"http://127.0.0.1:{GOOGLE_PORT}/token",
    "GOOGLE_OIDC_JWKS_URL": f"http://127.0.0.1:{GOOGLE_PORT}/certs",
    "STOREFRONT_ORIGIN_TEMPLATE": f"http://{{host}}:{WEB_PORT}",
    "REDIS_URL": "",
    # The store's e-mails go to apps/web/e2e/fake-n8n.mjs (the workflow checks the signature).
    "NOTIFY_N8N_URL": os.environ.get("NOTIFY_N8N_URL", ""),
    "NOTIFY_N8N_SECRET": os.environ.get("NOTIFY_N8N_SECRET", ""),
    "PAYMENTS_ALLOWED_PROVIDERS": "fake",
    "PAYMENTS_FAKE_WEBHOOK_SECRET": "e2e-" + "w" * 32,
    "CELERY_BROKER_URL": "",
    "METRICS_ENABLED": "false",
    "DOCS_ENABLED": "false",
    "LOG_LEVEL": "WARNING",
}
# Before any `app` import: settings are read once, at import time.
os.environ.update(E2E_ENV)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import APIRouter  # noqa: E402
from pydantic import BaseModel  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker  # noqa: E402

from app.audit import outbox  # noqa: E402
from app.catalog.events import EventService  # noqa: E402
from app.catalog.schemas import (  # noqa: E402
    EventUpsert,
    LotCreate,
    ModifierGroupIn,
    ModifierIn,
    ProductCreate,
    ProductModifiersUpdate,
    ProductOption,
    ProductOptionsUpdate,
    VariantUpdate,
)
from app.catalog.service import CatalogService  # noqa: E402
from app.cli import _seed_platform_in_session  # noqa: E402
from app.core.database import SessionFactory, create_app_engine  # noqa: E402
from app.main import app  # noqa: E402
from app.media.models import MediaAsset, MediaStatus  # noqa: E402
from app.models.all import Base  # noqa: E402  (every model, for create_all)
from app.models.base import utcnow  # noqa: E402
from app.notifications.jobs import run_send_notifications  # noqa: E402
from app.orders.jobs import run_expire_orders  # noqa: E402
from app.payments.config_service import PaymentConfigIn, PaymentConfigService  # noqa: E402
from app.payments.jobs import run_reconcile_payments  # noqa: E402
from app.payments.models import Payment  # noqa: E402
from app.payments.providers import fake  # noqa: E402
from app.payments.refunds import run_process_refunds  # noqa: E402
from app.payments.webhooks import run_process_webhooks  # noqa: E402
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


async def _ready_image(session: AsyncSession, tenant_id: str, product_id: str) -> None:
    bind_session_tenant(session, tenant_id)
    session.add(
        MediaAsset(
            owner_type="product",
            owner_id=product_id,
            status=MediaStatus.READY,
            declared_mime="image/png",
            declared_bytes=1,
            upload_key=f"incoming/{tenant_id}/{product_id}",
            width=600,
            height=600,
            renditions={
                "w600": {
                    "key": f"tenants/{tenant_id}/{product_id}.webp",
                    "width": 600,
                    "height": 600,
                }
            },
        )
    )
    await session.flush()


async def _publish_products(session: AsyncSession, tenant_id: str) -> None:
    context = await TenantResolver(session).resolve_by_id(tenant_id)
    catalog = CatalogService(session, context, ACTOR)
    for name, price in PRODUCTS:
        view = await catalog.create_product(ProductCreate(name=name, base_price_cents=price))
        await _ready_image(session, tenant_id, view.product.id)
        await catalog.publish(view.product.id)


async def _variant_product(session: AsyncSession, tenant_id: str) -> None:
    """Sizes P/M/G (G dearer, M paused), an optional paid print, tagged `algodão`."""
    context = await TenantResolver(session).resolve_by_id(tenant_id)
    catalog = CatalogService(session, context, ACTOR)
    view = await catalog.create_product(
        ProductCreate(
            name="Camiseta MuhBianco",
            base_price_cents=5900,
            stock_policy="unlimited",
            tags=["algodão"],
        )
    )
    product_id = view.product.id
    await _ready_image(session, tenant_id, product_id)
    size = ProductOption(name="Tamanho", values=["P", "M", "G"])
    view = await catalog.set_options(product_id, ProductOptionsUpdate(options=[size]))
    _, medium, large = view.variants
    await catalog.update_variant(product_id, large.id, VariantUpdate(price_cents=6900))
    print_ = ModifierIn(name="Personalizada", price_cents=1000)
    extras = ModifierGroupIn(name="Estampa", max_select=1, modifiers=[print_])
    await catalog.set_modifiers(product_id, ProductModifiersUpdate(groups=[extras]))
    await catalog.publish(product_id)
    await catalog.pause_variant(product_id, medium.id, reason="E2E")


async def _event_product(session: AsyncSession, tenant_id: str) -> None:
    """A workshop in 20 days: lot 1 on sale, lot 2 later; capacity leaves room for one more."""
    context = await TenantResolver(session).resolve_by_id(tenant_id)
    catalog = CatalogService(session, context, ACTOR)
    events = EventService(session, context, ACTOR)
    view = await catalog.create_product(
        ProductCreate(name="Oficina de Brownie", base_price_cents=0, kind="ticket")
    )
    product_id = view.product.id
    await _ready_image(session, tenant_id, product_id)
    starts = (datetime.now(UTC) + timedelta(days=20)).replace(minute=0, second=0, microsecond=0)
    await events.upsert(
        product_id,
        EventUpsert(
            starts_at=starts,
            ends_at=starts + timedelta(hours=3),
            venue_name="Cozinha MuhBianco",
            city="São Paulo",
            capacity=40,
        ),
    )
    await events.add_lot(product_id, LotCreate(name="1º lote", price_cents=12000, quantity=20))
    second = LotCreate(
        name="2º lote",
        price_cents=15000,
        quantity=10,
        sales_starts_at=starts - timedelta(days=5),
    )
    await events.add_lot(product_id, second)
    await catalog.publish(product_id)


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
        await service.set_features(
            store,
            {
                "catalog": True,
                "inventory": True,
                "events": True,
                "checkout": True,
                "pickup": True,
                "customer_login": True,
            },
            ACTOR,
        )
        await service.set_setting(store, "storefront", {"access_mode": "public"}, ACTOR)
        pickup = {"name": "Loja MuhBianco", "address": "Rua E2E, 100"}
        await service.set_setting(
            store, "fulfillment", {"pickup": {"enabled": True, "locations": [pickup]}}, ACTOR
        )
        await session.commit()
        await _publish_products(session, store.id)
        await _variant_product(session, store.id)
        await _event_product(session, store.id)
        context = await TenantResolver(session).resolve_by_id(store.id)
        await PaymentConfigService(session, context, ACTOR).save(
            "fake", PaymentConfigIn(enabled=True, is_default=True, methods=["pix"])
        )
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
        await service.set_features(
            await service.get_or_404(closed_id),
            {"catalog": True, "customer_login": True, "customer_phone_otp": True},
            ACTOR,
        )
        await service.set_setting(
            await service.get_or_404(closed_id),
            "branding",
            {"primary_color": "#2e7d32", "font": "serif"},
            ACTOR,
        )
        await session.commit()
        await _publish_products(session, closed_id)
        await session.commit()

    async with factory() as session:  # a suspended store answers 503
        created = await TenantService(session).create(slug="suspensa", name="Suspensa", actor=ACTOR)
        created.status = TenantStatus.SUSPENDED
        await session.commit()
    await engine.dispose()


# ------------------------------------------------------------------------------ /__e2e routes
e2e = APIRouter(prefix="/__e2e", include_in_schema=False)


class SettleIn(BaseModel):
    tenant: str = "muhbianco"
    status: str = "approved"


class TickIn(BaseModel):
    minutes: int = 0


@e2e.post("/payments/settle")
async def settle_latest(body: SettleIn) -> dict[str, object]:
    async with SessionFactory() as session:
        tenant = await TenantService(session).repo.get_by_slug(body.tenant)
        assert tenant is not None
        bind_session_tenant(session, tenant.id)
        payment = await session.scalar(
            select(Payment)
            .where(Payment.active_order_id.is_not(None))
            .order_by(Payment.created_at.desc())
            .limit(1)
        )
        if payment is None or payment.provider_payment_id is None:
            return {"settled": None}
        public_key = tenant.public_key
    fake.settle(payment.provider_payment_id, body.status)
    raw = fake.webhook_body(payment.provider_payment_id)
    async with httpx.AsyncClient(timeout=10) as client:
        sent = await client.post(
            f"http://127.0.0.1:{PORT}/api/v1/webhooks/fake/{public_key}",
            content=raw,
            headers={
                "host": E2E_ENV["API_PUBLIC_HOST"],
                "content-type": "application/json",
                "x-fake-signature": fake.signature(raw),
            },
        )
    return {"settled": payment.id, "webhook": sent.status_code}


@e2e.post("/tick")
async def tick(body: TickIn) -> dict[str, int]:
    """The beat, on demand: webhooks, reconciliation, deadlines, refunds, outbox and e-mails."""
    now = utcnow() + timedelta(minutes=body.minutes)
    counts = {
        "webhooks": await run_process_webhooks(SessionFactory, now),
        "reconciled": await run_reconcile_payments(SessionFactory, now),
        "expired": await run_expire_orders(SessionFactory, now),
        "refunds": await run_process_refunds(SessionFactory, now),
    }
    async with SessionFactory() as session:
        dispatch = await outbox.relay_pending(session)
        await session.commit()
    for event_id, consumer in dispatch:
        async with SessionFactory() as session:
            await outbox.deliver(session, event_id, consumer)
            await session.commit()
    counts["events"] = len(dispatch)
    counts["emails"] = await run_send_notifications(SessionFactory, utcnow())
    return counts


def main() -> None:
    register_tenant_filter()
    DB_FILE.unlink(missing_ok=True)
    asyncio.run(seed())
    app.include_router(e2e)
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
