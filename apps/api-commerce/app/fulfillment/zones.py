"""Which delivery zone serves an address (pure).

A zone lists CEP ranges or districts of one city. The owner's order decides ties: the first
active zone that covers the address wins, and CEP-range zones are checked before district
ones (a CEP is exact, a district name is typed by people).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence

from app.tenancy.settings_schemas import DeliveryZone

_NON_DIGIT = re.compile(r"\D")
_SPACES = re.compile(r"\s+")


def normalize_cep(value: str) -> str:
    """Digits only: "01310-100" → "01310100"."""
    return _NON_DIGIT.sub("", value)


def normalize_place(value: str) -> str:
    """Accents, case and repeated spaces do not matter: "São  Paulo" → "sao paulo"."""
    ascii_text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return _SPACES.sub(" ", ascii_text).strip().casefold()


def match_zone(
    zones: Sequence[DeliveryZone],
    *,
    cep: str,
    city: str | None,
    state: str | None,
    district: str | None,
) -> DeliveryZone | None:
    active = [zone for zone in zones if zone.active]
    digits = normalize_cep(cep)
    if len(digits) == 8:
        for zone in active:
            if zone.kind == "cep_ranges" and any(
                r.start <= digits <= r.end for r in zone.cep_ranges
            ):
                return zone
    if not (city and state and district):
        return None
    where = (normalize_place(city), state.upper())
    wanted = normalize_place(district)
    for zone in active:
        if zone.kind != "districts" or zone.city is None or zone.state is None:
            continue
        if (normalize_place(zone.city), zone.state) != where:
            continue
        if any(normalize_place(name) == wanted for name in zone.districts):
            return zone
    return None
