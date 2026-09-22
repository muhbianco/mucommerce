"""Order state machine (pure; ADR 0011 §6).

awaiting_payment → payment_confirmed → accepted → in_production → ready_for_pickup | shipped →
delivered, plus cancelled and failed. `failed → payment_confirmed` exists only for a payment
that arrives after the deadline and still finds stock. Refunds are not states (`refund_status`).

Every transition says who may make it (actor kind and, for staff, the scope) and its guard;
`check_transition` is the only gate the service uses, so the table below is the whole policy.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from app.core.exceptions import InvalidTransitionError
from app.core.scopes import Scope


class OrderStatus(StrEnum):
    AWAITING_PAYMENT = "awaiting_payment"
    PAYMENT_CONFIRMED = "payment_confirmed"
    ACCEPTED = "accepted"
    IN_PRODUCTION = "in_production"
    READY_FOR_PICKUP = "ready_for_pickup"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    FAILED = "failed"


# Orders the customer still has to pay (the open-order cap counts these).
OPEN_STATUSES = frozenset({OrderStatus.AWAITING_PAYMENT})
PAID_STATUSES = frozenset(
    {
        OrderStatus.PAYMENT_CONFIRMED,
        OrderStatus.ACCEPTED,
        OrderStatus.IN_PRODUCTION,
        OrderStatus.READY_FOR_PICKUP,
        OrderStatus.SHIPPED,
        OrderStatus.DELIVERED,
    }
)


class FulfillmentStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    PICKED_UP = "picked_up"
    OUT_FOR_DELIVERY = "out_for_delivery"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    NONE = "none"  # tickets, services, digital goods


class RefundStatus(StrEnum):
    NONE = "none"
    PARTIAL = "partial"
    FULL = "full"


class ActorKind(StrEnum):
    CUSTOMER = "customer"
    OPERATOR = "operator"  # store staff in the panel (scopes decide)
    SYSTEM = "system"  # jobs, payment approval, auto-accept
    AGENT = "agent"  # the store owner's agent (stage H)


@dataclass(frozen=True, slots=True)
class OrderSnapshot:
    status: str
    fulfillment_type: str  # pickup | delivery | none
    has_approved_payment: bool = False
    customer_cancel_until: str = OrderStatus.ACCEPTED


Guard = Callable[[OrderSnapshot], str | None]  # a problem code, or None when allowed


def _no_approved_payment(order: OrderSnapshot) -> str | None:
    return "payment_approved" if order.has_approved_payment else None


_CANCEL_ORDER = [OrderStatus.PAYMENT_CONFIRMED, OrderStatus.ACCEPTED]


def _customer_window(order: OrderSnapshot) -> str | None:
    limit = order.customer_cancel_until
    if order.status not in _CANCEL_ORDER or limit not in _CANCEL_ORDER:
        return "cancel_window_closed"
    if _CANCEL_ORDER.index(OrderStatus(order.status)) > _CANCEL_ORDER.index(OrderStatus(limit)):
        return "cancel_window_closed"
    return None


def _fulfillment(kind: str) -> Guard:
    def guard(order: OrderSnapshot) -> str | None:
        return None if order.fulfillment_type == kind else "wrong_fulfillment"

    return guard


@dataclass(frozen=True, slots=True)
class Transition:
    sources: frozenset[str]
    target: str
    actors: frozenset[ActorKind]
    scope: Scope | None = None  # required of operators
    guard: Guard | None = None


def _t(
    sources: set[OrderStatus] | OrderStatus,
    target: OrderStatus,
    actors: set[ActorKind],
    scope: Scope | None = None,
    guard: Guard | None = None,
) -> Transition:
    src = sources if isinstance(sources, set) else {sources}
    return Transition(frozenset(src), target, frozenset(actors), scope, guard)


S = OrderStatus
A = ActorKind
_AFTER_PAYMENT = {S.PAYMENT_CONFIRMED, S.ACCEPTED, S.IN_PRODUCTION, S.READY_FOR_PICKUP, S.SHIPPED}

TRANSITIONS: tuple[Transition, ...] = (
    _t(S.AWAITING_PAYMENT, S.PAYMENT_CONFIRMED, {A.SYSTEM}),
    _t(S.FAILED, S.PAYMENT_CONFIRMED, {A.SYSTEM}),  # late payment that still finds stock
    _t(S.AWAITING_PAYMENT, S.FAILED, {A.SYSTEM}),  # deadline passed
    _t(S.AWAITING_PAYMENT, S.CANCELLED, {A.CUSTOMER, A.AGENT}, guard=_no_approved_payment),
    _t(
        S.AWAITING_PAYMENT,
        S.CANCELLED,
        {A.OPERATOR},
        scope=Scope.ORDERS_CANCEL,
        guard=_no_approved_payment,
    ),
    _t({S.PAYMENT_CONFIRMED, S.ACCEPTED}, S.CANCELLED, {A.CUSTOMER}, guard=_customer_window),
    _t(_AFTER_PAYMENT, S.CANCELLED, {A.OPERATOR}, scope=Scope.ORDERS_CANCEL),
    _t(S.PAYMENT_CONFIRMED, S.ACCEPTED, {A.SYSTEM}),  # auto-accept
    _t(S.PAYMENT_CONFIRMED, S.ACCEPTED, {A.OPERATOR}, scope=Scope.ORDERS_TRANSITION),
    _t(S.ACCEPTED, S.IN_PRODUCTION, {A.OPERATOR}, scope=Scope.ORDERS_TRANSITION),
    _t(
        {S.ACCEPTED, S.IN_PRODUCTION},
        S.READY_FOR_PICKUP,
        {A.OPERATOR},
        scope=Scope.ORDERS_TRANSITION,
        guard=_fulfillment("pickup"),
    ),
    _t(
        {S.ACCEPTED, S.IN_PRODUCTION},
        S.SHIPPED,
        {A.OPERATOR},
        scope=Scope.ORDERS_TRANSITION,
        guard=_fulfillment("delivery"),
    ),
    _t(
        {S.ACCEPTED, S.IN_PRODUCTION},
        S.DELIVERED,
        {A.OPERATOR},
        scope=Scope.ORDERS_TRANSITION,
        guard=_fulfillment("none"),
    ),
    _t({S.READY_FOR_PICKUP, S.SHIPPED}, S.DELIVERED, {A.OPERATOR}, scope=Scope.ORDERS_TRANSITION),
)

# Timestamp column each target stamps.
STAMP = {
    S.PAYMENT_CONFIRMED: "paid_at",
    S.ACCEPTED: "accepted_at",
    S.READY_FOR_PICKUP: "ready_at",
    S.SHIPPED: "shipped_at",
    S.DELIVERED: "delivered_at",
    S.CANCELLED: "cancelled_at",
    S.FAILED: "failed_at",
}


def _allowed(
    order: OrderSnapshot, transition: Transition, actor: ActorKind, scopes: frozenset[str]
) -> str | None:
    """None when `actor` may take `transition` now; else why not."""
    if order.status not in transition.sources:
        return "wrong_status"
    if actor not in transition.actors:
        return "not_allowed"
    if actor == ActorKind.OPERATOR and transition.scope and transition.scope not in scopes:
        return "missing_scope"
    return transition.guard(order) if transition.guard else None


def allowed_targets(
    order: OrderSnapshot, actor: ActorKind, scopes: frozenset[str] = frozenset()
) -> list[str]:
    targets: list[str] = []
    for transition in TRANSITIONS:
        if transition.target not in targets and _allowed(order, transition, actor, scopes) is None:
            targets.append(transition.target)
    return targets


def check_transition(
    order: OrderSnapshot, target: str, actor: ActorKind, scopes: frozenset[str] = frozenset()
) -> Transition:
    """The transition that lets `actor` move `order` to `target`, or InvalidTransitionError
    (with the reason of the closest candidate and what this actor may do instead)."""
    # Why not: taken only from transitions this actor could make (the customer is told the
    # status is wrong, not that an operator could do it); a guard beats a scope beats a status.
    reasons: list[str] = []
    for transition in TRANSITIONS:
        if transition.target != target:
            continue
        reason = _allowed(order, transition, actor, scopes)
        if reason is None:
            return transition
        if actor in transition.actors:
            reasons.append(reason)
    rank = ["wrong_status", "missing_scope"]
    reason = (
        max(reasons, key=lambda r: rank.index(r) if r in rank else len(rank))
        if reasons
        else "not_allowed"
    )
    raise InvalidTransitionError(
        code=reason,
        status=order.status,
        target=target,
        allowed=allowed_targets(order, actor, scopes),
    )


def fulfillment_after(target: str, fulfillment_type: str) -> str | None:
    """Fulfillment status an order gets when it reaches `target` (None: unchanged)."""
    if fulfillment_type == "none":
        return FulfillmentStatus.NONE
    if target == S.CANCELLED:
        return FulfillmentStatus.CANCELLED
    if target == S.READY_FOR_PICKUP:
        return FulfillmentStatus.READY
    if target == S.SHIPPED:
        return FulfillmentStatus.OUT_FOR_DELIVERY
    if target == S.DELIVERED:
        return (
            FulfillmentStatus.PICKED_UP
            if fulfillment_type == "pickup"
            else FulfillmentStatus.DELIVERED
        )
    return None
