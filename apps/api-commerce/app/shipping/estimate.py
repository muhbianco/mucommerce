"""Frete na vitrine antes do login (frete v2, F6, docs/13-frete-v2.md §8).

O mesmo cálculo do carrinho — mesmas linhas, mesmo CEP, mesmo cache, mesmo preço — sem a
assinatura: a estimativa serve para mostrar, não para comprar. A compra recota no carrinho, com
o endereço do cliente, e é essa cotação assinada que vale no pedido.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.storefront import StorefrontCatalog
from app.shipping.service import QuoteLine, QuoteOutcome, ShippingQuoteService
from app.tenancy.context import TenantContext


async def estimate(
    session: AsyncSession,
    tenant: TenantContext,
    now: datetime,
    lines: Sequence[QuoteLine],
    *,
    destination_postal_code: str,
) -> QuoteOutcome:
    """Cota as linhas que a vitrine mostra; o resto (rascunho, arquivado, de outra loja) sai.

    As linhas seguem como vieram, sem somar repetidas: o carrinho cota assim, e a estimativa
    tem de cair na mesma chave de cache para dar o mesmo preço.
    """
    visiveis = await StorefrontCatalog(session, now).published_variant_ids(
        [line.variant_id for line in lines]
    )
    validas = [line for line in lines if line.variant_id in visiveis]
    if not validas:
        return QuoteOutcome(problem="no_items")
    return await ShippingQuoteService(session, tenant, now).options(
        validas, destination_postal_code=destination_postal_code
    )
