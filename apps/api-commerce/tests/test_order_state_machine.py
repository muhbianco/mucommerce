"""Order state machine (stage E, S6): who may move an order where, and why not."""

from __future__ import annotations

import pytest

from app.core.exceptions import InvalidTransitionError
from app.core.scopes import Scope, scopes_for_tenant_role
from app.orders.state_machine import (
    TRANSITIONS,
    ActorKind,
    FulfillmentStatus,
    OrderSnapshot,
    OrderStatus,
    allowed_targets,
    check_transition,
    fulfillment_after,
)

S = OrderStatus
A = ActorKind
ADMIN = frozenset(str(s) for s in scopes_for_tenant_role("admin"))
OPS = frozenset(str(s) for s in scopes_for_tenant_role("ops"))


def order(status: str, kind: str = "pickup", **kw: object) -> OrderSnapshot:
    return OrderSnapshot(status=status, fulfillment_type=kind, **kw)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("status", "target", "actor", "scopes"),
    [
        (S.AWAITING_PAYMENT, S.PAYMENT_CONFIRMED, A.SYSTEM, frozenset()),
        (S.FAILED, S.PAYMENT_CONFIRMED, A.SYSTEM, frozenset()),
        (S.AWAITING_PAYMENT, S.FAILED, A.SYSTEM, frozenset()),
        (S.AWAITING_PAYMENT, S.CANCELLED, A.CUSTOMER, frozenset()),
        (S.AWAITING_PAYMENT, S.CANCELLED, A.AGENT, frozenset()),
        (S.AWAITING_PAYMENT, S.CANCELLED, A.OPERATOR, ADMIN),
        (S.PAYMENT_CONFIRMED, S.CANCELLED, A.CUSTOMER, frozenset()),
        (S.ACCEPTED, S.CANCELLED, A.CUSTOMER, frozenset()),
        (S.SHIPPED, S.CANCELLED, A.OPERATOR, ADMIN),
        (S.PAYMENT_CONFIRMED, S.ACCEPTED, A.SYSTEM, frozenset()),
        (S.PAYMENT_CONFIRMED, S.ACCEPTED, A.OPERATOR, OPS),
        (S.ACCEPTED, S.IN_PRODUCTION, A.OPERATOR, OPS),
        (S.IN_PRODUCTION, S.READY_FOR_PICKUP, A.OPERATOR, OPS),
        (S.READY_FOR_PICKUP, S.DELIVERED, A.OPERATOR, OPS),
    ],
)
def test_allowed_transitions(
    status: str, target: str, actor: ActorKind, scopes: frozenset[str]
) -> None:
    assert check_transition(order(status), target, actor, scopes).target == target


@pytest.mark.parametrize(
    ("snapshot", "target", "actor", "scopes", "code"),
    [
        (order(S.DELIVERED), S.CANCELLED, A.CUSTOMER, frozenset(), "wrong_status"),
        (order(S.AWAITING_PAYMENT), S.PAYMENT_CONFIRMED, A.OPERATOR, ADMIN, "not_allowed"),
        (order(S.AWAITING_PAYMENT), S.PAYMENT_CONFIRMED, A.CUSTOMER, frozenset(), "not_allowed"),
        (order(S.PAYMENT_CONFIRMED), S.CANCELLED, A.OPERATOR, OPS, "missing_scope"),
        (order(S.IN_PRODUCTION), S.CANCELLED, A.CUSTOMER, frozenset(), "wrong_status"),
        (
            order(S.ACCEPTED, customer_cancel_until=S.PAYMENT_CONFIRMED),
            S.CANCELLED,
            A.CUSTOMER,
            frozenset(),
            "cancel_window_closed",
        ),
        (
            order(S.AWAITING_PAYMENT, has_approved_payment=True),
            S.CANCELLED,
            A.CUSTOMER,
            frozenset(),
            "payment_approved",
        ),
        (order(S.ACCEPTED, "pickup"), S.SHIPPED, A.OPERATOR, OPS, "wrong_fulfillment"),
        (order(S.ACCEPTED, "delivery"), S.READY_FOR_PICKUP, A.OPERATOR, OPS, "wrong_fulfillment"),
        (order(S.ACCEPTED, "pickup"), S.DELIVERED, A.OPERATOR, OPS, "wrong_fulfillment"),
        (order(S.CANCELLED), S.PAYMENT_CONFIRMED, A.SYSTEM, frozenset(), "wrong_status"),
    ],
)
def test_refused_transitions_say_why(
    snapshot: OrderSnapshot, target: str, actor: ActorKind, scopes: frozenset[str], code: str
) -> None:
    with pytest.raises(InvalidTransitionError) as caught:
        check_transition(snapshot, target, actor, scopes)
    assert caught.value.details["code"] == code
    assert caught.value.details["allowed"] == allowed_targets(snapshot, actor, scopes)


def test_allowed_targets_per_actor() -> None:
    accepted = order(S.ACCEPTED, "delivery")
    assert allowed_targets(accepted, A.OPERATOR, ADMIN) == [S.CANCELLED, S.IN_PRODUCTION, S.SHIPPED]
    assert allowed_targets(accepted, A.OPERATOR, OPS) == [S.IN_PRODUCTION, S.SHIPPED]
    assert allowed_targets(accepted, A.CUSTOMER) == [S.CANCELLED]
    ticket = order(S.ACCEPTED, "none")
    assert S.DELIVERED in allowed_targets(ticket, A.OPERATOR, OPS)
    assert allowed_targets(order(S.DELIVERED), A.OPERATOR, ADMIN) == []


def test_every_operator_transition_names_a_scope() -> None:
    for transition in TRANSITIONS:
        if A.OPERATOR in transition.actors:
            assert transition.scope in (Scope.ORDERS_TRANSITION, Scope.ORDERS_CANCEL)


def test_fulfillment_follows_the_order() -> None:
    assert fulfillment_after(S.READY_FOR_PICKUP, "pickup") == FulfillmentStatus.READY
    assert fulfillment_after(S.DELIVERED, "pickup") == FulfillmentStatus.PICKED_UP
    assert fulfillment_after(S.SHIPPED, "delivery") == FulfillmentStatus.OUT_FOR_DELIVERY
    assert fulfillment_after(S.DELIVERED, "delivery") == FulfillmentStatus.DELIVERED
    assert fulfillment_after(S.CANCELLED, "delivery") == FulfillmentStatus.CANCELLED
    assert fulfillment_after(S.ACCEPTED, "none") == FulfillmentStatus.NONE
    assert fulfillment_after(S.ACCEPTED, "pickup") is None
