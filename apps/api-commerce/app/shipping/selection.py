"""A cotação que o cliente escolheu, do jeito que ela foi mostrada para ele.

Trafega pelo navegador entre o checkout e o `place`, então vem sempre acompanhada da
assinatura (`app/shipping/signing.py`). O domínio confere a assinatura antes de acreditar no
preço — nada aqui é confiável só porque chegou.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


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

    def snapshot(self) -> dict[str, Any]:
        """O que fica congelado no pedido: sem assinatura, que não serve depois."""
        return {
            "provider": self.provider,
            "service_code": self.service_code,
            "service_name": self.service_name,
            "carrier": self.carrier,
            "price_cents": self.price_cents,
            "delivery_days": self.delivery_days,
            "quoted_at": self.quoted_at.isoformat(),
        }
