"""Repasse da taxa de pagamento ao cliente (puro).

A Lei 13.455/2017 permite preço diferente por meio de pagamento **desde que informado**. É por
isso que o acréscimo aparece na tela antes de a pessoa confirmar, e não só na fatura do cartão.

Duas escolhas que valem a pena entender:

*O acréscimo é sobre o total do pedido*, não só sobre os produtos: a maquininha cobra a taxa em
cima do valor que passa, frete incluído. Cobrar só sobre os produtos devolveria menos do que a
loja paga.

*O arredondamento é para cima* (centavo cheio). Metade de centavo por pedido some no relatório e
aparece na conta da loja no fim do mês.
"""

from __future__ import annotations

import math
from typing import Any, Literal, cast

from app.tenancy.settings_schemas import MethodSurcharge, PaymentsV1

#: Meios que aceitam acréscimo. Pix entra por completude: a loja *pode* configurar, mas o
#: padrão é zero, porque a taxa do Pix é ordens de grandeza menor.
METHODS = ("pix", "card", "link")


def rule(cfg: PaymentsV1, method: str, installments: int) -> MethodSurcharge:
    """Qual regra vale para este meio e este parcelamento."""
    if method == "card" and cfg.card_installments:
        parcelas = max(1, installments)
        # Faixas lidas da menor para a maior: a primeira que cobre o parcelamento vence.
        for faixa in sorted(cfg.card_installments, key=lambda f: f.up_to):
            if parcelas <= faixa.up_to:
                return MethodSurcharge(percent_bps=faixa.percent_bps, fixed_cents=faixa.fixed_cents)
        # Parcelou além da última faixa configurada: vale a última (a mais cara).
        ultima = max(cfg.card_installments, key=lambda f: f.up_to)
        return MethodSurcharge(percent_bps=ultima.percent_bps, fixed_cents=ultima.fixed_cents)
    if method not in METHODS:
        return MethodSurcharge()
    return cfg.surcharge.get(cast('Literal["pix", "card", "link"]', method)) or MethodSurcharge()


def compute(cfg: PaymentsV1, *, method: str, installments: int, base_cents: int) -> int:
    """Quanto entra a mais no pedido por pagar assim. Zero quando a loja não repassa."""
    if not cfg.enabled or base_cents <= 0:
        return 0
    regra = rule(cfg, method, installments)
    if regra.zero:
        return 0
    percentual = math.ceil(base_cents * regra.percent_bps / 10_000)
    return max(0, percentual + regra.fixed_cents)


def preview(cfg: PaymentsV1, *, base_cents: int, installments: int = 1) -> dict[str, int]:
    """O que cada meio custaria — para a tela mostrar antes de a pessoa escolher."""
    return {
        method: compute(cfg, method=method, installments=installments, base_cents=base_cents)
        for method in METHODS
    }


def set_order_surcharge(order: Any, cents: int) -> None:
    """Põe (ou tira) o acréscimo no total do pedido. Idempotente.

    Recalcula sempre a partir da base — total menos o acréscimo atual —, então chamar duas
    vezes, ou trocar de meio de pagamento, não empilha taxa em cima de taxa.
    """
    base = int(order.total_cents) - int(order.payment_surcharge_cents or 0)
    order.payment_surcharge_cents = max(0, int(cents))
    order.total_cents = base + order.payment_surcharge_cents
