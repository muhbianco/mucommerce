"""Payment status rules (pure).

The provider is the truth, but a status never goes backwards: approved does not return to
pending, a refund does not become approved again. `rejected | cancelled | expired → approved`
is allowed on purpose — a Pix paid after we gave up is still money in the store's account, and
the order side decides what to do with it (late payment).
"""

from __future__ import annotations

from app.payments.models import PaymentStatus as P

_NEXT: dict[str, frozenset[str]] = {
    P.PENDING: frozenset({P.REQUIRES_ACTION, P.APPROVED, P.REJECTED, P.CANCELLED, P.EXPIRED}),
    P.REQUIRES_ACTION: frozenset({P.APPROVED, P.REJECTED, P.CANCELLED, P.EXPIRED}),
    P.REJECTED: frozenset({P.APPROVED}),
    P.CANCELLED: frozenset({P.APPROVED}),
    P.EXPIRED: frozenset({P.APPROVED}),
    P.APPROVED: frozenset({P.PARTIALLY_REFUNDED, P.REFUNDED, P.CHARGEBACK}),
    P.PARTIALLY_REFUNDED: frozenset({P.REFUNDED, P.CHARGEBACK}),
    P.REFUNDED: frozenset(),
    P.CHARGEBACK: frozenset(),
}

CLOSED = frozenset(
    {P.APPROVED, P.REJECTED, P.CANCELLED, P.EXPIRED, P.PARTIALLY_REFUNDED, P.REFUNDED, P.CHARGEBACK}
)


def can_transition(current: str, target: str) -> bool:
    return target in _NEXT.get(current, frozenset())
