"""Transforma os blocos guardados no que a página precisa para renderizar.

Um resolver só, duas portas: a vitrine (`GET /storefront/landing`) e a prévia do painel
(`GET /admin/tenants/{id}/landing/preview`). É por construção, não por disciplina, que a prévia
não diverge do que o cliente vê — se divergisse, o lojista publicaria confiando numa tela que
mente.

O que este arquivo faz, além de trocar id por conteúdo:

- **esconde o que não é para ver**: bloco de catálogo some quando a loja não tem catálogo, e
  vira "entre para ver" quando o visitante ainda não tem acesso — com as listas vazias, nunca
  com os produtos dentro;
- **degrada em silêncio**: imagem que ainda está processando, produto despublicado ou categoria
  arquivada simplesmente não aparecem. Uma página inicial não pode dar erro porque uma foto
  saiu do ar;
- **põe um teto no total da página**: doze blocos podendo pedir doze imagens cada dá cento e
  quarenta e quatro imagens numa consulta. O teto corta o excedente sem recusar o que já está
  salvo — regra de gosto não vira erro de validação;
- **mostra os expostos** na loja fechada com a home aberta: quem ainda não entrou vê os
  produtos que o lojista marcou para aparecer na home (docs/14-pedidos-lunares.md §3), e só
  eles. Os da vitrine de destaques entram no bloco; os que nenhum bloco cita vão para o primeiro
  bloco de destaques, ou para um bloco "Destaques" criado logo depois do primeiro.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.storefront import StorefrontCatalog
from app.media.service import ready_media_by_ids
from app.models.base import utcnow
from app.tenancy.context import TenantContext
from app.tenancy.settings_schemas import LandingV1

#: Tetos por página inteira, não por bloco: protegem a consulta sem impedir que o lojista salve.
MAX_PAGE_MEDIA = 32
MAX_PAGE_PRODUCTS = 48
MAX_PAGE_CATEGORIES = 20
#: Produtos expostos na home de quem ainda não entrou (o mesmo teto de um bloco de destaques).
MAX_EXPOSED = 12
EXPOSED_TITLE = "Destaques"

CATALOG_BLOCKS = frozenset({"featured_products", "categories"})


def _capped(ids: list[str], limit: int) -> list[str]:
    """Os primeiros `limit` ids distintos, na ordem em que a página os pede."""
    seen: dict[str, None] = {}
    for value in ids:
        if len(seen) >= limit:
            break
        seen.setdefault(value, None)
    return list(seen)


async def resolve_landing(
    session: AsyncSession,
    tenant: TenantContext,
    *,
    show_catalog: bool,
    exposed: bool = False,
    blocks_override: list[dict[str, Any]] | None = None,
    card_payload: Any,
    category_payload: Any,
    image_payload: Any,
) -> list[dict[str, Any]]:
    """Blocos prontos para a tela.

    `blocks_override` é o que permite a prévia: em vez do que está publicado, resolve os blocos
    que o painel está mostrando. `show_catalog` diz se este visitante pode ver produto — a
    vitrine calcula pelo acesso da loja; a prévia é sempre do dono, que pode. `exposed` (só
    com `show_catalog` falso): a loja é fechada com a home aberta, e os produtos expostos
    aparecem para quem ainda não entrou.

    Os três `*_payload` chegam de fora porque quem sabe desenhar produto, categoria e imagem são
    os esquemas da API; aqui só se decide o que entra.
    """
    landing = LandingV1.model_validate(tenant.settings.get("landing") or {})
    blocks = (
        blocks_override if blocks_override is not None else [b.model_dump() for b in landing.blocks]
    )
    catalog = StorefrontCatalog(session, utcnow())

    media_ids = _capped(
        [
            media_id
            for block in blocks
            for media_id in ([block["media_id"]] if block.get("media_id") else [])
            + list(block.get("media_ids") or [])
        ],
        MAX_PAGE_MEDIA,
    )
    media = await ready_media_by_ids(session, media_ids)

    product_ids = (
        _capped([pid for b in blocks for pid in (b.get("product_ids") or [])], MAX_PAGE_PRODUCTS)
        if show_catalog
        else []
    )
    cards = {
        c.product.id: c
        for c in (
            await catalog.list_cards(limit=len(product_ids), product_ids=product_ids)
            if product_ids
            else []
        )
    }
    exposed_cards = (
        (await catalog.list_cards(limit=MAX_EXPOSED, exposed_only=True))[:MAX_EXPOSED]
        if exposed and not show_catalog and tenant.feature("catalog")
        else []
    )
    if exposed_cards:
        cards = {c.product.id: c for c in exposed_cards}
        cited = {pid for b in blocks for pid in (b.get("product_ids") or [])}
        loose = [c.product.id for c in exposed_cards if c.product.id not in cited]
        blocks = _place_loose(blocks, loose)
    categories = {c.id: c for c in (await catalog.categories() if show_catalog else [])}

    catalog_on = tenant.feature("catalog")
    resolved: list[dict[str, Any]] = []
    for block in blocks:
        kind = block.get("type")
        catalog_block = kind in CATALOG_BLOCKS
        if catalog_block and not catalog_on:
            continue  # a loja não tem catálogo: o bloco existe para ninguém
        out = {k: v for k, v in block.items() if k not in {"media_id", "media_ids"}}
        if block.get("media_id"):
            image = media.get(block["media_id"])
            out["image"] = image_payload(image) if image else None
        if "media_ids" in block:
            out["images"] = [
                image_payload(media[m]) for m in (block["media_ids"] or []) if m in media
            ]
        if kind == "featured_products":
            out["products"] = [
                card_payload(cards[pid], tenant.currency)
                for pid in (block.get("product_ids") or [])
                if pid in cards
            ]
            out.pop("product_ids", None)
        if kind == "categories":
            out["categories"] = [
                category_payload(categories[cid])
                for cid in _capped(block.get("category_ids") or [], MAX_PAGE_CATEGORIES)
                if cid in categories
            ]
            out.pop("category_ids", None)
        if catalog_block and not show_catalog and not out.get("products"):
            # Falta login ou aprovação: fica a seção e o título que a loja escreveu, marcados
            # como travados, para o visitante ver que há mais depois de entrar. As listas
            # montadas acima estão vazias — nenhum produto sai da loja por este caminho, a não
            # ser os que o lojista expôs na home.
            out["locked"] = True
        resolved.append(out)
    return resolved


def _place_loose(blocks: list[dict[str, Any]], loose: list[str]) -> list[dict[str, Any]]:
    """Os expostos que nenhum bloco cita entram no primeiro bloco de destaques (até o teto
    dele), ou num bloco novo logo depois do primeiro bloco da página."""
    if not loose:
        return blocks
    out = [dict(b) for b in blocks]
    for block in out:
        if block.get("type") == "featured_products":
            ids = list(block.get("product_ids") or [])
            block["product_ids"] = ids + loose[: max(MAX_EXPOSED - len(ids), 0)]
            return out
    novo = {
        "id": "destaques-expostos",
        "type": "featured_products",
        "title": EXPOSED_TITLE,
        "product_ids": loose,
        "variant": "grid",
    }
    return [*out[:1], novo, *out[1:]]
