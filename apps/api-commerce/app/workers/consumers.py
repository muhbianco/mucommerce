"""Outbox consumers registered at import time.

Phase 0 ships only the audit projector; ChatwootSync, Notifier and
InventoryCommitter arrive with their modules and register here.
"""

from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import OutboxEvent
from app.audit.outbox import registry
from app.core.logging import get_logger
from app.core.storage import get_storage
from app.workers.media import process_media_task

logger = get_logger(__name__)


async def audit_projector(session: AsyncSession, event: OutboxEvent) -> None:
    """Structured log of every domain event; the cheapest possible consumer."""
    del session
    logger.info(
        "Domain event",
        extra={
            "event_id": event.id,
            "event_type": event.event_type,
            "aggregate_type": event.aggregate_type,
            "aggregate_id": event.aggregate_id,
            "sequence": event.sequence,
            "tenant_id": event.tenant_id,
        },
    )


async def media_processor(session: AsyncSession, event: OutboxEvent) -> None:
    """Hand a confirmed upload to the media queue (the image work never runs on the outbox
    queue). A lost task message is caught by `sweep_media`."""
    del session
    # `.delay` is a blocking broker round trip: keep it off the event loop.
    await asyncio.to_thread(
        process_media_task.delay, event.tenant_id, str(event.payload["media_id"])
    )


async def media_janitor(session: AsyncSession, event: OutboxEvent) -> None:
    """Delete the objects of a deleted media row. Idempotent: deleting a missing key is a no-op,
    so an outbox retry is safe."""
    del session
    storage = get_storage()
    objects: dict[str, list[str]] = event.payload.get("objects", {})
    for bucket, keys in objects.items():
        await storage.delete(bucket, [str(key) for key in keys])


registry.register("audit_projector", ("*",), audit_projector)
registry.register("media_processor", ("media.uploaded",), media_processor)
registry.register("media_janitor", ("media.deleted",), media_janitor)
