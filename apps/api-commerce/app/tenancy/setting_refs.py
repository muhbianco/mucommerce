"""Settings that point at the tenant's own rows (images, products, categories) are checked on
write: an id from another tenant, or of the wrong kind of image, is refused with the list of
offending ids. The session must already carry the tenant (the ORM filter scopes the lookups).
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.models import Category, Product, ProductStatus
from app.core.exceptions import ValidationError
from app.media.models import MediaAsset, MediaOwner


def _references(key: str, value: dict[str, Any]) -> dict[str, set[str]]:
    refs: dict[str, set[str]] = {
        "brand_media": set(),
        "landing_media": set(),
        "products": set(),
        "categories": set(),
    }
    if key == "branding" and value.get("logo_media_id"):
        refs["brand_media"].add(value["logo_media_id"])
    elif key == "seo" and value.get("og_image_media_id"):
        refs["brand_media"].add(value["og_image_media_id"])
    elif key == "landing":
        for block in value.get("blocks", []):
            if block.get("media_id"):
                refs["landing_media"].add(block["media_id"])
            refs["landing_media"].update(block.get("media_ids", []))
            refs["products"].update(block.get("product_ids", []))
            refs["categories"].update(block.get("category_ids", []))
    return refs


#: De que donos cada tipo de referência aceita imagem. A página inicial aceita, além do material
#: da vitrine, as fotos de produto: são as melhores imagens que uma loja pequena tem, já são
#: públicas na vitrine, e deixá-las de fora fez o material novo da SGPipas — nove fotos de
#: produto — não chegar à página que ela pediu para remontar (docs/16 §A.2).
LANDING_MEDIA_OWNERS = (MediaOwner.LANDING, MediaOwner.PRODUCT)


async def _existing_media(
    session: AsyncSession, ids: Collection[str], owners: Collection[str]
) -> set[str]:
    stmt = (
        select(MediaAsset.id)
        .where(MediaAsset.id.in_(list(ids)))
        .where(MediaAsset.owner_type.in_([str(o) for o in owners]))
    )
    return set((await session.execute(stmt)).scalars())


async def check_setting_references(session: AsyncSession, key: str, value: dict[str, Any]) -> None:
    refs = _references(key, value)
    unknown: dict[str, list[str]] = {}
    for field, ids, owners in (
        ("media_ids", refs["brand_media"], (MediaOwner.TENANT_BRAND,)),
        ("media_ids", refs["landing_media"], LANDING_MEDIA_OWNERS),
    ):
        if ids:
            missing = ids - await _existing_media(session, ids, owners)
            if missing:
                unknown.setdefault(field, []).extend(sorted(missing))
    if refs["products"]:
        stmt = (
            select(Product.id)
            .where(Product.id.in_(list(refs["products"])))
            .where(Product.status != ProductStatus.ARCHIVED)
        )
        missing = refs["products"] - set((await session.execute(stmt)).scalars())
        if missing:
            unknown["product_ids"] = sorted(missing)
    if refs["categories"]:
        stmt = (
            select(Category.id)
            .where(Category.id.in_(list(refs["categories"])))
            .where(Category.archived_at.is_(None))
        )
        missing = refs["categories"] - set((await session.execute(stmt)).scalars())
        if missing:
            unknown["category_ids"] = sorted(missing)
    if unknown:
        raise ValidationError("Configuração aponta para itens inexistentes.", key=key, **unknown)
