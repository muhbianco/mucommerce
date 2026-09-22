"""Panel: the store's orders (ADR 0011 §6).

Reading needs `orders:read`; moving an order along needs `orders:transition`; cancelling needs
`orders:cancel` and goes through the one entry point that also gives the money back. The detail
answers with `allowed_transitions` for the person asking, so the panel shows exactly the buttons
that will work, and a stale version is refused instead of moving the wrong order.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import CurrentAdmin, DbSession, admin_actor, require_tenant_scopes
from app.audit.idempotency import idempotent
from app.core.exceptions import NotFoundError, PermissionDeniedError, StaleOrderError
from app.core.pagination import Page, decode_cursor, encode_cursor
from app.core.scopes import Scope, scopes_for_tenant_role
from app.identity.repository import AdminUserRepository
from app.notifications.models import NotificationDelivery
from app.orders.models import Order
from app.orders.schemas import OrderRead, order_read
from app.orders.service import OrderService
from app.orders.state_machine import ActorKind, OrderStatus
from app.payments.cancellation import cancel_order, dispatch_refunds
from app.payments.models import Payment, Refund
from app.payments.schemas import PaymentRead, payment_read
from app.schemas.common import StrictModel
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/admin/tenants/{tenant_id}/orders", tags=["Painel — Pedidos"])

OrdersReader = Annotated[
    TenantContext, Depends(require_tenant_scopes(Scope.ORDERS_READ, features=("checkout",)))
]
OrdersMover = Annotated[
    TenantContext, Depends(require_tenant_scopes(Scope.ORDERS_TRANSITION, features=("checkout",)))
]
OrderId = Annotated[str, Path(min_length=36, max_length=36)]


class TransitionIn(StrictModel):
    to: OrderStatus
    reason: Annotated[str, Field(max_length=200)] | None = None
    # What the panel had on screen: a different version means someone else moved it first.
    expected_version: Annotated[int, Field(ge=1)] | None = None
    # Cancelling a paid order: whether the items go back to the shelf.
    restock: bool = True


class OrderSummaryRead(BaseModel):
    id: str
    number: int
    status: str
    fulfillment_type: str
    fulfillment_status: str
    total_cents: int
    currency: str
    customer_name: str | None
    placed_at: datetime
    paid_at: datetime | None
    refund_status: str


class RefundSummaryRead(BaseModel):
    id: str
    amount_cents: int
    kind: str
    method: str
    status: str
    reason: str
    requested_at: datetime


class EmailRead(BaseModel):
    id: str
    template_key: str
    recipient: str
    subject: str
    status: str
    attempts: int
    sent_at: datetime | None
    last_error: str | None


class OrderDetailRead(BaseModel):
    order: OrderRead
    customer: dict[str, Any]
    payments: list[PaymentRead]
    refunds: list[RefundSummaryRead]
    emails: list[EmailRead]
    allowed_transitions: list[str]
    version: int
    risk_flags: dict[str, Any] | None
    notes: str | None


def summary(order: Order) -> OrderSummaryRead:
    return OrderSummaryRead(
        id=order.id,
        number=order.number,
        status=order.status,
        fulfillment_type=order.fulfillment_type,
        fulfillment_status=order.fulfillment_status,
        total_cents=order.total_cents,
        currency=order.currency,
        customer_name=(order.customer_snapshot or {}).get("name"),
        placed_at=order.placed_at,
        paid_at=order.paid_at,
        refund_status=order.refund_status,
    )


def _admin(kwargs: dict[str, Any]) -> str:
    return str(kwargs["user"].id)


async def _scopes(session: DbSession, user: CurrentAdmin, tenant: TenantContext) -> frozenset[str]:
    """What this person may do in this store. Platform staff read; they never move orders."""
    membership = await AdminUserRepository(session).membership(user.id, tenant.id)
    if membership is None:
        return frozenset()
    return frozenset(str(scope) for scope in scopes_for_tenant_role(membership.role))


async def _order(session: DbSession, order_id: str, *, lock: bool = False) -> Order:
    stmt = select(Order).where(Order.id == order_id)
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    order = await session.scalar(stmt)
    if order is None:
        raise NotFoundError("Pedido não encontrado.")
    return order


@router.get("", response_model=Page[OrderSummaryRead], summary="Pedidos da loja")
async def list_orders(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: OrdersReader,
    order_status: Annotated[OrderStatus | None, Query(alias="status")] = None,
    q: Annotated[str | None, Query(max_length=60)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: Annotated[str | None, Query(max_length=256)] = None,
) -> Page[OrderSummaryRead]:
    before = decode_cursor(cursor, "id")["id"] if cursor else None
    service = OrderService(session, tenant, admin_actor(request, user))
    rows = await service.list_for_store(
        limit=limit, status=order_status, search=q, before_id=before
    )
    page = rows[:limit]
    return Page[OrderSummaryRead](
        items=[summary(o) for o in page],
        next_cursor=encode_cursor(id=page[-1].id) if len(rows) > limit else None,
    )


@router.get("/{order_id}", response_model=OrderDetailRead, summary="Um pedido com tudo o que houve")
async def get_order(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: OrdersReader,
    order_id: OrderId,
) -> OrderDetailRead:
    return await _detail(request, session, user, tenant, order_id)


async def _detail(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: TenantContext,
    order_id: str,
) -> OrderDetailRead:
    """Everything that happened to one order: lines, timeline, payments, refunds, e-mails."""
    order = await _order(session, order_id)
    service = OrderService(session, tenant, admin_actor(request, user))
    payments = list(
        (
            await session.execute(
                select(Payment)
                .where(Payment.order_id == order_id)
                .order_by(Payment.created_at.desc())
                .limit(20)
            )
        ).scalars()
    )
    refunds = list(
        (
            await session.execute(
                select(Refund)
                .where(Refund.order_id == order_id)
                .order_by(Refund.requested_at)
                .limit(50)
            )
        ).scalars()
    )
    emails = list(
        (
            await session.execute(
                select(NotificationDelivery)
                .where(NotificationDelivery.order_id == order_id)
                .order_by(NotificationDelivery.created_at)
                .limit(50)
            )
        ).scalars()
    )
    return OrderDetailRead(
        order=order_read(order, await service.items(order_id), await service.history(order_id)),
        customer=dict(order.customer_snapshot or {}),
        payments=[payment_read(p) for p in payments],
        refunds=[
            RefundSummaryRead(
                id=r.id,
                amount_cents=r.amount_cents,
                kind=r.kind,
                method=r.method,
                status=r.status,
                reason=r.reason,
                requested_at=r.requested_at,
            )
            for r in refunds
        ],
        emails=[
            EmailRead(
                id=e.id,
                template_key=e.template_key,
                recipient=e.recipient,
                subject=e.subject,
                status=e.status,
                attempts=e.attempts,
                sent_at=e.sent_at,
                last_error=e.last_error,
            )
            for e in emails
        ],
        allowed_transitions=service.allowed_transitions(
            order, await _scopes(session, user, tenant)
        ),
        version=order.version,
        risk_flags=order.risk_flags,
        notes=order.notes_customer,
    )


@router.post(
    "/{order_id}/transition",
    response_model=OrderDetailRead,
    summary="Move o pedido (aceitar, preparar, pronto, enviado, entregue) ou cancela",
)
@idempotent("orders.transition", required=False, principal=_admin)
async def move_order(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: OrdersMover,
    order_id: OrderId,
    body: TransitionIn,
) -> OrderDetailRead:
    actor = admin_actor(request, user)
    scopes = await _scopes(session, user, tenant)
    if not scopes:  # platform staff read orders; the store runs them
        raise PermissionDeniedError("Só a equipe da loja movimenta pedidos.")
    order = await _order(session, order_id, lock=True)
    refunds = []
    if body.to == OrderStatus.CANCELLED:
        if Scope.ORDERS_CANCEL not in scopes:
            raise PermissionDeniedError(
                "Seu papel não cancela pedidos.", missing=[str(Scope.ORDERS_CANCEL)]
            )
        if body.expected_version is not None and body.expected_version != order.version:
            raise StaleOrderError(version=order.version, expected=body.expected_version)
        refunds = await cancel_order(
            session,
            tenant,
            actor,
            order,
            ActorKind.OPERATOR,
            reason=body.reason or "cancelado pela loja",
            scopes=scopes,
            restock=body.restock,
        )
    else:
        await OrderService(session, tenant, actor).transition(
            order,
            body.to,
            reason=body.reason,
            scopes=scopes,
            expected_version=body.expected_version,
        )
    await session.commit()
    await dispatch_refunds(session, tenant, refunds)
    # Read it back after the refund was sent: the screen shows where things really stand.
    return await _detail(request, session, user, tenant, order_id)
