"""Effective price: pure functions, no I/O. The storefront, the cart and the checkout (phase 2)
all price through here, so a promotion never shows one price and charges another.

Modifiers ("cobertura extra +R$ 3") add to the variant's effective price; `price_with_modifiers`
also validates the choice against the product's groups, so no caller can skip that check.

Promotion window is half-open, `[promo_starts_at, promo_ends_at)`: at the exact end instant
the regular price is back. A bound left empty means "open on that side".
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.core.exceptions import ValidationError

# R$ 10 milhões: far above any real ticket, low enough to catch a price converted to cents twice.
MAX_PRICE_CENTS = 1_000_000_000


@dataclass(frozen=True, slots=True)
class EffectivePrice:
    amount_cents: int
    # Regular price while a promotion runs (shown struck through); None otherwise.
    compare_at_cents: int | None
    promo_active: bool
    promo_ends_at: datetime | None


def promo_is_active(
    *,
    base_cents: int,
    promo_cents: int | None,
    starts_at: datetime | None,
    ends_at: datetime | None,
    now: datetime,
) -> bool:
    if promo_cents is None or not 0 < promo_cents < base_cents:
        return False
    if starts_at is not None and now < starts_at:
        return False
    return ends_at is None or now < ends_at


def effective_price(
    *,
    base_cents: int,
    promo_cents: int | None,
    starts_at: datetime | None,
    ends_at: datetime | None,
    now: datetime,
) -> EffectivePrice:
    if promo_is_active(
        base_cents=base_cents,
        promo_cents=promo_cents,
        starts_at=starts_at,
        ends_at=ends_at,
        now=now,
    ):
        assert promo_cents is not None
        return EffectivePrice(promo_cents, base_cents, True, ends_at)
    return EffectivePrice(base_cents, None, False, None)


def variant_price(
    *,
    variant_price_cents: int | None,
    base_cents: int,
    promo_cents: int | None,
    starts_at: datetime | None,
    ends_at: datetime | None,
    now: datetime,
) -> EffectivePrice:
    """A variant with its own price ignores the product promotion; one that inherits the base
    price inherits the promotion too."""
    if variant_price_cents is not None:
        return EffectivePrice(variant_price_cents, None, False, None)
    return effective_price(
        base_cents=base_cents,
        promo_cents=promo_cents,
        starts_at=starts_at,
        ends_at=ends_at,
        now=now,
    )


def check_promotion(
    *,
    base_cents: int,
    promo_cents: int | None,
    starts_at: datetime | None,
    ends_at: datetime | None,
) -> None:
    """Reject a promotion that could never apply or that would raise the price."""
    if promo_cents is None:
        if starts_at is not None or ends_at is not None:
            raise ValidationError("Período de promoção sem preço promocional.")
        return
    if not 0 < promo_cents < base_cents:
        raise ValidationError(
            "Preço promocional deve ser maior que zero e menor que o preço normal.",
            promo_price_cents=promo_cents,
            base_price_cents=base_cents,
        )
    if starts_at is not None and ends_at is not None and ends_at <= starts_at:
        raise ValidationError("Fim da promoção deve ser depois do início.")


@dataclass(frozen=True, slots=True)
class ChosenModifier:
    group_id: str
    group_name: str
    modifier_id: str
    name: str
    price_cents: int


@dataclass(frozen=True, slots=True)
class ModifiedPrice:
    """One unit: the variant's effective price plus the chosen modifiers."""

    unit_cents: int
    base: EffectivePrice
    modifiers: tuple[ChosenModifier, ...]


def price_with_modifiers(
    base: EffectivePrice,
    groups: Sequence[Mapping[str, Any]] | None,
    chosen_ids: Sequence[str],
) -> ModifiedPrice:
    """Check the choice (known and active modifiers, no repeats, each group within its min/max)
    and price it. Modifiers come out in the product's order, whatever order they were sent in."""
    if len(set(chosen_ids)) != len(chosen_ids):
        raise ValidationError("Adicional repetido.", code="modifier_repeated")
    wanted = set(chosen_ids)
    chosen: list[ChosenModifier] = []
    known: set[str] = set()
    for group in groups or []:
        count = 0
        for modifier in group["modifiers"]:
            if not modifier.get("active", True):
                continue
            known.add(modifier["id"])
            if modifier["id"] in wanted:
                count += 1
                chosen.append(
                    ChosenModifier(
                        group["id"],
                        group["name"],
                        modifier["id"],
                        modifier["name"],
                        int(modifier["price_cents"]),
                    )
                )
        if not group["min_select"] <= count <= group["max_select"]:
            raise ValidationError(
                "Escolha de adicionais fora do limite do grupo.",
                code="modifier_group_limit",
                group=group["name"],
                min=group["min_select"],
                max=group["max_select"],
            )
    unknown = sorted(wanted - known)
    if unknown:
        raise ValidationError(
            "Adicional indisponível.", code="modifier_unavailable", modifier_ids=unknown
        )
    total = base.amount_cents + sum(m.price_cents for m in chosen)
    return ModifiedPrice(total, base, tuple(chosen))


# ------------------------------------------------------------------ preço por quantidade

#: Um degrau: a partir de `min_qty_milli`, cada unidade sai por `unit_price_cents`.
#: Guardado como JSON no produto (ou na variante, que sobrepõe), sempre em ordem crescente.
MAX_TIERS = 8


def parse_tiers(raw: Sequence[Mapping[str, Any]] | None) -> tuple[tuple[int, int], ...]:
    """JSON guardado → degraus (quantidade, preço), ordenados. Lixo vira lista vazia."""
    if not raw:
        return ()
    degraus: list[tuple[int, int]] = []
    for item in raw:
        try:
            quantidade = int(item["min_qty_milli"])
            preco = int(item["unit_price_cents"])
        except (KeyError, TypeError, ValueError):
            continue
        if quantidade > 0 and preco > 0:
            degraus.append((quantidade, preco))
    return tuple(sorted(degraus))


def tier_price(tiers: Sequence[tuple[int, int]], quantity_milli: int) -> int | None:
    """Preço unitário do degrau que esta quantidade alcança. `None` = nenhum."""
    alcancado: int | None = None
    for minimo, preco in tiers:
        if quantity_milli >= minimo:
            alcancado = preco
        else:
            break  # ordenado: o primeiro que não alcança encerra a busca
    return alcancado


def apply_tiers(
    base: EffectivePrice, tiers: Sequence[tuple[int, int]], quantity_milli: int
) -> EffectivePrice:
    """Aplica o degrau ao preço base.

    Quando há promoção **e** degrau, vale o menor dos dois. Fazer o degrau sobrepor uma
    promoção melhor seria subir o preço de quem comprou mais — o contrário do que a loja
    anunciou, e a primeira reclamação do dia.
    """
    preco = tier_price(tiers, quantity_milli)
    if preco is None or preco >= base.amount_cents:
        return base
    # O preço cheio fica como `compare_at` para a vitrine poder riscar.
    return EffectivePrice(
        amount_cents=preco,
        compare_at_cents=base.compare_at_cents or base.amount_cents,
        promo_active=base.promo_active,
        promo_ends_at=base.promo_ends_at,
    )


def check_tiers(raw: Sequence[Mapping[str, Any]] | None, *, base_cents: int) -> None:
    """Recusa tabela que não é desconto: preço acima do cheio, ou que sobe com a quantidade."""
    if not raw:
        return
    if len(raw) > MAX_TIERS:
        raise ValidationError("Degraus demais na tabela de preço.", max_tiers=MAX_TIERS)
    degraus = parse_tiers(raw)
    if len(degraus) != len(raw):
        raise ValidationError("Degrau inválido: informe quantidade e preço maiores que zero.")
    quantidades = [q for q, _ in degraus]
    if len(set(quantidades)) != len(quantidades):
        raise ValidationError("Duas faixas com a mesma quantidade mínima.")
    anterior = base_cents
    for quantidade, preco in degraus:
        if preco >= anterior:
            raise ValidationError(
                "Cada faixa precisa custar menos que a anterior.",
                min_qty_milli=quantidade,
                unit_price_cents=preco,
            )
        anterior = preco
