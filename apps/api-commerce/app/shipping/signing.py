"""Assina a cotação de frete para o cliente poder devolvê-la sem poder mexer nela.

A cotação sai do servidor, vive no navegador e volta no `place`. Sem assinatura, qualquer um
edita o preço e a loja paga a diferença no despacho. Com ela, o servidor não precisa guardar
cotação nenhuma: recalcula o HMAC e sabe se aquilo saiu daqui, para este carrinho, agora.

O que a assinatura amarra: loja, carrinho (itens + endereço), transportadora, serviço, preço e
o instante da cotação. Mudou qualquer coisa — item, CEP, preço —, a assinatura não confere.

Frete v2: também o **plano de volumes** (`plan` = `<hash>:<modo de etiqueta>`), para a etiqueta
sair igual à cotação. Vazio (cotação v1) não entra no texto assinado, então as assinaturas
antigas continuam conferindo byte a byte.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Sequence
from datetime import datetime, timedelta

from app.core.config import settings

_DOMAIN = b"shipping-quote-v1"


def cart_signature(
    *,
    tenant_id: str,
    destination_postal_code: str,
    lines: Sequence[tuple[str, int]],
) -> str:
    """Impressão digital do que está sendo cotado: variante + quantidade, e o destino.

    Ordenada, para a mesma cesta dar sempre a mesma assinatura independente da ordem em que o
    cliente adicionou.
    """
    corpo = "|".join(f"{variant}:{quantity}" for variant, quantity in sorted(lines))
    bruto = f"{tenant_id}#{_digits(destination_postal_code)}#{corpo}"
    return hashlib.sha256(bruto.encode("utf-8")).hexdigest()[:32]


def sign(
    *,
    tenant_id: str,
    cart: str,
    provider: str,
    service_code: str,
    price_cents: int,
    quoted_at: datetime,
    plan: str = "",
) -> str:
    return hmac.new(
        _key(),
        _payload(tenant_id, cart, provider, service_code, price_cents, quoted_at, plan),
        hashlib.sha256,
    ).hexdigest()


def verify(
    *,
    signature: str,
    tenant_id: str,
    cart: str,
    provider: str,
    service_code: str,
    price_cents: int,
    quoted_at: datetime,
    plan: str = "",
) -> bool:
    esperado = sign(
        tenant_id=tenant_id,
        cart=cart,
        provider=provider,
        service_code=service_code,
        price_cents=price_cents,
        quoted_at=quoted_at,
        plan=plan,
    )
    return hmac.compare_digest(esperado, signature or "")


def expired(quoted_at: datetime, now: datetime, ttl_minutes: int | None = None) -> bool:
    minutos = ttl_minutes if ttl_minutes is not None else settings.shipping_quote_ttl_minutes
    return now - quoted_at > timedelta(minutes=max(1, minutos))


def _key() -> bytes:
    segredo = settings.jwt_secret.get_secret_value().encode("utf-8")
    return hmac.new(segredo, _DOMAIN, hashlib.sha256).digest()


def _payload(
    tenant_id: str,
    cart: str,
    provider: str,
    service_code: str,
    price_cents: int,
    quoted_at: datetime,
    plan: str = "",
) -> bytes:
    carimbo = quoted_at.replace(microsecond=0).isoformat()
    texto = f"{tenant_id}|{cart}|{provider}|{service_code}|{price_cents}|{carimbo}"
    return (f"{texto}|{plan}" if plan else texto).encode()


def _digits(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())
