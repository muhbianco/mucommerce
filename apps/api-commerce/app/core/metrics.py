"""Counters for what money does (Prometheus; `/metrics` is only served inside the network).

Labels are bounded on purpose — provider, method, result, origin — never ids or store names.
"""

from __future__ import annotations

from prometheus_client import Counter

PAYMENT_WEBHOOKS = Counter(
    "commerce_payment_webhooks_total",
    "Payment notices received, by provider and what we did with them.",
    ("provider", "result"),
)
PAYMENTS_STARTED = Counter(
    "commerce_payments_started_total",
    "Payments created, by provider and method.",
    ("provider", "method"),
)
PAYMENTS_APPROVED = Counter(
    "commerce_payments_approved_total",
    "Payments approved, by provider and method.",
    ("provider", "method"),
)
ORDERS_PLACED = Counter(
    "commerce_orders_placed_total", "Orders created, by where they came from.", ("origin",)
)
REFUNDS_COMPLETED = Counter(
    "commerce_refunds_completed_total", "Refunds completed, by how they were made.", ("method",)
)
