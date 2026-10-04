"""Cotação de frete para o carrinho: empacota, pergunta à transportadora, assina o resultado.

Esta é a única camada do envio que faz I/O. Ela existe para o `evaluate` continuar puro: aqui
falamos com o provedor e com o banco, e o que sai é um punhado de opções assinadas que o
domínio sabe conferir sozinho depois.

Princípio do caminho do checkout: **nunca travar e nunca mentir**. Timeout curto, cache da
chamada cara, e quando a transportadora não responde o cliente vê "frete indisponível agora",
não uma lista vazia que parece "não entregamos aí".

Frete v2 (flag `shipping.packing_v2`, docs/13-frete-v2.md): o motor de embalagem monta o plano
de volumes, a transportadora cota esse plano (uma chamada, com o seguro por volume) e cada
opção sai assinada junto com o hash do plano — é ele que o `place` reconstrói e o despacho usa.
Sem embalagem ativa, o v2 não se aplica e a cotação segue pelo v1.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.models import PHYSICAL_KINDS, Product, ProductVariant
from app.core.cache import TtlCache
from app.core.config import settings
from app.core.logging import get_logger
from app.integrations.credentials import CredentialStore
from app.shipping import registry, signing
from app.shipping.inputs import LineIn, load_packing_inputs
from app.shipping.packing import Box, MissingDimensions, PackItem, item_from_variant, pack
from app.shipping.packing.candidates import plan_candidates
from app.shipping.packing.model import ParcelPlan
from app.shipping.plan import label_mode, to_parcel
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
    # Frete v2: toda combinação passou do teto de volumes da loja (regras de embalagem).
    "too_many_parcels",
]

#: Recusa que o motor v2 acrescenta enquanto a etiqueta por volume (F7) não existe: a opção
#: some em vez de vender um envio que o despacho ainda não sabe comprar.
_PER_VOLUME_PENDING = "Envio em mais de um volume ainda não disponível neste serviço."


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
    #: Frete v2: `<hash do plano>:<modo de etiqueta>`, assinado junto. Vazio no v1.
    plan: str = ""
    delivery_min: int | None = None
    delivery_max: int | None = None

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
            "plan": self.plan,
            "delivery_min": self.delivery_min,
            "delivery_max": self.delivery_max,
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
        if self.tenant.feature("shipping.packing_v2"):
            resultado = await self._options_v2(provider, credenciais, cfg, destino, lines)
            if resultado is not None:
                return resultado
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

    # ------------------------------------------------------------------ frete v2

    async def _options_v2(
        self,
        provider: Any,
        credentials: ShippingCredentials,
        cfg: ShippingSettings,
        destino: str,
        lines: Sequence[QuoteLine],
    ) -> QuoteOutcome | None:
        """Planeja, cota as melhores combinações e assina, por serviço, a mais barata.

        Até `max_candidates` combinações (top-K da estimativa local), cotadas em paralelo com
        uma chamada cada — o sandbox mostrou que uma cotação com N volumes já traz o preço da
        remessa inteira. `None` = o v2 não se aplica aqui (sem embalagem ativa): segue o v1.
        """
        regras = cfg.packing
        entradas = await load_packing_inputs(
            self.session,
            [LineIn(line.variant_id, line.quantity_milli) for line in lines],
            regras,
            now=self.now,
        )
        if entradas.missing:
            return QuoteOutcome(problem="missing_dimensions", missing=entradas.missing)
        if not entradas.classes:
            return QuoteOutcome(problem="shipping_disabled")
        if not entradas.packages:
            logger.info("Frete v2 sem embalagem ativa; cotação pelo v1")
            return None
        candidatos = await asyncio.to_thread(
            plan_candidates,
            entradas.classes,
            entradas.packages,
            entradas.rules,
            top_k=regras.max_candidates,
        )
        planos = candidatos.candidates
        if not planos:
            return QuoteOutcome(problem="too_many_parcels")
        respostas = await self._quote_many(provider, credentials, cfg, destino, planos)
        escolha = choose_offers(planos, respostas, charge_material=regras.charge_material)
        carrinho = signing.cart_signature(
            tenant_id=self.tenant.id,
            destination_postal_code=destino,
            lines=[(line.variant_id, line.quantity_milli) for line in lines],
        )
        opcoes = [
            self._sign(
                cfg,
                oferta.option,
                carrinho,
                plan=f"{oferta.plan.hash}:{oferta.mode}",
                extra_cents=oferta.charged_material_cents,
            )
            for oferta in escolha.offers
        ]
        logger.info(
            "Cotação de frete",
            extra={
                "engine": "v2",
                "dest_prefix": destino[:3],
                "candidates": [
                    {"strategy": p.strategy, "parcels": len(p.parcels), "degraded": p.degraded}
                    for p in planos
                ],
                "calls_failed": sum(1 for r in respostas if r is None),
                "offers": [
                    {
                        "service": o.option.service_code,
                        "strategy": o.plan.strategy,
                        "parcels": len(o.plan.parcels),
                        "mode": o.mode,
                        "price_cents": o.option.price_cents,
                    }
                    for o in escolha.offers
                ],
            },
        )
        if not opcoes:
            if escolha.failed:
                return QuoteOutcome(problem="unavailable")
            return QuoteOutcome(problem="no_service", refusals=_refusals(escolha.refusals))
        return QuoteOutcome(options=tuple(sorted(opcoes, key=lambda o: o.price_cents)))

    async def quote_plans(
        self, plans: Sequence[ParcelPlan], *, destination_postal_code: str
    ) -> tuple[list[tuple[ShippingOption, ...] | None], QuoteProblem | None]:
        """Cota planos prontos (o simulador do painel): respostas por plano, ou o problema."""
        cfg = fulfillment_settings(self.tenant.settings).shipping
        destino = _digits(destination_postal_code)
        if not cfg.enabled or cfg.origin is None or len(destino) != 8:
            return [], "shipping_disabled"
        provider = registry.get_provider(cfg.provider)
        if provider is None or not registry.flag_on(self.tenant, cfg.provider):
            return [], "shipping_disabled"
        credenciais = await self._credentials(cfg)
        if credenciais is None:
            return [], "not_configured"
        return await self._quote_many(provider, credenciais, cfg, destino, plans), None

    async def _quote_many(
        self,
        provider: Any,
        credentials: ShippingCredentials,
        cfg: ShippingSettings,
        destino: str,
        plans: Sequence[ParcelPlan],
    ) -> list[tuple[ShippingOption, ...] | None]:
        """Uma chamada por pedido **distinto**, todas em paralelo, cada uma com o seu teto de
        tempo; o que falhou volta `None` e não derruba as outras (nem entra no cache)."""
        volumes = [tuple(to_parcel(v) for v in plano.parcels) for plano in plans]
        unicos: dict[str, tuple[Parcel, ...]] = {}
        for vols in volumes:
            unicos.setdefault(_cache_key(self.tenant.id, cfg, destino, vols), vols)
        chaves = list(unicos)
        respostas = await asyncio.gather(
            *(self._ask(provider, credentials, cfg, destino, unicos[k]) for k in chaves)
        )
        por_chave = dict(zip(chaves, respostas, strict=True))
        return [por_chave[_cache_key(self.tenant.id, cfg, destino, vols)] for vols in volumes]

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
            return tuple(
                ShippingOption(
                    **{**item, "parcel_prices_cents": tuple(item.get("parcel_prices_cents") or ())}
                )
                for item in guardado
            )
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

    def _sign(
        self,
        cfg: ShippingSettings,
        option: ShippingOption,
        cart: str,
        *,
        plan: str = "",
        extra_cents: int = 0,
    ) -> QuotedOption:
        preco = customer_price(option.price_cents, extra_cents, cfg)
        quoted_at = self.now.replace(microsecond=0)
        return QuotedOption(
            provider=cfg.provider,
            service_code=option.service_code,
            service_name=option.service_name,
            carrier=option.carrier,
            price_cents=preco,
            delivery_days=_plus(option.delivery_days, cfg.handling_days),
            quoted_at=quoted_at,
            cart=cart,
            plan=plan,
            delivery_min=_plus(option.delivery_min, cfg.handling_days),
            delivery_max=_plus(option.delivery_max, cfg.handling_days),
            signature=signing.sign(
                tenant_id=self.tenant.id,
                cart=cart,
                provider=cfg.provider,
                service_code=option.service_code,
                price_cents=preco,
                quoted_at=quoted_at,
                plan=plan,
            ),
        )


@dataclass(frozen=True, slots=True)
class Offer:
    """A combinação vencedora de um serviço."""

    plan: ParcelPlan
    option: ShippingOption
    mode: str
    #: Custo da embalagem que vai para o preço do cliente (só com `charge_material`).
    charged_material_cents: int


@dataclass(frozen=True, slots=True)
class OfferChoice:
    offers: tuple[Offer, ...]
    refusals: tuple[ShippingOption, ...]
    #: Alguma chamada falhou ou estourou o tempo (sem oferta, isso vira "indisponível").
    failed: bool


def choose_offers(
    plans: Sequence[ParcelPlan],
    responses: Sequence[tuple[ShippingOption, ...] | None],
    *,
    charge_material: bool,
) -> OfferChoice:
    """Por serviço, a combinação mais barata para a loja: preço + material; empate fica com a
    de menos volumes e depois com a ordem do ranking. Serviço que exige uma etiqueta por volume
    não vale para plano com mais de um volume enquanto a F7 não existir (vira recusa)."""
    melhor: dict[str, tuple[tuple[int, int, int], Offer]] = {}
    recusas: list[ShippingOption] = []
    falhou = False
    for posicao, (plano, resposta) in enumerate(zip(plans, responses, strict=True)):
        if resposta is None:
            falhou = True
            continue
        material = sum(v.material_cents for v in plano.parcels)
        for opcao in resposta:
            if not opcao.usable:
                recusas.append(opcao)
                continue
            modo = label_mode(len(plano.parcels), opcao.multi_volume_max)
            if modo == "per_volume":
                recusas.append(replace(opcao, error=_PER_VOLUME_PENDING))
                continue
            chave = (opcao.price_cents + material, len(plano.parcels), posicao)
            atual = melhor.get(opcao.service_code)
            if atual is None or chave < atual[0]:
                melhor[opcao.service_code] = (
                    chave,
                    Offer(plano, opcao, modo, material if charge_material else 0),
                )
    ofertas = tuple(oferta for _, oferta in sorted(melhor.values(), key=lambda t: t[0]))
    return OfferChoice(ofertas, tuple(recusas), falhou)


async def parcels_for(
    session: AsyncSession, lines: Sequence[QuoteLine], cfg: ShippingSettings
) -> tuple[Parcel, ...]:
    """Volumes de um conjunto de linhas (carrinho ou pedido).

    Mesma conta na cotação e no despacho: se divergir, a loja cobra um frete e paga outro.
    """
    if not lines:
        return ()
    variantes = await variants_for(session, [line.variant_id for line in lines])
    caixas = _boxes(cfg)
    # Um grupo por embalagem: produtos que viajam em caixas diferentes não podem ser somados na
    # mesma conta de volume. A chave `None` é a caixa padrão, de quem não escolheu nenhuma.
    por_caixa: dict[str | None, list[PackItem]] = defaultdict(list)
    faltando: list[str] = []
    for line in lines:
        par = variantes.get(line.variant_id)
        if par is None:
            continue
        variante, produto = par
        # Ingresso, serviço e digital não viajam: entrar aqui sem medida travava o frete do
        # carrinho inteiro com `missing_dimensions`, por causa de uma linha que nem vai na caixa.
        if produto.kind not in PHYSICAL_KINDS:
            continue
        # Para cima, nunca para o mais próximo: `round` é bancário (2,5 kg virava 2 peças) e
        # frete cotado a menos sai do bolso da loja no despacho.
        unidades = max(1, -(-line.quantity_milli // 1000))
        try:
            itens = item_from_variant(
                # Peso e medidas são do produto: hoje o catálogo não guarda medida por
                # variante, então P e GG pesam igual para a transportadora.
                weight_grams=produto.weight_grams,
                width_mm=produto.width_mm,
                height_mm=produto.height_mm,
                depth_mm=produto.depth_mm,
                value_cents=_unit_price(variante, produto) * unidades,
                quantity=unidades,
            )
        except MissingDimensions:
            faltando.append(variante.id)
            continue
        # Embalagem apagada depois de o produto apontar para ela cai na padrão. Recusar a
        # cotação por causa disso puniria o cliente por uma edição da loja.
        escolhida = produto.shipping_box_id if produto.shipping_box_id in caixas else None
        por_caixa[escolhida].extend(itens)
    if faltando:
        raise MissingDimensions("variantes sem medida", tuple(faltando))
    volumes: list[Parcel] = []
    for box_id, itens in por_caixa.items():
        volumes.extend(pack(itens, caixas.get(box_id) if box_id else _box(cfg)))
    return tuple(volumes)


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


def customer_price(carrier_cents: int, material_cents: int, cfg: ShippingSettings) -> int:
    """O que o cliente paga: frete da transportadora + material (se a loja cobra), com o
    acréscimo da loja por cima. Material entra antes do acréscimo: ele é custo dela."""
    return _with_markup(carrier_cents + material_cents, cfg)


def _plus(days: int | None, handling: int) -> int | None:
    """Prazo da transportadora + dias de preparo da loja (os dois em dias úteis)."""
    return days + handling if days is not None else None


def _with_markup(price_cents: int, cfg: ShippingSettings) -> int:
    """Acréscimo da loja (embalagem, mão de obra). Entra antes da assinatura, senão não vale."""
    return round(price_cents * (100 + cfg.markup_percent) / 100) + cfg.markup_cents


def _boxes(cfg: ShippingSettings) -> dict[str, Box]:
    """As embalagens cadastradas, por id. A caixa padrão não entra: ela não tem id próprio."""
    return {
        b.id: Box(
            width_mm=b.width_mm,
            height_mm=b.height_mm,
            depth_mm=b.depth_mm,
            max_weight_grams=b.max_weight_grams,
            empty_weight_grams=b.empty_weight_grams,
        )
        for b in cfg.boxes
        if b.id
    }


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
    # Os serviços entram na chave: sem eles, desligar o SEDEX só valia depois de o cache vencer.
    servicos = ",".join(sorted(s.code for s in cfg.services if s.active))
    return f"{tenant_id}:{cfg.provider}:{cfg.origin.postal_code}:{destino}:{servicos}:{corpo}"


def _as_dict(option: ShippingOption) -> dict[str, Any]:
    """Para o cache. Campo novo com default: entrada antiga do cache continua carregando."""
    return {
        "service_code": option.service_code,
        "service_name": option.service_name,
        "carrier": option.carrier,
        "price_cents": option.price_cents,
        "delivery_days": option.delivery_days,
        "error": option.error,
        "delivery_min": option.delivery_min,
        "delivery_max": option.delivery_max,
        "parcel_prices_cents": list(option.parcel_prices_cents),
        "multi_volume_max": option.multi_volume_max,
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
