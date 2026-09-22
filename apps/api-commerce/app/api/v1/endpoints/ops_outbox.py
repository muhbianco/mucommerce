from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.deps import DbSession, PlatformOperator
from app.audit.models import OutboxEvent
from app.audit.outbox import retry_failed
from app.core.exceptions import NotFoundError
from app.models.base import utcnow
from app.payments.health import payment_anomalies
from app.schemas.tenant import OutboxEventRead
from app.tenancy.context import CROSS_TENANT_OPTION

router = APIRouter(prefix="/ops/outbox", tags=["Ops — Outbox / DLQ"])
payments_router = APIRouter(prefix="/ops/payments", tags=["Ops — Outbox / DLQ"])


@router.get("", response_model=list[OutboxEventRead], summary="Eventos do outbox por status")
async def list_events(
    session: DbSession,
    _: PlatformOperator,
    status: str = Query(default="failed", pattern=r"^(pending|dispatched|done|failed)$"),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[OutboxEventRead]:
    stmt = (
        select(OutboxEvent)
        .where(OutboxEvent.status == status)
        .order_by(OutboxEvent.occurred_at.desc())
        .limit(limit)
        .execution_options(**{CROSS_TENANT_OPTION: True})
    )
    events = (await session.execute(stmt)).scalars().all()
    return [OutboxEventRead.model_validate(e, from_attributes=True) for e in events]


@router.post(
    "/{event_id}/retry", response_model=OutboxEventRead, summary="Reprocessa evento da DLQ"
)
async def retry_event(session: DbSession, _: PlatformOperator, event_id: str) -> OutboxEventRead:
    event = await session.get(OutboxEvent, event_id)
    if event is None:
        raise NotFoundError("Evento não encontrado.")
    await retry_failed(session, event_id)
    await session.refresh(event)
    return OutboxEventRead.model_validate(event, from_attributes=True)


@payments_router.get(
    "/health",
    summary="O que está travado em pagamentos, em todas as lojas (mesma conta dos alertas)",
)
async def payments_health(session: DbSession, _: PlatformOperator) -> dict[str, object]:
    now = utcnow()
    return {"checked_at": now, "anomalies": await payment_anomalies(session, now)}
