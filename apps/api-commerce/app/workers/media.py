"""Media tasks. They run on the `commerce.media` queue, served by its own single-process worker
(image decoding is memory-hungry; it must not starve the outbox or payments)."""

from __future__ import annotations

from app.core.logging import get_logger
from app.core.storage import get_storage
from app.media.service import process_media, sweep_stale_media
from app.workers.celery_app import celery_app
from app.workers.runtime import run_async, with_session

logger = get_logger(__name__)


@celery_app.task(name="app.workers.media.process_media")
def process_media_task(tenant_id: str, media_id: str) -> str:
    """No Celery retries: a failed run leaves the row `processing` and `sweep_media` queues it
    again after PROCESSING_STALE_AFTER, up to MAX_PROCESS_ATTEMPTS."""
    return run_async(
        process_media(with_session, get_storage(), tenant_id=tenant_id, media_id=media_id)
    )


@celery_app.task(name="app.workers.media.sweep_media")
def sweep_media() -> int:
    requeue = run_async(with_session(sweep_stale_media))  # committed on return
    for tenant_id, media_id in requeue:
        process_media_task.delay(tenant_id, media_id)
    if requeue:
        logger.warning("Stale media re-queued", extra={"count": len(requeue)})
    return len(requeue)
