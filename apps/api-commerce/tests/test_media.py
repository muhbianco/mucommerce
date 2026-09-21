"""Media (phase 1, S4): image rules, direct upload flow, processing, cleanup, isolation."""

from __future__ import annotations

import io
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from PIL import Image
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.audit.models import OutboxEvent
from app.core import storage as storage_module
from app.core.config import settings
from app.core.storage import IMMUTABLE_CACHE, FakeStorage, get_storage
from app.media.imaging import InvalidImageError, process_image
from app.media.models import MediaAsset, MediaStatus
from app.media.service import MAX_PROCESS_ATTEMPTS, process_media, sweep_stale_media
from app.models.base import utcnow
from app.tenancy.context import CROSS_TENANT_OPTION
from app.workers.consumers import media_janitor
from tests.conftest import _mounted_apps
from tests.test_catalog import base, catalog_tenant, create_product, member_headers

CROSS = {CROSS_TENANT_OPTION: True}
PRIVATE = settings.storage_private_bucket
PUBLIC = settings.storage_public_bucket


def image_bytes(size: tuple[int, int], fmt: str = "JPEG", mode: str = "RGB", **save: Any) -> bytes:
    color: Any = {"1": 1, "RGBA": (255, 0, 0, 128)}.get(mode, "red")  # RGBA: half transparent
    out = io.BytesIO()
    Image.new(mode, size, color).save(out, fmt, **save)
    return out.getvalue()


# ----------------------------------------------------------------------------- imaging
def test_jpeg_is_rotated_by_exif_downscaled_and_stripped() -> None:
    exif = Image.Exif()
    exif[0x0112] = 6  # Orientation: rotate 90° clockwise to display
    exif[0x010F] = "Camera do vendedor"  # Make: metadata that must not survive
    processed = process_image(image_bytes((3000, 2000), exif=exif.tobytes()))

    assert processed.source_format == "JPEG"
    assert [r.name for r in processed.renditions] == ["orig", "w1200", "w600", "w320"]
    assert [(r.width, r.height) for r in processed.renditions] == [
        (1600, 2400),
        (800, 1200),
        (400, 600),
        (213, 320),
    ]
    with Image.open(io.BytesIO(processed.renditions[0].data)) as out:
        assert out.format == "WEBP"
        assert not out.getexif()


def test_small_png_keeps_alpha_and_skips_upscaling() -> None:
    processed = process_image(image_bytes((500, 300), "PNG", "RGBA"))
    assert [(r.name, r.width, r.height) for r in processed.renditions] == [
        ("orig", 500, 300),
        ("w320", 320, 192),
    ]
    with Image.open(io.BytesIO(processed.renditions[0].data)) as out:
        assert out.mode == "RGBA"


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"", "vazio"),
        (b"not an image at all", "reconhecida"),
        (image_bytes((10, 10), "GIF", "P"), "Formato"),
        (image_bytes((6000, 5000), "PNG", "1"), "24 megapixels"),
        (image_bytes((800, 600))[:400], "reconhecida"),  # header cut: not even opened
        (image_bytes((800, 600))[:-300], "corrompida"),  # scan data cut: fails on decode
    ],
    ids=["empty", "garbage", "gif", "pixel-bomb", "header-cut", "truncated"],
)
def test_bad_uploads_are_refused(data: bytes, message: str) -> None:
    with pytest.raises(InvalidImageError, match=message):
        process_image(data)


# ----------------------------------------------------------------------------- fixtures
@pytest.fixture
async def fake_storage(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[FakeStorage]:
    fake = FakeStorage()
    for mounted in _mounted_apps():
        mounted.dependency_overrides[get_storage] = lambda: fake
    monkeypatch.setattr(storage_module, "_storage", fake)
    yield fake
    for mounted in _mounted_apps():
        mounted.dependency_overrides.pop(get_storage, None)


def tx_for(
    factory: async_sessionmaker[AsyncSession],
) -> Callable[[Callable[[AsyncSession], Awaitable[Any]]], Awaitable[Any]]:
    async def tx(fn: Callable[[AsyncSession], Awaitable[Any]]) -> Any:
        async with factory() as session:
            result = await fn(session)
            await session.commit()
            return result

    return tx


async def request_upload(
    client: AsyncClient, tenant: Any, headers: dict[str, str], **body: Any
) -> dict[str, Any]:
    payload: dict[str, Any] = {"owner_type": "product", "mime": "image/jpeg", "bytes": 50_000}
    response = await client.post(
        f"{base(tenant)}/media/uploads", json=payload | body, headers=headers
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


async def uploaded_and_processed(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    fake: FakeStorage,
    tenant: Any,
    headers: dict[str, str],
    product_id: str,
    data: bytes | None = None,
) -> dict[str, Any]:
    data = data if data is not None else image_bytes((1500, 1000))
    created = await request_upload(
        client, tenant, headers, owner_id=product_id, bytes=len(data) or 1
    )
    fake.upload(PRIVATE, created["upload"]["fields"]["key"], data, "image/jpeg")
    media_id = created["media"]["id"]
    done = await client.post(f"{base(tenant)}/media/{media_id}/complete", headers=headers)
    assert done.status_code == 202, done.text
    await process_media(tx_for(session_factory), fake, tenant_id=tenant.id, media_id=media_id)
    detail = await client.get(f"{base(tenant)}/media/{media_id}", headers=headers)
    return dict(detail.json())


# ----------------------------------------------------------------------------- flow
async def test_upload_complete_process_and_publish(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    fake_storage: FakeStorage,
) -> None:
    tenant = await catalog_tenant(session_factory, "alpha")
    headers = await member_headers(client, session_factory, tenant)
    product = await create_product(client, tenant, headers)
    publish_url = f"{base(tenant)}/products/{product['id']}/publish"

    blocked = await client.post(publish_url, headers=headers)
    assert blocked.json()["error"]["details"] == {"missing": ["media"]}

    created = await request_upload(client, tenant, headers, owner_id=product["id"], bytes=1234)
    media_id = created["media"]["id"]
    assert created["media"]["status"] == "pending"
    key = created["upload"]["fields"]["key"]
    assert key == f"incoming/{tenant.id}/{media_id}"
    assert created["upload"]["fields"]["Content-Type"] == "image/jpeg"
    assert fake_storage.posts == [(PRIVATE, key, 1234)]  # policy capped at the declared size

    early = await client.post(f"{base(tenant)}/media/{media_id}/complete", headers=headers)
    assert early.status_code == 409

    data = image_bytes((1500, 1000))
    fake_storage.upload(PRIVATE, key, data, "image/jpeg")
    oversized = await client.post(f"{base(tenant)}/media/{media_id}/complete", headers=headers)
    assert oversized.status_code == 422  # bigger than declared

    fake_storage.upload(PRIVATE, key, data[:1234], "image/jpeg")
    done = await client.post(f"{base(tenant)}/media/{media_id}/complete", headers=headers)
    assert done.status_code == 202 and done.json()["status"] == "processing"
    again = await client.post(f"{base(tenant)}/media/{media_id}/complete", headers=headers)
    assert again.json()["status"] == "processing"
    async with session_factory() as session:
        events = (
            await session.execute(
                select(OutboxEvent.event_type)
                .where(OutboxEvent.aggregate_id == media_id)
                .execution_options(**CROSS)
            )
        ).scalars()
        assert list(events) == ["media.uploaded"]

    # Truncated upload: processed, refused, and it does not use a slot.
    status = await process_media(
        tx_for(session_factory), fake_storage, tenant_id=tenant.id, media_id=media_id
    )
    assert status == "failed"
    failed = (await client.get(f"{base(tenant)}/media/{media_id}", headers=headers)).json()
    assert failed["failure_reason"] == "Imagem corrompida ou incompleta."

    ready = await uploaded_and_processed(
        client, session_factory, fake_storage, tenant, headers, product["id"]
    )
    assert ready["status"] == "ready" and (ready["width"], ready["height"]) == (1500, 1000)
    assert [r["name"] for r in ready["renditions"]] == ["orig", "w1200", "w600", "w320"]
    prefix = f"/{PUBLIC}/tenants/{tenant.id}/media/{ready['id']}/"
    assert all(prefix in r["url"] and r["url"].endswith(".webp") for r in ready["renditions"])
    public = [obj for (bucket, _), obj in fake_storage.objects.items() if bucket == PUBLIC]
    assert len(public) == 4
    assert {(o.content_type, o.cache_control) for o in public} == {("image/webp", IMMUTABLE_CACHE)}

    published = await client.post(publish_url, headers=headers)
    assert published.status_code == 200
    detail = await client.get(f"{base(tenant)}/products/{product['id']}", headers=headers)
    assert [m["status"] for m in detail.json()["media"]] == ["failed", "ready"]
    listed = await client.get(f"{base(tenant)}/products", headers=headers)
    assert listed.json()["items"][0]["cover_url"].endswith("/w320.webp")


async def test_processing_is_idempotent(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    fake_storage: FakeStorage,
) -> None:
    tenant = await catalog_tenant(session_factory, "alpha")
    headers = await member_headers(client, session_factory, tenant)
    product = await create_product(client, tenant, headers)
    ready = await uploaded_and_processed(
        client, session_factory, fake_storage, tenant, headers, product["id"]
    )
    rerun = await process_media(
        tx_for(session_factory), fake_storage, tenant_id=tenant.id, media_id=ready["id"]
    )
    assert rerun == "ready"
    async with session_factory() as session:
        types = (
            await session.execute(
                select(OutboxEvent.event_type)
                .where(OutboxEvent.aggregate_id == ready["id"])
                .order_by(OutboxEvent.sequence)
                .execution_options(**CROSS)
            )
        ).scalars()
        assert list(types) == ["media.uploaded", "media.ready"]


async def test_delete_keeps_one_image_on_published_products_and_cleans_objects(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    fake_storage: FakeStorage,
) -> None:
    tenant = await catalog_tenant(session_factory, "alpha")
    headers = await member_headers(client, session_factory, tenant)
    product = await create_product(client, tenant, headers)
    first = await uploaded_and_processed(
        client, session_factory, fake_storage, tenant, headers, product["id"]
    )
    await client.post(f"{base(tenant)}/products/{product['id']}/publish", headers=headers)

    only = await client.delete(f"{base(tenant)}/media/{first['id']}", headers=headers)
    assert only.status_code == 409

    second = await uploaded_and_processed(
        client, session_factory, fake_storage, tenant, headers, product["id"]
    )
    public_before = {k for (b, k) in fake_storage.objects if b == PUBLIC}
    gone = await client.delete(f"{base(tenant)}/media/{first['id']}", headers=headers)
    assert gone.status_code == 204
    assert (
        await client.get(f"{base(tenant)}/media/{first['id']}", headers=headers)
    ).status_code == 404

    async with session_factory() as session:
        event = (
            await session.execute(
                select(OutboxEvent)
                .where(OutboxEvent.aggregate_id == first["id"])
                .where(OutboxEvent.event_type == "media.deleted")
                .execution_options(**CROSS)
            )
        ).scalar_one()
        await media_janitor(session, event)
        await media_janitor(session, event)  # retry-safe
    public_after = {k for (b, k) in fake_storage.objects if b == PUBLIC}
    assert len(public_before - public_after) == 4
    assert all(second["id"] in k for k in public_after)


async def test_owner_rules_slots_and_flags(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    fake_storage: FakeStorage,
) -> None:
    tenant = await catalog_tenant(session_factory, "alpha")
    headers = await member_headers(client, session_factory, tenant)
    product = await create_product(client, tenant, headers)
    url = f"{base(tenant)}/media/uploads"
    body = {"owner_type": "product", "mime": "image/jpeg", "bytes": 10}

    assert (await client.post(url, json=body, headers=headers)).status_code == 422
    brand_with_owner = body | {"owner_type": "tenant_brand", "owner_id": product["id"]}
    assert (await client.post(url, json=brand_with_owner, headers=headers)).status_code == 422
    assert (
        await client.post(
            url, json=body | {"mime": "image/gif", "owner_id": product["id"]}, headers=headers
        )
    ).status_code == 422
    too_big = body | {"owner_id": product["id"], "bytes": 10 * 1024 * 1024 + 1}
    assert (await client.post(url, json=too_big, headers=headers)).status_code == 422
    logo = await client.post(url, json=body | {"owner_type": "tenant_brand"}, headers=headers)
    assert logo.status_code == 201

    for _ in range(12):
        await request_upload(client, tenant, headers, owner_id=product["id"])
    full = await client.post(url, json=body | {"owner_id": product["id"]}, headers=headers)
    assert full.status_code == 409

    dark = await catalog_tenant(session_factory, "dark", catalog=False)
    dark_headers = await member_headers(client, session_factory, dark)
    off = await client.post(
        f"{base(dark)}/media/uploads",
        json=body | {"owner_id": product["id"]},
        headers=dark_headers,
    )
    assert off.status_code == 403


async def test_other_tenants_media_and_products_are_invisible(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    fake_storage: FakeStorage,
) -> None:
    alpha = await catalog_tenant(session_factory, "alpha")
    beta = await catalog_tenant(session_factory, "beta")
    alpha_headers = await member_headers(client, session_factory, alpha)
    beta_headers = await member_headers(client, session_factory, beta)
    beta_product = await create_product(client, beta, beta_headers)
    beta_media = await request_upload(client, beta, beta_headers, owner_id=beta_product["id"])
    media_id = beta_media["media"]["id"]

    foreign_owner = await client.post(
        f"{base(alpha)}/media/uploads",
        json={
            "owner_type": "product",
            "owner_id": beta_product["id"],
            "mime": "image/png",
            "bytes": 5,
        },
        headers=alpha_headers,
    )
    assert foreign_owner.status_code == 404
    for method, path in (
        ("GET", f"/media/{media_id}"),
        ("POST", f"/media/{media_id}/complete"),
        ("DELETE", f"/media/{media_id}"),
    ):
        response = await client.request(method, f"{base(alpha)}{path}", headers=alpha_headers)
        assert response.status_code == 404, (method, path)
    listed = await client.get(
        f"{base(alpha)}/media",
        params={"owner_type": "product", "owner_id": beta_product["id"]},
        headers=alpha_headers,
    )
    assert listed.json() == []


async def test_upload_without_storage_configured_is_503(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant = await catalog_tenant(session_factory, "alpha")
    headers = await member_headers(client, session_factory, tenant)
    response = await client.post(
        f"{base(tenant)}/media/uploads",
        json={"owner_type": "tenant_brand", "mime": "image/png", "bytes": 10},
        headers=headers,
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "storage_unavailable"


async def test_sweep_requeues_lost_runs_fails_hopeless_ones_and_drops_abandoned(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    fake_storage: FakeStorage,
) -> None:
    tenant = await catalog_tenant(session_factory, "alpha")
    headers = await member_headers(client, session_factory, tenant)
    ids = [
        (await request_upload(client, tenant, headers, owner_type="landing"))["media"]["id"]
        for _ in range(4)
    ]
    lost, hopeless, abandoned, fresh = ids
    old = utcnow() - timedelta(days=2)
    async with session_factory() as session:
        for media_id, status, attempts in (
            (lost, MediaStatus.PROCESSING, 1),
            (hopeless, MediaStatus.PROCESSING, MAX_PROCESS_ATTEMPTS),
            (abandoned, MediaStatus.PENDING, 0),
        ):
            await session.execute(
                update(MediaAsset)
                .where(MediaAsset.id == media_id)
                .values(status=status, attempts=attempts, updated_at=old)
                .execution_options(synchronize_session=False, **CROSS)
            )
        await session.commit()

    async with session_factory() as session:
        requeue = await sweep_stale_media(session)
        await session.commit()
    assert requeue == [(tenant.id, lost)]

    async with session_factory() as session:
        rows = {
            m.id: m.status
            for m in (
                await session.execute(select(MediaAsset).execution_options(**CROSS))
            ).scalars()
        }
    assert rows == {lost: "processing", hopeless: "failed", fresh: "pending"}
    assert abandoned not in rows
