"""Panel: refunds (ADR 0011 §12). The store's own members only — whose money goes back to whom
is the store's decision, so platform staff get no bypass (they read status in /ops).

- request: `payments:refund_request`; above the store's limit it waits for a second person;
- approve / reject / complete (external refund done in the provider's app, with evidence):
  `payments:refund_approve`; the person who asked cannot approve (four eyes).
Approved provider refunds are sent right after the commit (and by the sweep if that fails).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import CurrentAdmin, DbSession, admin_actor, require_tenant_scopes
from app.audit.idempotency import idempotent
from app.core.exceptions import NotFoundError
from app.core.pagination import Page, decode_cursor, encode_cursor
from app.core.scopes import Scope
from app.orders.models import Order
from app.payments.cancellation import dispatch_refunds
from app.payments.models import Refund, RefundKind, RefundStatus
from app.payments.refunds import RefundService
from app.schemas.common import StrictModel
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/admin/tenants/{tenant_id}", tags=["Painel — Pagamentos"])

RefundsReader = Annotated[
    TenantContext, Depends(require_tenant_scopes(Scope.PAYMENTS_READ, features=("checkout",)))
]
RefundRequester = Annotated[
    TenantContext,
    Depends(
        require_tenant_scopes(
            Scope.PAYMENTS_REFUND_REQUEST, features=("checkout",), members_only=True
        )
    ),
]
RefundApprover = Annotated[
    TenantContext,
    Depends(
        require_tenant_scopes(
            Scope.PAYMENTS_REFUND_APPROVE, features=("checkout",), members_only=True
        )
    ),
]
EntityId = Annotated[str, Path(min_length=36, max_length=36)]


class RefundIn(StrictModel):
    # Omitted: everything that can still be refunded on the order's payment.
    amount_cents: Annotated[int, Field(gt=0, le=100_000_000)] | None = None
    reason: Annotated[str, Field(min_length=3, max_length=200)]


class RejectIn(StrictModel):
    reason: Annotated[str, Field(min_length=3, max_length=200)]


class EvidenceIn(StrictModel):
    evidence: Annotated[str, Field(min_length=10, max_length=500)]


class RefundRead(BaseModel):
    id: str
    order_id: str
    payment_id: str
    amount_cents: int
    reason: str
    kind: str
    method: str
    status: str
    requested_by: str
    approved_by: str | None
    rejected_by: str | None
    rejection_reason: str | None
    external_evidence: str | None
    failure_message: str | None
    attempts: int
    requested_at: datetime
    approved_at: datetime | None
    completed_at: datetime | None


def refund_read(refund: Refund) -> RefundRead:
    return RefundRead(
        id=refund.id,
        order_id=refund.order_id,
        payment_id=refund.payment_id,
        amount_cents=refund.amount_cents,
        reason=refund.reason,
        kind=refund.kind,
        method=refund.method,
        status=refund.status,
        requested_by=refund.requested_by_actor,
        approved_by=refund.approved_by_actor,
        rejected_by=refund.rejected_by_actor,
        rejection_reason=refund.rejection_reason,
        external_evidence=refund.external_evidence,
        failure_message=refund.failure_message,
        attempts=refund.attempts,
        requested_at=refund.requested_at,
        approved_at=refund.approved_at,
        completed_at=refund.completed_at,
    )


async def _sent(session: DbSession, tenant: TenantContext, refund: Refund) -> RefundRead:
    """Commit, send it if approved (inline or a task), and answer with where it stands now."""
    await session.commit()
    await dispatch_refunds(session, tenant, [refund])
    fresh = await session.get(Refund, refund.id, populate_existing=True)
    return refund_read(fresh or refund)


def _admin(kwargs: dict[str, Any]) -> str:
    return str(kwargs["user"].id)


@router.post(
    "/orders/{order_id}/refunds",
    response_model=RefundRead,
    status_code=status.HTTP_201_CREATED,
    summary="Devolve dinheiro de um pedido pago (acima do limite da loja, outra pessoa aprova)",
)
@idempotent("refunds.request", status_code=201, principal=_admin)
async def request_refund(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: RefundRequester,
    order_id: EntityId,
    body: RefundIn,
) -> RefundRead:
    order = await session.scalar(
        select(Order)
        .where(Order.id == order_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if order is None:
        raise NotFoundError("Pedido não encontrado.")
    refund = await RefundService(session, tenant, admin_actor(request, user)).request(
        order, kind=RefundKind.OPERATOR, reason=body.reason, amount_cents=body.amount_cents
    )
    return await _sent(session, tenant, refund)


@router.get("/refunds", response_model=Page[RefundRead], summary="Devoluções da loja")
async def list_refunds(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: RefundsReader,
    refund_status: Annotated[RefundStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: Annotated[str | None, Query(max_length=256)] = None,
) -> Page[RefundRead]:
    before = decode_cursor(cursor, "id")["id"] if cursor else None
    service = RefundService(session, tenant, admin_actor(request, user))
    rows = await service.recent(status=refund_status, limit=limit, before_id=before)
    page = rows[:limit]
    return Page[RefundRead](
        items=[refund_read(r) for r in page],
        next_cursor=encode_cursor(id=page[-1].id) if len(rows) > limit else None,
    )


@router.post(
    "/refunds/{refund_id}/approve",
    response_model=RefundRead,
    summary="Aprova uma devolução pedida por outra pessoa",
)
async def approve_refund(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: RefundApprover,
    refund_id: EntityId,
) -> RefundRead:
    refund = await RefundService(session, tenant, admin_actor(request, user)).approve(refund_id)
    return await _sent(session, tenant, refund)


@router.post(
    "/refunds/{refund_id}/reject", response_model=RefundRead, summary="Recusa uma devolução"
)
async def reject_refund(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: RefundApprover,
    refund_id: EntityId,
    body: RejectIn,
) -> RefundRead:
    service = RefundService(session, tenant, admin_actor(request, user))
    return refund_read(await service.reject(refund_id, body.reason))


@router.post(
    "/refunds/{refund_id}/complete",
    response_model=RefundRead,
    summary="Registra a devolução feita no app do meio de pagamento (com a evidência)",
)
async def complete_refund(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: RefundApprover,
    refund_id: EntityId,
    body: EvidenceIn,
) -> RefundRead:
    service = RefundService(session, tenant, admin_actor(request, user))
    return refund_read(await service.complete_external(refund_id, body.evidence))
