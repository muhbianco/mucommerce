"""SKU of variants the catalog creates (option matrix, event lots)."""

from __future__ import annotations

import itertools

from app.core.exceptions import ValidationError

MAX_SKU_LENGTH = 64


def next_variant_sku(product_sku: str, taken: set[str]) -> str:
    """`<product SKU>-1`, `-2`… skipping any SKU already in use in the store (added to `taken`)."""
    for number in itertools.count(1):
        candidate = f"{product_sku}-{number}"
        if len(candidate) > MAX_SKU_LENGTH:
            raise ValidationError(
                "SKU do produto longo demais para gerar variantes.", fields=["sku"]
            )
        if candidate not in taken:
            taken.add(candidate)
            return candidate
    raise AssertionError("unreachable")
