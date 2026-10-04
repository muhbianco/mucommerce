"""Frete na vitrine sem login: estimativa por CEP na página do produto e no carrinho (F6)."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import CatalogReader, DbSession, require_same_origin
from app.cart.schemas import EntityId, Units
from app.core.exceptions import NotFoundError, RateLimitedError
from app.core.rate_limit import rate_limit, rate_limiter
from app.models.base import utcnow
from app.pricing.quote import MILLI
from app.schemas.common import StrictModel
from app.shipping.estimate import estimate
from app.shipping.service import QuoteLine

router = APIRouter(prefix="/storefront", tags=["Vitrine (público)"])

#: Por IP, como o plano pede: quem digita CEP não passa disso.
PER_IP = 20
#: Por loja, somando todos os visitantes: cada estimativa pode virar até 3 cotações na conta da
#: loja no Melhor Envio (o cache de 15 min segura a maior parte), e um robô espalhado por muitos
#: IPs não pode esgotar o limite dela.
PER_STORE = 300


class EstimateLineIn(StrictModel):
    variant_id: EntityId
    quantity: Units = Decimal(1)


class ShippingEstimateIn(StrictModel):
    postal_code: Annotated[str, Field(pattern=r"^\d{5}-?\d{3}$")]
    lines: Annotated[list[EstimateLineIn], Field(min_length=1, max_length=20)]


class ShippingEstimateOption(BaseModel):
    """Uma opção de frete para mostrar. Sem assinatura: não serve para fechar o pedido."""

    service_code: str
    service_name: str
    carrier: str
    #: Já com o acréscimo da loja; o frete grátis a vitrine aplica pelo subtotal dela.
    price_cents: int
    delivery_days: int | None
    #: Faixa em dias úteis, já com os dias de preparo da loja.
    delivery_min: int | None = None
    delivery_max: int | None = None


class ShippingEstimateRead(BaseModel):
    options: list[ShippingEstimateOption] = []
    #: `None` = deu certo; senão o motivo (os mesmos do carrinho, mais `no_items`).
    problem: str | None = None
    #: O que as transportadoras responderam ao recusar (texto delas, no máximo três).
    refusals: list[str] = []


@router.post(
    "/shipping/estimate",
    response_model=ShippingEstimateRead,
    summary="Estima o frete por CEP, sem login (não serve para fechar o pedido)",
    dependencies=[
        Depends(require_same_origin),
        Depends(rate_limit("shipping_estimate", PER_IP, 60)),
    ],
)
async def estimate_shipping(
    session: DbSession, tenant: CatalogReader, body: ShippingEstimateIn
) -> ShippingEstimateRead:
    # Junto com o frete v2 (liberação por loja na F8): loja sem a flag não tem estimativa.
    if not (tenant.feature("checkout") and tenant.feature("shipping.packing_v2")):
        raise NotFoundError("Recurso não encontrado.")
    teto = await rate_limiter.hit(f"shipping_estimate_store:{tenant.id}", PER_STORE, 60)
    if not teto.allowed:
        raise RateLimitedError(retry_after_seconds=teto.retry_after_seconds, limit=PER_STORE)
    resultado = await estimate(
        session,
        tenant,
        utcnow(),
        [QuoteLine(line.variant_id, int(line.quantity * MILLI)) for line in body.lines],
        destination_postal_code=body.postal_code,
    )
    return ShippingEstimateRead(
        options=[
            ShippingEstimateOption(
                service_code=opcao.service_code,
                service_name=opcao.service_name,
                carrier=opcao.carrier,
                price_cents=opcao.price_cents,
                delivery_days=opcao.delivery_days,
                delivery_min=opcao.delivery_min,
                delivery_max=opcao.delivery_max,
            )
            for opcao in resultado.options
        ],
        problem=resultado.problem,
        refusals=list(resultado.refusals),
    )
