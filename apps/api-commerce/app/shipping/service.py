"""Cotação de frete para o carrinho: empacota, pergunta à transportadora, assina o resultado.

Esta é a única camada do envio que faz I/O. Ela existe para o `evaluate` continuar puro: aqui
falamos com o provedor e com o banco, e o que sai é um punhado de opções assinadas que o
domínio sabe conferir sozinho depois.

Princípio do caminho do checkout: **nunca travar e nunca mentir**. Timeout curto, cache da
chamada cara, e quando a transportadora não responde o cliente vê "frete indisponível agora",
não uma lista vazia que parece "não entregamos aí".
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.models import Product, ProductVariant
from app.core.cache import TtlCache
from app.core.config import settings
from app.core.logging import get_logger
from app.integrations.credentials import CredentialStore
from app.shipping import registry, signing
from app.shipping.packing import Box, MissingDimensions, PackItem, item_from_variant, pack
from app.shipping.provider import (
    Parcel,
    QuoteRequest,
    ShippingCredentials,
    ShippingOption,
    ShippingProviderError,
)
from app.tenancy.context import TenantContext
from app.tenancy.settings_schemas import ShippingSettings, fulfillment_settings

logger = get_logger(__name__)

_cache = TtlCache("shipping-quote")

QuoteProblem = Literal[
    "shipping_disabled",
    "not_configured",
    "missing_dimensions",
    "unavailable",
    "no_service",
]


@dataclass(frozen=True, slots=True)
class QuoteLine:
    variant_id: str
    quantity_milli: int


@dataclass(frozen=True, slots=True)
class QuotedOption:
    """Uma opção pronta para a tela — e para voltar assinada no `place`."""

    provider: str
    service_code: str
    service_name: str
    carrier: str
    price_cents: int
    delivery_days: int | None
    quoted_at: datetime
    signature: str
    cart: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "service_code": self.service_code,
            "service_name": self.service_name,
            "carrier": self.carrier,
            "price_cents": self.price_cents,
            "delivery_days": self.delivery_days,
            "quoted_at": self.quoted_at.isoformat(),
            "signature": self.signature,
            "cart": self.cart,
        }


@dataclass(frozen=True, slots=True)
class QuoteOutcome:
    options: tuple[QuotedOption, ...] = ()
    problem: QuoteProblem | None = None
    #: Só em `missing_dimensions`: quais variantes travaram a cotação.
    missing: tuple[str, ...] = ()
    #: Só em `no_service`: o que cada transportadora respondeu ao recusar. Sem isto, "nenhuma
    #: atende" manda o cliente conferir o endereço quando o problema é o tamanho do pacote.
    refusals: tuple[str, ...] = ()


class ShippingQuoteService:
    def __init__(self, session: AsyncSession, tenant: TenantContext, now: datetime) -> None:
        self.session = session
        self.tenant = tenant
        self.now = now

    async def options(
        self, lines: Sequence[QuoteLine], *, destination_postal_code: str
    ) -> QuoteOutcome:
        cfg = fulfillment_settings(self.tenant.settings).shipping
        destino = _digits(destination_postal_code)
        if not cfg.enabled or cfg.origin is None or not destino:
            return QuoteOutcome(problem="shipping_disabled")
        provider = registry.get_provider(cfg.provider)
        if provider is None or not registry.flag_on(self.tenant, cfg.provider):
            return QuoteOutcome(problem="shipping_disabled")
        credenciais = await self._credentials(cfg)
        if credenciais is None:
            return QuoteOutcome(problem="not_configured")
        try:
            volumes = await self._parcels(lines, cfg)
        except MissingDimensions as exc:
            return QuoteOutcome(problem="missing_dimensions", missing=exc.variants)
        if not volumes:
            return QuoteOutcome(problem="shipping_disabled")

        bruto = await self._ask(provider, credenciais, cfg, destino, volumes)
        if bruto is None:
            return QuoteOutcome(problem="unavailable")
        carrinho = signing.cart_signature(
            tenant_id=self.tenant.id,
            destination_postal_code=destino,
            lines=[(line.variant_id, line.quantity_milli) for line in lines],
        )
        opcoes = tuple(self._sign(cfg, opcao, carrinho) for opcao in bruto if opcao.usable)
        if not opcoes:
            return QuoteOutcome(problem="no_service", refusals=_refusals(bruto))
        return QuoteOutcome(options=tuple(sorted(opcoes, key=lambda o: o.price_cents)))

    # ------------------------------------------------------------------ interno

    async def _credentials(self, cfg: ShippingSettings) -> ShippingCredentials | None:
        store = CredentialStore(self.session, self.tenant.id)
        token = await store.get(cfg.provider, "access_token")
        if not token:
            return None
        return ShippingCredentials(
            secrets={"access_token": token},
            public_config={},
            sandbox=settings.environment != "production",
        )

    async def _parcels(
        self, lines: Sequence[QuoteLine], cfg: ShippingSettings
    ) -> tuple[Parcel, ...]:
        return await parcels_for(self.session, lines, cfg)

    async def _ask(
        self,
        provider: Any,
        credentials: ShippingCredentials,
        cfg: ShippingSettings,
        destino: str,
        volumes: tuple[Parcel, ...],
    ) -> tuple[ShippingOption, ...] | None:
        """Chamada cara, com cache e teto de tempo. `None` = não deu para cotar agora."""
        assert cfg.origin is not None
        chave = _cache_key(self.tenant.id, cfg, destino, volumes)
        guardado = await _cache.get(chave)
        if isinstance(guardado, list):
            return tuple(ShippingOption(**item) for item in guardado)
        pedido = QuoteRequest(
            origin_postal_code=cfg.origin.postal_code,
            destination_postal_code=destino,
            parcels=volumes,
            services=tuple(s.code for s in cfg.services if s.active),
        )
        try:
            async with asyncio.timeout(settings.shipping_timeout_seconds + 2):
                opcoes = await provider.quote(credentials, pedido)
        except (ShippingProviderError, TimeoutError) as exc:
            logger.warning(
                "Cotação de frete indisponível",
                extra={"provider": cfg.provider, "erro": type(exc).__name__},
            )
            return None
        await _cache.set(
            chave,
            [_as_dict(o) for o in opcoes],
            ttl_seconds=max(60, settings.shipping_quote_ttl_minutes * 60 // 2),
        )
        return tuple(opcoes)

    def _sign(self, cfg: ShippingSettings, option: ShippingOption, cart: str) -> QuotedOption:
        preco = _with_markup(option.price_cents, cfg)
        prazo = (
            option.delivery_days + cfg.handling_days if option.delivery_days is not None else None
        )
        quoted_at = self.now.replace(microsecond=0)
        return QuotedOption(
            provider=cfg.provider,
            service_code=option.service_code,
            service_name=option.service_name,
            carrier=option.carrier,
            price_cents=preco,
            delivery_days=prazo,
            quoted_at=quoted_at,
            cart=cart,
            signature=signing.sign(
                tenant_id=self.tenant.id,
                cart=cart,
                provider=cfg.provider,
                service_code=option.service_code,
                price_cents=preco,
                quoted_at=quoted_at,
            ),
        )


async def parcels_for(
    session: AsyncSession, lines: Sequence[QuoteLine], cfg: ShippingSettings
) -> tuple[Parcel, ...]:
    """Volumes de um conjunto de linhas (carrinho ou pedido).

    Mesma conta na cotação e no despacho: se divergir, a loja cobra um frete e paga outro.
    """
    if not lines:
        return ()
    variantes = await variants_for(session, [line.variant_id for line in lines])
    itens: list[PackItem] = []
    faltando: list[str] = []
    for line in lines:
        par = variantes.get(line.variant_id)
        if par is None:
            continue
        variante, produto = par
        unidades = max(1, round(line.quantity_milli / 1000))
        try:
            itens.extend(
                item_from_variant(
                    # Peso e medidas são do produto: hoje o catálogo não guarda medida por
                    # variante, então P e GG pesam igual para a transportadora.
                    weight_grams=produto.weight_grams,
                    width_mm=produto.width_mm,
                    height_mm=produto.height_mm,
                    depth_mm=produto.depth_mm,
                    value_cents=_unit_price(variante, produto) * unidades,
                    quantity=unidades,
                )
            )
        except MissingDimensions:
            faltando.append(variante.id)
    if faltando:
        raise MissingDimensions("variantes sem medida", tuple(faltando))
    return pack(itens, _box(cfg))


async def variants_for(
    session: AsyncSession, ids: Sequence[str]
) -> dict[str, tuple[ProductVariant, Product]]:
    if not ids:
        return {}
    stmt = (
        select(ProductVariant, Product)
        .join(Product, Product.id == ProductVariant.product_id)
        .where(ProductVariant.id.in_(list(dict.fromkeys(ids))))
    )
    return {v.id: (v, p) for v, p in (await session.execute(stmt)).tuples()}


def _unit_price(variant: ProductVariant, product: Product) -> int:
    """Valor declarado por unidade: preço da variante, senão o do produto.

    É estimativa de seguro, não cobrança: promoção e modificador não entram para não fazer o
    frete oscilar com campanha de preço.
    """
    return int(variant.price_cents or product.base_price_cents or 0)


def _with_markup(price_cents: int, cfg: ShippingSettings) -> int:
    """Acréscimo da loja (embalagem, mão de obra). Entra antes da assinatura, senão não vale."""
    return round(price_cents * (100 + cfg.markup_percent) / 100) + cfg.markup_cents


def _box(cfg: ShippingSettings) -> Box | None:
    if cfg.box is None:
        return None
    return Box(
        width_mm=cfg.box.width_mm,
        height_mm=cfg.box.height_mm,
        depth_mm=cfg.box.depth_mm,
        max_weight_grams=cfg.box.max_weight_grams,
        empty_weight_grams=cfg.box.empty_weight_grams,
    )


def _cache_key(
    tenant_id: str, cfg: ShippingSettings, destino: str, volumes: tuple[Parcel, ...]
) -> str:
    assert cfg.origin is not None
    corpo = ";".join(
        f"{p.weight_grams}x{p.width_mm}x{p.height_mm}x{p.depth_mm}:{p.value_cents}" for p in volumes
    )
    return f"{tenant_id}:{cfg.provider}:{cfg.origin.postal_code}:{destino}:{corpo}"


def _as_dict(option: ShippingOption) -> dict[str, Any]:
    return {
        "service_code": option.service_code,
        "service_name": option.service_name,
        "carrier": option.carrier,
        "price_cents": option.price_cents,
        "delivery_days": option.delivery_days,
        "error": option.error,
    }


def _refusals(options: Sequence[ShippingOption]) -> tuple[str, ...]:
    """O que as transportadoras responderam ao recusar, sem repetir e sem virar parede de texto.

    A recusa costuma ser a resposta inteira ("as dimensões excedem o limite"), e era justamente
    ela que a gente descartava junto com a opção. Texto de terceiro: entra como dado, cortado.
    """
    vistos: list[str] = []
    for opcao in options:
        motivo = " ".join((opcao.error or "").split())[:200]
        if motivo and motivo not in vistos:
            vistos.append(motivo)
    return tuple(vistos[:3])


def _digits(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())
