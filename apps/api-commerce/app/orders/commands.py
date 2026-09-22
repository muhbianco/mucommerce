"""Inputs of OrderService.place: where the lines come from and what the customer agreed to."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

OrderOrigin = Literal["storefront", "panel", "agent_llm", "agent_typebot"]


@dataclass(frozen=True, slots=True)
class CartSource:
    """Place the customer's active cart, as reviewed (version and total seen)."""

    cart_id: str
    cart_version: int


@dataclass(frozen=True, slots=True)
class Contact:
    name: str
    phone: str | None = None


@dataclass(frozen=True, slots=True)
class PlaceOrder:
    origin: OrderOrigin
    customer_id: str
    # Scoped by origin and customer: the same key always means the same order.
    idempotency_key: str
    source: CartSource
    contact: Contact
    # The total the customer saw; a different server total answers cart_changed.
    expected_total_cents: int
    # {"terms": 3, "privacy": 2}: the versions the customer accepted at checkout.
    consent: dict[str, int] = field(default_factory=dict)
    notes: str | None = None
    channel_refs: dict[str, Any] | None = None
    ip: str | None = None
    user_agent: str | None = None
