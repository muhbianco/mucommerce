"""Outbox consumers registered at import time.

Phase 0 ships only the audit projector; ChatwootSync, Notifier and
InventoryCommitter arrive with their modules and register here.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import OutboxEvent
from app.audit.outbox import registry
from app.core.logging import get_logger

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


registry.register("audit_projector", ("*",), audit_projector)
