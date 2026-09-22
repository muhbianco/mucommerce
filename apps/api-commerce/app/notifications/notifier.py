"""Domain event → e-mails waiting to be sent (ADR 0011 §11).

An outbox consumer: it reads the order as it stands now (never the event's snapshot, which may
be stale by a retry), renders each e-mail and writes one delivery row per recipient. The unique
key `(tenant, event, template, recipient)` is what makes an e-mail happen once, so the outbox
may deliver the same event twice without writing to the customer twice.

Addresses come from the database — the customer's own account, the store's members — never from
a request. A store without e-mail configured (no n8n URL) still gets its rows, marked `skipped`,
so the panel can show what would have been sent.
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import OutboxEvent
from app.catalog.models import Event
from app.core.config import settings
from app.core.logging import get_logger
from app.core.scopes import TenantRole
from app.identity.models import AdminUser, AdminUserStatus, Customer, TenantMembership
from app.models.base import utcnow
from app.notifications.messages import (
    TOLD_ABOUT,
    MailContext,
    order_cancelled,
    order_failed,
    order_paid,
    order_placed,
    order_status_changed,
    payment_pix_pending,
    refund_completed,
    store_order_paid,
)
from app.notifications.models import DeliveryStatus, NotificationDelivery
from app.notifications.render import Message, render
from app.orders.models import Order, OrderItem
from app.payments.models import Payment, Refund
from app.tenancy.context import TenantContext, bind_session_tenant
from app.tenancy.models import DomainPurpose, DomainRole, TenantDomain
from app.tenancy.resolver import TenantResolver

logger = get_logger(__name__)

EVENT_TYPES = (
    "order.placed",
    "order.paid",
    "order.failed",
    "order.cancelled",
    "order.status_changed",
    "payment.requires_action",
    "refund.completed",
)
Builder = Callable[[MailContext], Message]
# Who hears about what: the customer always, the store's owners when money lands.
CUSTOMER_BUILDERS: dict[str, Builder] = {
    "order.placed": order_placed,
    "order.paid": order_paid,
    "order.failed": order_failed,
    "order.cancelled": order_cancelled,
    "order.status_changed": order_status_changed,
    "payment.requires_action": payment_pix_pending,
    "refund.completed": refund_completed,
}
STORE_BUILDERS: dict[str, Builder] = {"order.paid": store_order_paid}
MAX_STORE_RECIPIENTS = 5


async def notifier(session: AsyncSession, event: OutboxEvent) -> None:
    if event.event_type not in EVENT_TYPES or not event.tenant_id:
        return
    tenant = await TenantResolver(session).resolve_by_id(event.tenant_id)
    bind_session_tenant(session, event.tenant_id)
    order_id = str(event.payload.get("order_id") or "")
    if not order_id:
        return
    order = await session.get(Order, order_id)
    if order is None:
        return
    if event.event_type == "order.status_changed" and order.status not in TOLD_ABOUT:
        return  # internal steps are not the customer's business
    if event.event_type == "payment.requires_action" and not await _is_pix(session, event):
        return
    context = await _context(session, tenant, order, event)
    queued = DeliveryStatus.QUEUED if settings.notify_n8n_url else DeliveryStatus.SKIPPED
    builder = CUSTOMER_BUILDERS.get(event.event_type)
    if builder is not None:
        address = await _customer_email(session, order)
        if address:
            await _queue(session, event, tenant, builder(context), address, order_id, queued)
    store_builder = STORE_BUILDERS.get(event.event_type)
    if store_builder is not None:
        message = store_builder(context)
        for address in await _store_emails(session, tenant):
            await _queue(session, event, tenant, message, address, order_id, queued)


async def _queue(
    session: AsyncSession,
    event: OutboxEvent,
    tenant: TenantContext,
    message: Message,
    recipient: str,
    order_id: str,
    status: str,
) -> None:
    rendered = render(
        message,
        store_name=tenant.name,
        brand_color=str((tenant.settings.get("branding") or {}).get("primary_color") or "") or None,
    )
    delivery = NotificationDelivery(
        event_id=event.id,
        template_key=message.template_key,
        channel="email",
        recipient=recipient[:320],
        order_id=order_id,
        subject=rendered.subject,
        body_html=rendered.html,
        body_text=rendered.text,
        status=status,
        next_attempt_at=utcnow() if status == DeliveryStatus.QUEUED else None,
    )
    try:
        async with session.begin_nested():
            session.add(delivery)
    except IntegrityError:
        # This event was delivered to the consumer before: the e-mail already exists.
        logger.info(
            "Notification already queued",
            extra={"event_id": event.id, "template": message.template_key},
        )


async def _context(
    session: AsyncSession, tenant: TenantContext, order: Order, event: OutboxEvent
) -> MailContext:
    from app.orders.service import OrderService
    from app.tenancy.service import Actor

    items = await OrderService(session, tenant, Actor.system("notifier")).items(order.id)
    payment = None
    if payment_id := str(event.payload.get("payment_id") or ""):
        payment = await session.get(Payment, payment_id)
    refund = None
    if refund_id := str(event.payload.get("refund_id") or ""):
        refund = await session.get(Refund, refund_id)
    host = await _host(session, tenant)
    url = f"{settings.storefront_origin(host)}/conta/pedidos/{order.id}" if host else None
    return MailContext(
        store_name=tenant.name,
        timezone=tenant.timezone,
        order=order,
        items=items,
        url=url,
        panel_url=f"https://{settings.panel_host}/t/{tenant.id}/pedidos/{order.id}",
        payment=payment,
        refund=refund,
        online_url=await _online_url(session, items) if order.paid_at else None,
    )


async def _is_pix(session: AsyncSession, event: OutboxEvent) -> bool:
    payment_id = str(event.payload.get("payment_id") or "")
    payment = await session.get(Payment, payment_id) if payment_id else None
    return payment is not None and bool(payment.pix_copy_paste)


async def _customer_email(session: AsyncSession, order: Order) -> str | None:
    customer = await session.get(Customer, order.customer_id)
    return customer.email_normalized if customer else None


async def _store_emails(session: AsyncSession, tenant: TenantContext) -> list[str]:
    stmt = (
        select(AdminUser.email)
        .join(TenantMembership, TenantMembership.admin_user_id == AdminUser.id)
        .where(TenantMembership.tenant_id == tenant.id)
        .where(TenantMembership.role == TenantRole.OWNER)
        .where(AdminUser.status == AdminUserStatus.ACTIVE)
        .order_by(AdminUser.email)
        .limit(MAX_STORE_RECIPIENTS)
    )
    return [str(email) for email in (await session.execute(stmt)).scalars()]


async def _host(session: AsyncSession, tenant: TenantContext) -> str | None:
    stmt = (
        select(TenantDomain.hostname)
        .where(TenantDomain.tenant_id == tenant.id)
        .where(TenantDomain.purpose == DomainPurpose.STOREFRONT)
        .order_by(TenantDomain.role != DomainRole.PRIMARY, TenantDomain.hostname)
        .limit(1)
    )
    host: str | None = await session.scalar(stmt)
    return host


async def _online_url(session: AsyncSession, items: list[OrderItem]) -> str | None:
    """A paid ticket's online address, when the event has one."""
    event_ids = [
        str(item.event["event_id"]) for item in items if item.event and item.event.get("event_id")
    ]
    if not event_ids:
        return None
    url: str | None = await session.scalar(
        select(Event.online_url).where(Event.id.in_(event_ids)).where(Event.online_url.isnot(None))
    )
    return url
