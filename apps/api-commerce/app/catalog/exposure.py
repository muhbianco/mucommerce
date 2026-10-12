"""Compra sem acesso à vitrine: só produto de compra direta na home (docs/14-pedidos-lunares.md §3).

Na loja fechada com a home aberta, quem entrou mas ainda não foi aprovado (nem tem a senha) pode
comprar os produtos que o lojista marcou como "aparece e vai direto ao carrinho". O carrinho
confere ao adicionar e o pedido confere de novo ao fechar: o lojista pode ter mudado a marca no
meio do caminho.
"""

from __future__ import annotations

from collections.abc import Collection

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cart.models import CartItem
from app.catalog.models import HomeExposure, Product, ProductVariant
from app.core.exceptions import DomainError


async def ensure_open_to_buy(
    session: AsyncSession, variant_ids: Collection[str], refusal: DomainError
) -> None:
    """Levanta `refusal` se alguma variação não é de produto com compra direta na home."""
    wanted = set(variant_ids)
    if not wanted:
        return
    rows = await session.execute(
        select(ProductVariant.id, Product.home_exposure)
        .join(Product, Product.id == ProductVariant.product_id)
        .where(ProductVariant.id.in_(list(wanted)))
    )
    exposure = dict(rows.tuples().all())
    if any(exposure.get(v) != HomeExposure.OPEN for v in wanted):
        raise refusal


async def cart_variant_ids(session: AsyncSession, cart_id: str) -> list[str]:
    rows = await session.execute(
        select(CartItem.variant_id).where(CartItem.cart_id == cart_id).limit(200)
    )
    return list(rows.scalars())
