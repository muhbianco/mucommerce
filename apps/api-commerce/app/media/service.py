"""Media use cases: upload forms and confirmation (panel), processing and cleanup (worker).

Upload flow:
1. `create_upload` stores a `pending` row and returns a presigned POST form for the private
   bucket (`incoming/{tenant}/{media}`), limited to the declared size and MIME type;
2. the browser posts the file straight to storage (the API never proxies the bytes);
3. `complete` checks the object exists, marks the row `processing` and emits `media.uploaded`
   in the same transaction; an outbox consumer queues `process_media` on `commerce.media`.

Processing never holds a transaction across storage I/O: a short transaction claims the
attempt, the bytes are read and rendered outside of it, another short transaction records the
result. Rendition keys are immutable (content hash in the path), so a duplicate run writes the
same objects and the second finaliser is a no-op.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.outbox import emit
from app.audit.writer import audit
from app.catalog.models import PUBLISHED_STATUSES, ProductStatus, ProductVariant
from app.catalog.repository import CatalogRepository
from app.core.config import settings
from app.core.exceptions import (
    ConflictError,
    FeatureDisabledError,
    NotFoundError,
    ValidationError,
)
from app.core.ids import new_id
from app.core.logging import get_logger
from app.core.storage import (
    IMMUTABLE_CACHE,
    ObjectMissingError,
    ObjectStorage,
    ObjectTooLargeError,
    PresignedPost,
    public_object_url,
)
from app.media.imaging import MAX_UPLOAD_BYTES, InvalidImageError, process_image
from app.media.models import MediaAsset, MediaOwner, MediaStatus
from app.media.repository import MAX_MEDIA_PER_OWNER, MediaRepository
from app.media.schemas import MediaUpdate, UploadCreate
from app.models.base import utcnow
from app.tenancy.context import TenantContext, bind_session_tenant
from app.tenancy.service import Actor

logger = get_logger(__name__)

UPLOAD_TTL = timedelta(minutes=15)
# Longer than the Celery hard time limit (300 s): a run still in flight is never re-queued.
PROCESSING_STALE_AFTER = timedelta(minutes=6)
# Upload form expired long ago and nobody confirmed it; the object (if any) expires by ILM.
PENDING_ABANDONED_AFTER = timedelta(hours=24)
MAX_PROCESS_ATTEMPTS = 5

_UNSAFE_FILENAME = re.compile(r"[\x00-\x1f\x7f/\\]+")


def rendition_urls(media: MediaAsset) -> list[dict[str, Any]]:
    """Public renditions, largest first. Pure: works without a storage client."""
    renditions = media.renditions or {}
    items = [
        {
            "name": name,
            "url": public_object_url(settings.storage_public_bucket, str(info["key"])),
            "width": int(info["width"]),
            "height": int(info["height"]),
        }
        for name, info in renditions.items()
    ]
    return sorted(items, key=lambda item: item["width"], reverse=True)


def pick_rendition(media: MediaAsset, *, max_width: int) -> dict[str, Any] | None:
    """Largest rendition not wider than `max_width` (else the smallest one)."""
    urls = rendition_urls(media)  # largest first
    if not urls:
        return None
    fitting = [item for item in urls if item["width"] <= max_width]
    return fitting[0] if fitting else urls[-1]


async def ready_media_by_ids(session: AsyncSession, media_ids: list[str]) -> dict[str, MediaAsset]:
    """Ready images among `media_ids` (tenant-scoped session), by id."""
    if not media_ids:
        return {}
    stmt = (
        select(MediaAsset)
        .where(MediaAsset.id.in_(media_ids))
        .where(MediaAsset.status == MediaStatus.READY)
    )
    return {m.id: m for m in (await session.execute(stmt)).scalars()}


def _clean_filename(name: str | None) -> str | None:
    if not name:
        return None
    cleaned = _UNSAFE_FILENAME.sub("_", name).strip()[:200]
    return cleaned or None


@dataclass(frozen=True, slots=True)
class UploadTicket:
    media: MediaAsset
    form: PresignedPost
    expires_at: datetime


class MediaService:
    def __init__(
        self,
        session: AsyncSession,
        tenant: TenantContext,
        actor: Actor,
        storage: ObjectStorage | None = None,
    ) -> None:
        self.session = session
        self.tenant = tenant
        self.actor = actor
        self._storage = storage
        self.repo = MediaRepository(session)

    @property
    def storage(self) -> ObjectStorage:
        if self._storage is None:
            raise RuntimeError("MediaService needs storage for this operation")
        return self._storage

    async def create_upload(self, data: UploadCreate) -> UploadTicket:
        await self._check_owner(data.owner_type, data.owner_id)
        used = await self.repo.count_for_owner(data.owner_type, data.owner_id)
        if used >= MAX_MEDIA_PER_OWNER:
            raise ConflictError("Limite de imagens atingido.", limit=MAX_MEDIA_PER_OWNER)

        media_id = new_id()
        upload_key = f"incoming/{self.tenant.id}/{media_id}"
        expires_at = utcnow() + UPLOAD_TTL
        form = await self.storage.presigned_post(
            settings.storage_private_bucket,
            upload_key,
            content_type=data.mime,
            max_bytes=data.bytes,
            expires_at=expires_at,
        )
        media = MediaAsset(
            id=media_id,
            owner_type=data.owner_type,
            owner_id=data.owner_id,
            status=MediaStatus.PENDING,
            declared_mime=data.mime,
            declared_bytes=data.bytes,
            filename=_clean_filename(data.filename),
            upload_key=upload_key,
            alt=data.alt,
            position=used,
            created_by_actor=self.actor.id,
            updated_by_actor=self.actor.id,
        )
        self.session.add(media)
        await self.session.flush()
        await self._audit("media.upload_requested", media, {"bytes": data.bytes})
        return UploadTicket(media, form, expires_at)

    async def complete(self, media_id: str) -> MediaAsset:
        media = await self._get_or_404(media_id)
        if media.status != MediaStatus.PENDING:
            return media  # already confirmed: idempotent
        info = await self.storage.stat(settings.storage_private_bucket, media.upload_key)
        if info is None:
            raise ConflictError("Arquivo ainda não foi enviado.", code="upload_missing")
        if info.size > media.declared_bytes:
            # The POST policy already refuses this; defence in depth.
            raise ValidationError("Arquivo maior que o declarado.")
        media.status = MediaStatus.PROCESSING
        media.updated_by_actor = self.actor.id
        await self.session.flush()
        await self._audit("media.uploaded", media, {"bytes": info.size})
        await self._emit(media, "media.uploaded")
        return media

    async def get(self, media_id: str) -> MediaAsset:
        return await self._get_or_404(media_id)

    async def list_for_owner(self, owner_type: str, owner_id: str | None) -> list[MediaAsset]:
        return list(await self.repo.for_owner(owner_type, owner_id))

    async def update(self, media_id: str, data: MediaUpdate) -> MediaAsset:
        media = await self._get_or_404(media_id)
        changes = data.model_dump(exclude_unset=True)
        if "position" in changes and changes["position"] is None:
            raise ValidationError("Posição não pode ser nula.", fields=["position"])
        for name, value in changes.items():
            setattr(media, name, value)
        media.updated_by_actor = self.actor.id
        await self.session.flush()
        return media

    async def delete(self, media_id: str) -> None:
        media = await self._get_or_404(media_id)
        if media.owner_type == MediaOwner.PRODUCT and media.owner_id:
            product = await CatalogRepository(self.session).get_product(media.owner_id)
            ready = await self.repo.ready_for_owners(MediaOwner.PRODUCT, [media.owner_id])
            others = [m for m in ready.get(media.owner_id, []) if m.id != media.id]
            if product is not None and product.status in PUBLISHED_STATUSES and not others:
                raise ConflictError("Produto publicado precisa de ao menos uma imagem.")
        await self.session.execute(
            update(ProductVariant)
            .where(ProductVariant.media_id == media.id)
            .values(media_id=None)
            .execution_options(synchronize_session=False)
        )
        public_keys = [str(info["key"]) for info in (media.renditions or {}).values()]
        await self._audit("media.deleted", media, {"owner_type": media.owner_type})
        await emit(
            self.session,
            aggregate_type="media",
            aggregate_id=media.id,
            event_type="media.deleted",
            payload={
                "media_id": media.id,
                "objects": {
                    settings.storage_private_bucket: [media.upload_key],
                    settings.storage_public_bucket: public_keys,
                },
            },
            tenant_id=self.tenant.id,
        )
        await self.session.delete(media)
        await self.session.flush()

    # ------------------------------------------------------------------ helpers
    async def _check_owner(self, owner_type: str, owner_id: str | None) -> None:
        if owner_type == MediaOwner.PRODUCT:
            if not self.tenant.feature("catalog"):
                raise FeatureDisabledError(features=["catalog"])
            if owner_id is None:
                raise ValidationError("Imagem de produto precisa de owner_id.")
            product = await CatalogRepository(self.session).get_product(owner_id)
            if product is None or product.status == ProductStatus.ARCHIVED:
                raise NotFoundError("Produto não encontrado.")
        elif owner_id is not None:
            raise ValidationError("Imagens da loja não têm owner_id.")

    async def _get_or_404(self, media_id: str) -> MediaAsset:
        media = await self.repo.get(media_id)
        if media is None:
            raise NotFoundError("Imagem não encontrada.")
        return media

    async def _audit(self, action: str, media: MediaAsset, after: dict[str, Any]) -> None:
        await audit(
            self.session,
            actor=self.actor.id,
            action=action,
            entity_type="media",
            entity_id=media.id,
            tenant_id=self.tenant.id,
            after={"owner_type": media.owner_type, "owner_id": media.owner_id, **after},
            ip=self.actor.ip,
            user_agent=self.actor.user_agent,
        )

    async def _emit(self, media: MediaAsset, event_type: str) -> None:
        await _emit_media(self.session, media, event_type)


async def _emit_media(session: AsyncSession, media: MediaAsset, event_type: str) -> None:
    await emit(
        session,
        aggregate_type="media",
        aggregate_id=media.id,
        event_type=event_type,
        payload={
            "media_id": media.id,
            "owner_type": media.owner_type,
            "owner_id": media.owner_id,
            "status": media.status,
        },
        tenant_id=media.tenant_id,
    )


async def ready_media_count(session: AsyncSession, product_id: str) -> int:
    ready = await MediaRepository(session).ready_for_owners(MediaOwner.PRODUCT, [product_id])
    return len(ready.get(product_id, []))


# ============================================================================ worker side
class TxRunner(Protocol):
    """Runs `fn` in its own session and commits (the worker's `with_session`)."""

    def __call__[T](self, fn: Callable[[AsyncSession], Awaitable[T]]) -> Awaitable[T]: ...


@dataclass(frozen=True, slots=True)
class _Claim:
    upload_key: str


async def process_media(
    tx: TxRunner, storage: ObjectStorage, *, tenant_id: str, media_id: str
) -> str:
    """Render one upload into public WebP renditions. Returns the final status."""

    async def claim(session: AsyncSession) -> _Claim | str:
        bind_session_tenant(session, tenant_id)
        media = await MediaRepository(session).get(media_id)
        if media is None:
            return "missing"
        if media.status != MediaStatus.PROCESSING:
            return str(media.status)
        media.attempts += 1
        media.updated_at = utcnow()  # keeps the stale-requeue job away while we run
        return _Claim(media.upload_key)

    claimed = await tx(claim)
    if isinstance(claimed, str):
        return claimed

    try:
        data = await storage.read(
            settings.storage_private_bucket, claimed.upload_key, max_bytes=MAX_UPLOAD_BYTES
        )
        # CPU-bound: off the event loop (the worker loop also drives the DB driver).
        processed = await asyncio.to_thread(process_image, data)
    except ObjectMissingError:
        return await _finish_failed(tx, tenant_id, media_id, "Arquivo enviado não encontrado.")
    except ObjectTooLargeError:
        return await _finish_failed(tx, tenant_id, media_id, "Arquivo maior que 10 MB.")
    except InvalidImageError as exc:
        return await _finish_failed(tx, tenant_id, media_id, str(exc))

    prefix = f"tenants/{tenant_id}/media/{media_id}/{processed.checksum_sha256[:16]}"
    renditions: dict[str, Any] = {}
    for rendition in processed.renditions:
        key = f"{prefix}/{rendition.name}.webp"
        await storage.put(
            settings.storage_public_bucket,
            key,
            rendition.data,
            content_type="image/webp",
            cache_control=IMMUTABLE_CACHE,
        )
        renditions[rendition.name] = {
            "key": key,
            "width": rendition.width,
            "height": rendition.height,
            "bytes": len(rendition.data),
        }

    async def finish(session: AsyncSession) -> str:
        bind_session_tenant(session, tenant_id)
        media = await MediaRepository(session).get(media_id)
        if media is None:
            return "missing"  # deleted meanwhile; media.deleted cleans the objects up
        if media.status != MediaStatus.PROCESSING:
            return str(media.status)
        media.status = MediaStatus.READY
        media.public_prefix = prefix
        media.renditions = renditions
        media.width = processed.width
        media.height = processed.height
        media.checksum_sha256 = processed.checksum_sha256
        media.processed_at = utcnow()
        media.failure_reason = None
        media.updated_by_actor = "system:media"
        await session.flush()
        await _emit_media(session, media, "media.ready")
        return str(MediaStatus.READY)

    status = await tx(finish)
    logger.info("Media processed", extra={"media_id": media_id, "status": status})
    return status


async def _finish_failed(tx: TxRunner, tenant_id: str, media_id: str, reason: str) -> str:
    async def fail(session: AsyncSession) -> str:
        bind_session_tenant(session, tenant_id)
        media = await MediaRepository(session).get(media_id)
        if media is None:
            return "missing"
        if media.status != MediaStatus.PROCESSING:
            return str(media.status)
        media.status = MediaStatus.FAILED
        media.failure_reason = reason[:300]
        media.updated_by_actor = "system:media"
        await session.flush()
        await _emit_media(session, media, "media.failed")
        return str(MediaStatus.FAILED)

    status = await tx(fail)
    logger.info("Media rejected", extra={"media_id": media_id, "reason": reason})
    return status


async def sweep_stale_media(session: AsyncSession) -> list[tuple[str, str]]:
    """Maintenance (cross-tenant). Returns (tenant_id, media_id) to queue again; the caller
    commits before queueing.

    - `processing` untouched for a while: its task was lost (worker killed, broker restart)
      → queued again, or failed after MAX_PROCESS_ATTEMPTS;
    - `pending` long abandoned: the row goes (the object, if any, expires by bucket lifecycle).
    """
    now = utcnow()
    repo = MediaRepository(session)
    requeue: list[tuple[str, str]] = []
    for media in await repo.stale(MediaStatus.PROCESSING, now - PROCESSING_STALE_AFTER):
        bind_session_tenant(session, media.tenant_id)
        if media.attempts >= MAX_PROCESS_ATTEMPTS:
            media.status = MediaStatus.FAILED
            media.failure_reason = "Processamento não concluiu após várias tentativas."
            await session.flush()
            await _emit_media(session, media, "media.failed")
        else:
            media.updated_at = now
            requeue.append((media.tenant_id, media.id))
    for media in await repo.stale(MediaStatus.PENDING, now - PENDING_ABANDONED_AFTER):
        bind_session_tenant(session, media.tenant_id)
        await session.delete(media)
    await session.flush()
    return requeue
