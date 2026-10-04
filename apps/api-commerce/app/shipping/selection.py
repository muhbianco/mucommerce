"""A cotação que o cliente escolheu, do jeito que ela foi mostrada para ele.

Trafega pelo navegador entre o checkout e o `place`, então vem sempre acompanhada da
assinatura (`app/shipping/signing.py`). O domínio confere a assinatura antes de acreditar no
preço — nada aqui é confiável só porque chegou.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


def parse_selection(raw: Any) -> ShippingSelection | None:
    """O que o carrinho guardou vira cotação de novo. Dado torto some em vez de explodir:
    quem avalia trata "sem cotação" como problema, não como erro."""
    if not isinstance(raw, dict):
        return None
    try:
        quoted_at = datetime.fromisoformat(str(raw["quoted_at"]))
    except (KeyError, TypeError, ValueError):
        return None
    if quoted_at.tzinfo is None:
        quoted_at = quoted_at.replace(tzinfo=UTC)
    try:
        return ShippingSelection(
            provider=str(raw["provider"]),
            service_code=str(raw["service_code"]),
            service_name=str(raw.get("service_name") or ""),
            carrier=str(raw.get("carrier") or ""),
            price_cents=int(raw["price_cents"]),
            quoted_at=quoted_at,
            signature=str(raw["signature"]),
            cart=str(raw["cart"]),
            delivery_days=int(raw["delivery_days"]) if raw.get("delivery_days") else None,
            plan=str(raw.get("plan") or ""),
            delivery_min=int(raw["delivery_min"]) if raw.get("delivery_min") else None,
            delivery_max=int(raw["delivery_max"]) if raw.get("delivery_max") else None,
        )
    except (KeyError, TypeError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class ShippingSelection:
    provider: str
    service_code: str
    service_name: str
    carrier: str
    price_cents: int
    quoted_at: datetime
    signature: str
    cart: str
    delivery_days: int | None = None
    #: Frete v2: `<hash do plano>:<modo de etiqueta>`, assinado junto. Vazio = cotação v1.
    plan: str = ""
    delivery_min: int | None = None
    delivery_max: int | None = None

    @property
    def plan_hash(self) -> str:
        return self.plan.partition(":")[0]

    @property
    def label_mode(self) -> str:
        return self.plan.partition(":")[2]

    def snapshot(self) -> dict[str, Any]:
        """O que fica congelado no pedido: sem assinatura, que não serve depois."""
        return {
            "provider": self.provider,
            "service_code": self.service_code,
            "service_name": self.service_name,
            "carrier": self.carrier,
            "price_cents": self.price_cents,
            "delivery_days": self.delivery_days,
            "delivery_min": self.delivery_min,
            "delivery_max": self.delivery_max,
            "quoted_at": self.quoted_at.isoformat(),
            "plan": self.plan or None,
        }
