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
from app.shipping import signing
from app.shipping.selection import ShippingSelection
from app.tenancy.context import TenantContext
from app.tenancy.settings_schemas import FulfillmentMode, FulfillmentV2, fulfillment_settings

FulfillmentType = Literal["pickup", "delivery", "shipping", "none"]
FulfillmentProblem = Literal[
    "mode_unavailable",
    "location_unknown",
    "address_required",
    "out_of_zone",
    "below_minimum",
    "slot_required",
    "slot_invalid",
    "quote_required",
    "quote_expired",
    "quote_invalid",
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
    #: Só para `shipping`: a cotação escolhida, assinada por nós.
    shipping: ShippingSelection | None = None


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
    if (
        tenant.feature(f"shipping.{cfg.shipping.provider}")
        and cfg.shipping.enabled
        and cfg.shipping.origin is not None
    ):
        modes.append("shipping")
    return modes


def evaluate(
    tenant: TenantContext,
    choice: FulfillmentChoice,
    *,
    subtotal_cents: int,
    address: DeliveryAddress | None,
    now: datetime,
    cart_signature: str | None = None,
) -> FulfillmentQuote:
    """Fee, snapshot and problems of `choice` for an order of `subtotal_cents`.

    `cart_signature` só importa em `shipping`: é com ela que a cotação assinada é conferida
    contra o carrinho que está sendo fechado.
    """
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
    elif choice.type == "shipping":
        if address is None:
            return FulfillmentQuote("shipping", problems=("address_required",))
        problema = _shipping_problem(
            tenant, choice.shipping, now=now, cart_signature=cart_signature
        )
        if problema is not None:
            return FulfillmentQuote("shipping", problems=(problema,))
        escolha = choice.shipping
        assert escolha is not None  # _shipping_problem já garantiu
        fee = _shipping_fee(cfg, escolha, subtotal_cents)
        snapshot = escolha.snapshot() | {"fee_cents": fee}
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


def _shipping_problem(
    tenant: TenantContext,
    selection: ShippingSelection | None,
    *,
    now: datetime,
    cart_signature: str | None,
) -> FulfillmentProblem | None:
    """A cotação escolhida ainda vale? Ordem importa: faltando < vencida < adulterada."""
    if selection is None:
        return "quote_required"
    if signing.expired(selection.quoted_at, now):
        return "quote_expired"
    if cart_signature is not None and selection.cart != cart_signature:
        # Mudou item ou endereço depois de cotar: recotar é obrigatório, não opcional.
        return "quote_expired"
    confere = signing.verify(
        signature=selection.signature,
        tenant_id=tenant.id,
        cart=selection.cart,
        provider=selection.provider,
        service_code=selection.service_code,
        price_cents=selection.price_cents,
        quoted_at=selection.quoted_at,
    )
    return None if confere else "quote_invalid"


def _shipping_fee(cfg: FulfillmentV2, selection: ShippingSelection, subtotal_cents: int) -> int:
    """O preço já vem com acréscimo (foi assinado assim); aqui só entra o frete grátis."""
    gratis = cfg.shipping.free_above_cents
    if gratis is not None and subtotal_cents >= gratis:
        return 0
    return selection.price_cents


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
        # Endereço de origem e caixa são operação da loja: a vitrine só precisa saber que há
        # envio e a partir de quanto ele sai de graça.
        "shipping": {
            "enabled": "shipping" in modes,
            "free_above_cents": cfg.shipping.free_above_cents,
            "handling_days": cfg.shipping.handling_days,
        },
    }
