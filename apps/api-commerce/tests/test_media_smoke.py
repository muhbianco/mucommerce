"""`python -m app.cli media smoke`: the production media check, against fake storage and HTTP."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.audit.models import OutboxEvent
from app.cli import main
from app.core import storage as storage_module
from app.core.config import settings
from app.core.storage import FakeStorage
from app.media.models import MediaAsset
from app.media.service import process_media
from app.media.smoke import run_media_smoke, smoke_image
from app.tenancy.context import CROSS_TENANT_OPTION
from tests.conftest import create_tenant
from tests.test_media import tx_for

CROSS = {CROSS_TENANT_OPTION: True}


@pytest.fixture(autouse=True)
def public_storage_host(monkeypatch: pytest.MonkeyPatch) -> None:
    # Renditions are read back from the public host, the one FakeStorage signs uploads for.
    monkeypatch.setattr(settings, "storage_public_url", FakeStorage().public_base)


def storage_http(fake: FakeStorage, *, upload_status: int = 204) -> httpx.AsyncClient:
    """The public storage host: POST stores the file (like MinIO), HEAD serves stored objects."""
    prefix = f"{fake.public_base}/"

    def handler(request: httpx.Request) -> httpx.Response:
        path = str(request.url).removeprefix(prefix)
        if request.method == "POST":
            if upload_status == 204:
                # The form's `key` field is the one the ticket reserved; the file is the smoke PNG.
                fake.upload(path, fake.posts[-1][1], smoke_image(), "image/png")
            return httpx.Response(upload_status)
        bucket, _, key = path.partition("/")
        stored = fake.objects.get((bucket, key))
        if stored is None:
            return httpx.Response(404)
        headers = {"content-type": stored.content_type, "cache-control": stored.cache_control}
        return httpx.Response(200, headers=headers)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def worker(
    factory: async_sessionmaker[AsyncSession], fake: FakeStorage
) -> Callable[[float], Awaitable[None]]:
    """Stands in for relay + media worker: each poll processes whatever is waiting."""

    async def sleep(_: float) -> None:
        async with factory() as session:
            rows = (
                await session.execute(
                    select(MediaAsset.id, MediaAsset.tenant_id)
                    .where(MediaAsset.status == "processing")
                    .execution_options(**CROSS)
                )
            ).all()
        for media_id, tenant_id in rows:
            await process_media(tx_for(factory), fake, tenant_id=tenant_id, media_id=media_id)

    return sleep


async def events(factory: async_sessionmaker[AsyncSession]) -> list[str]:
    async with factory() as session:
        stmt = select(OutboxEvent.event_type).order_by(OutboxEvent.id).execution_options(**CROSS)
        return list((await session.execute(stmt)).scalars())


async def remaining_media(factory: async_sessionmaker[AsyncSession]) -> int:
    async with factory() as session:
        stmt = select(MediaAsset.id).execution_options(**CROSS)
        return len((await session.execute(stmt)).all())


async def test_smoke_passes_end_to_end_and_leaves_nothing_behind(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await create_tenant(session_factory, "smoke")
    fake = FakeStorage()
    async with storage_http(fake) as http:
        result = await run_media_smoke(
            session_factory,
            fake,
            http,
            tenant_slug="smoke",
            poll_s=0,
            sleep=worker(session_factory, fake),
        )

    assert result.ok, result
    assert result.step == "done" and result.detail.startswith("w600 600px")
    # The upload went through the private incoming/ prefix, capped at the file's exact size.
    bucket, key, max_bytes = fake.posts[0]
    assert bucket == settings.storage_private_bucket and key.startswith("incoming/")
    assert max_bytes > 0
    # Deleted at the end: no row left, and the objects go away through media.deleted.
    assert await remaining_media(session_factory) == 0
    assert (await events(session_factory))[-1] == "media.deleted"


async def test_smoke_reports_the_failing_step_and_still_cleans_up(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await create_tenant(session_factory, "smoke")
    fake = FakeStorage()

    async with storage_http(fake, upload_status=403) as http:
        refused = await run_media_smoke(session_factory, fake, http, tenant_slug="smoke")
    assert (refused.ok, refused.step) == (False, "upload")
    assert "403" in refused.detail
    assert await remaining_media(session_factory) == 0

    async def no_worker(_: float) -> None:
        return None

    async with storage_http(fake) as http:
        stuck = await run_media_smoke(
            session_factory, fake, http, tenant_slug="smoke", timeout_s=0, sleep=no_worker
        )
    assert (stuck.ok, stuck.step) == (False, "process")
    assert await remaining_media(session_factory) == 0


async def test_smoke_unknown_tenant(session_factory: async_sessionmaker[AsyncSession]) -> None:
    fake = FakeStorage()
    async with storage_http(fake) as http:
        result = await run_media_smoke(session_factory, fake, http, tenant_slug="nope")
    assert (result.ok, result.step, result.media_id) == (False, "tenant", None)
    assert fake.posts == []


@pytest.mark.parametrize("cache_control", ["", "public, max-age=60"])
async def test_smoke_fails_when_renditions_are_not_immutable(
    session_factory: async_sessionmaker[AsyncSession], cache_control: str
) -> None:
    await create_tenant(session_factory, "smoke")
    fake = FakeStorage()
    process = worker(session_factory, fake)

    async def worker_then_break_headers(delay: float) -> None:
        await process(delay)
        for stored in fake.objects.values():
            stored.cache_control = cache_control

    async with storage_http(fake) as http:
        result = await run_media_smoke(
            session_factory,
            fake,
            http,
            tenant_slug="smoke",
            poll_s=0,
            sleep=worker_then_break_headers,
        )
    assert (result.ok, result.step) == (False, "public")


def test_cli_refuses_without_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(storage_module, "_storage", None)
    monkeypatch.setattr(settings, "storage_endpoint", "")
    assert main(["media", "smoke", "--tenant", "muhbianco"]) == 2
