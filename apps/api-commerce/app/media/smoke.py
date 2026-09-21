"""End-to-end media check against the real storage and worker (`python -m app.cli media smoke`).

Same path as the panel: reserve → browser-style POST to the public storage host → complete →
the media worker renders WebP (outbox relay + `commerce.media`) → anonymous HEAD of a rendition →
delete. The asset is a `landing` image that nothing references, so no catalog flag or product is
involved, and it is deleted at the end even when a step fails.
"""

from __future__ import annotations

import asyncio
import io
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx
from PIL import Image
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import ConflictError, FeatureDisabledError, NotFoundError
from app.core.logging import get_logger
from app.core.storage import IMMUTABLE_CACHE, ObjectStorage
from app.media.models import MediaStatus
from app.media.schemas import UploadCreate
from app.media.service import MediaService, pick_rendition
from app.tenancy.context import TenantContext, bind_session_tenant
from app.tenancy.repository import TenantRepository
from app.tenancy.resolver import TenantResolver
from app.tenancy.service import Actor

logger = get_logger("media.smoke")

ACTOR = Actor.system("media-smoke")
# 1400 px wide: the worker produces orig, w1200, w600 and w320, so w600 is always checked.
IMAGE_SIZE = (1400, 900)


@dataclass(frozen=True, slots=True)
class SmokeResult:
    ok: bool
    step: str
    detail: str
    media_id: str | None = None
    seconds: float = 0.0


def smoke_image() -> bytes:
    """A small, valid PNG with a gradient (compresses well; decodes like a real photo)."""
    width, height = IMAGE_SIZE
    image = Image.linear_gradient("L").resize((width, height)).convert("RGB")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


class TenantMissingError(Exception):
    pass


async def _context(session: AsyncSession, slug: str) -> TenantContext:
    tenant = await TenantRepository(session).get_by_slug(slug)
    if tenant is None:
        raise TenantMissingError(slug)
    context = await TenantResolver(session).build_context(tenant, None)
    bind_session_tenant(session, context.id)
    return context


async def run_media_smoke(
    factory: async_sessionmaker[AsyncSession],
    storage: ObjectStorage,
    http: httpx.AsyncClient,
    *,
    tenant_slug: str,
    timeout_s: float = 60.0,
    poll_s: float = 1.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> SmokeResult:
    started = time.monotonic()
    data = smoke_image()
    media_id: str | None = None

    def result(ok: bool, step: str, detail: str) -> SmokeResult:
        return SmokeResult(ok, step, detail, media_id, round(time.monotonic() - started, 2))

    try:
        # 1. Reserve, exactly like POST /media/uploads.
        async with factory() as session:
            context = await _context(session, tenant_slug)
            ticket = await MediaService(session, context, ACTOR, storage).create_upload(
                UploadCreate(
                    owner_type="landing",
                    mime="image/png",
                    bytes=len(data),
                    filename="media-smoke.png",
                    alt="media smoke (apagado automaticamente)",
                )
            )
            media_id = ticket.media.id
            await session.commit()

        # 2. What the browser does: multipart POST to the public storage host.
        response = await http.post(
            ticket.form.url,
            data=ticket.form.fields,
            files={"file": ("media-smoke.png", data, "image/png")},
        )
        if response.status_code not in (200, 201, 204):
            return result(False, "upload", f"storage respondeu {response.status_code}")

        # 3. Confirm, like POST /media/{id}/complete (emits media.uploaded).
        async with factory() as session:
            context = await _context(session, tenant_slug)
            await MediaService(session, context, ACTOR, storage).complete(media_id)
            await session.commit()

        # 4. Wait for the worker: relay (beat, 5 s) → consumer → process_media.
        deadline = time.monotonic() + timeout_s
        while True:
            async with factory() as session:
                context = await _context(session, tenant_slug)
                media = await MediaService(session, context, ACTOR).get(media_id)
            if media.status == MediaStatus.READY:
                break
            if media.status == MediaStatus.FAILED:
                return result(False, "process", f"worker falhou: {media.failure_reason}")
            if time.monotonic() >= deadline:
                return result(False, "process", f"ainda {media.status} após {timeout_s:.0f} s")
            await sleep(poll_s)

        # 5. Anonymous read of the rendition the storefront uses most (≤600 px).
        rendition = pick_rendition(media, max_width=600)
        if rendition is None:
            return result(False, "renditions", "nenhuma variante gerada")
        head = await http.head(str(rendition["url"]))
        content_type = head.headers.get("content-type", "")
        cache_control = head.headers.get("cache-control", "")
        if head.status_code != 200 or content_type != "image/webp":
            return result(
                False, "public", f"HEAD {rendition['name']}: {head.status_code} {content_type}"
            )
        if cache_control != IMMUTABLE_CACHE:
            return result(False, "public", f"cache-control inesperado: {cache_control!r}")
        return result(True, "done", f"{rendition['name']} {rendition['width']}px ok")
    except TenantMissingError:
        return result(False, "tenant", f"tenant {tenant_slug!r} não existe")
    except (ConflictError, FeatureDisabledError) as exc:
        return result(False, "api", f"{type(exc).__name__}: {exc}")
    except httpx.HTTPError as exc:
        return result(False, "http", f"{type(exc).__name__}: {exc}")
    finally:
        if media_id is not None:
            await _cleanup(factory, storage, tenant_slug, media_id)


async def _cleanup(
    factory: async_sessionmaker[AsyncSession],
    storage: ObjectStorage,
    tenant_slug: str,
    media_id: str,
) -> None:
    """Delete the asset; the media.deleted consumer removes its objects from both buckets."""
    async with factory() as session:
        try:
            context = await _context(session, tenant_slug)
            await MediaService(session, context, ACTOR, storage).delete(media_id)
            await session.commit()
        except (TenantMissingError, NotFoundError):
            return  # nothing left to delete
        except SQLAlchemyError:
            # Cleanup must not mask the smoke result; the leftover is logged for a manual delete.
            logger.exception("media smoke cleanup failed", extra={"media_id": media_id})
            await session.rollback()
