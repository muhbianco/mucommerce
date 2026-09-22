"""Fulfillment rules of a store: which modes it offers and whether a choice is valid.

A mode is offered when its flag (`pickup` / `delivery`) is on, the store enabled it in the
settings and it has at least one active location or zone. `evaluate` never raises for a bad
choice: it returns the problems, so the cart can show them and `place` can refuse with them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal, Protocol
from zoneinfo import ZoneInfo

from app.fulfillment.windows import Slot, find_slot, needs_slot
from app.fulfillment.zones import match_zone
from app.tenancy.context import TenantContext
from app.tenancy.settings_schemas import FulfillmentMode, FulfillmentV2, fulfillment_settings

FulfillmentType = Literal["pickup", "delivery", "none"]
FulfillmentProblem = Literal[
    "mode_unavailable",
    "location_unknown",
    "address_required",
    "out_of_zone",
    "below_minimum",
    "slot_required",
    "slot_invalid",
]


class DeliveryAddress(Protocol):
    postal_code: str
    city: str
    state: str
    district: str


@dataclass(frozen=True, slots=True)
class FulfillmentChoice:
    type: FulfillmentType
    pickup_location_id: str | None = None
    slot_date: date | None = None
    slot_start: str | None = None


@dataclass(frozen=True, slots=True)
class FulfillmentQuote:
    type: FulfillmentType
    fee_cents: int = 0
    # What the order keeps: the location, or the zone (id, name, fee) — no FK to settings.
    snapshot: dict[str, Any] = field(default_factory=dict)
    slot: Slot | None = None
    problems: tuple[FulfillmentProblem, ...] = ()


def offered_modes(tenant: TenantContext, cfg: FulfillmentV2 | None = None) -> list[FulfillmentMode]:
    cfg = cfg or fulfillment_settings(tenant.settings)
    modes: list[FulfillmentMode] = []
    if (
        tenant.feature("pickup")
        and cfg.pickup.enabled
        and any(loc.active for loc in cfg.pickup.locations)
    ):
        modes.append("pickup")
    if (
        tenant.feature("delivery")
        and cfg.delivery.enabled
        and any(zone.active for zone in cfg.delivery.zones)
    ):
        modes.append("delivery")
    return modes


def evaluate(
    tenant: TenantContext,
    choice: FulfillmentChoice,
    *,
    subtotal_cents: int,
    address: DeliveryAddress | None,
    now: datetime,
) -> FulfillmentQuote:
    """Fee, snapshot and problems of `choice` for an order of `subtotal_cents`."""
    if choice.type == "none":
        return FulfillmentQuote("none")
    cfg = fulfillment_settings(tenant.settings)
    if choice.type not in offered_modes(tenant, cfg):
        return FulfillmentQuote(choice.type, problems=("mode_unavailable",))
    problems: list[FulfillmentProblem] = []
    minimum = cfg.min_order_cents
    fee = 0
    snapshot: dict[str, Any]
    if choice.type == "pickup":
        location = next(
            (
                loc
                for loc in cfg.pickup.locations
                if loc.active and loc.id == choice.pickup_location_id
            ),
            None,
        )
        if location is None:
            return FulfillmentQuote("pickup", problems=("location_unknown",))
        snapshot = {
            "location_id": location.id,
            "name": location.name,
            "address": location.address,
            "instructions": location.instructions,
        }
    else:
        if address is None:
            return FulfillmentQuote("delivery", problems=("address_required",))
        zone = match_zone(
            cfg.delivery.zones,
            cep=address.postal_code,
            city=address.city,
            state=address.state,
            district=address.district,
        )
        if zone is None:
            return FulfillmentQuote("delivery", problems=("out_of_zone",))
        fee = zone.fee_cents
        if zone.min_order_cents is not None:
            minimum = max(minimum, zone.min_order_cents)
        snapshot = {
            "zone_id": zone.id,
            "name": zone.name,
            "fee_cents": zone.fee_cents,
            "eta_minutes": zone.eta_minutes,
        }
    if subtotal_cents < minimum:
        problems.append("below_minimum")
    slot = None
    if needs_slot(cfg.scheduling, choice.type):
        if choice.slot_date is None or choice.slot_start is None:
            problems.append("slot_required")
        else:
            slot = find_slot(
                cfg.scheduling,
                ZoneInfo(tenant.timezone),
                now,
                choice.type,
                day=choice.slot_date,
                start=choice.slot_start,
            )
            if slot is None:
                problems.append("slot_invalid")
    return FulfillmentQuote(choice.type, fee, snapshot, slot, tuple(problems))


def public_fulfillment(tenant: TenantContext) -> dict[str, Any]:
    """What the storefront may show: offered modes, active places and zone fees (no CEP lists
    or districts, which are long and only matter to the matcher)."""
    cfg = fulfillment_settings(tenant.settings)
    modes = offered_modes(tenant, cfg)
    return {
        "modes": modes,
        "min_order_cents": cfg.min_order_cents,
        "pickup_locations": [
            {
                "id": loc.id,
                "name": loc.name,
                "address": loc.address,
                "instructions": loc.instructions,
            }
            for loc in cfg.pickup.locations
            if "pickup" in modes and loc.active
        ],
        "delivery_zones": [
            {
                "id": zone.id,
                "name": zone.name,
                "fee_cents": zone.fee_cents,
                "min_order_cents": zone.min_order_cents,
                "eta_minutes": zone.eta_minutes,
            }
            for zone in cfg.delivery.zones
            if "delivery" in modes and zone.active
        ],
        "scheduling": cfg.scheduling.enabled,
    }
