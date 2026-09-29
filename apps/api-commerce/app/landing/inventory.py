"""O que a loja tem à disposição na hora de montar a página: imagens, produtos e categorias.

Isto é colhido **uma vez**, numa transação que fecha antes da chamada ao modelo, e fica gravado
no rascunho. Congelar importa por dois motivos:

- a proposta passa a explicar a si mesma. Sem o inventário guardado, "por que ele escolheu essa
  foto?" vira adivinhação seis semanas depois, quando a loja já trocou metade das imagens;
- o reparo determinístico precisa saber o que era válido **naquele momento**. Comparar a saída
  do modelo com o catálogo de agora acusaria como invenção um produto que existia.

Só entra o que o modelo pode referenciar de verdade: imagem pronta (processando não vai para a
página), produto publicado, categoria não arquivada. Oferecer um id que o `check_setting_refs`
vai recusar é gastar token para produzir erro.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.models import PUBLISHED_STATUSES, Category, Product
from app.media.models import MediaAsset, MediaOwner, MediaRole, MediaStatus
from app.tenancy.context import TenantContext

#: Tetos do que viaja no prompt. Não são regra de gosto — são o tamanho da conta: cada linha
#: aqui é token em toda tentativa, e uma loja com 400 produtos não cabe (nem ajuda).
MAX_INVENTORY_MEDIA = 24
MAX_INVENTORY_PRODUCTS = 30
MAX_INVENTORY_CATEGORIES = 12

#: Papéis que podem virar imagem de destaque ou de galeria. **O logotipo nunca entra**: ele já
#: aparece no cabeçalho da loja, e esticado num hero fica horrível. A regra é código, e não um
#: pedido no prompt, porque pedido o modelo esquece.
SCENE_ROLES = frozenset(
    {
        MediaRole.BANNER,
        MediaRole.PLACE_PHOTO,
        MediaRole.PRODUCT_PHOTO,
        MediaRole.TEAM_PHOTO,
        MediaRole.TEXTURE,
        MediaRole.OTHER,
    }
)

#: Donos cujas imagens já estão publicáveis na vitrine. O material do brief é insumo: serve para
#: a gente entender a loja e para extrair cor, mas não é imagem da página.
PAGE_MEDIA_OWNERS = (MediaOwner.LANDING, MediaOwner.TENANT_BRAND)


def usable_media(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """As imagens que podem ir para um bloco visual, na ordem em que o inventário as trouxe."""
    return [item for item in items if item.get("role") in SCENE_ROLES or item.get("role") is None]


async def collect_inventory(session: AsyncSession, tenant: TenantContext) -> dict[str, Any]:
    """O retrato do que existe agora. Roda dentro de uma transação, e ela fecha logo depois."""
    media_rows = (
        (
            await session.execute(
                select(MediaAsset)
                .where(MediaAsset.owner_type.in_([str(o) for o in PAGE_MEDIA_OWNERS]))
                .where(MediaAsset.status == MediaStatus.READY)
                .order_by(MediaAsset.owner_type, MediaAsset.position, MediaAsset.id)
                .limit(MAX_INVENTORY_MEDIA)
            )
        )
        .scalars()
        .all()
    )
    media = [
        {
            "id": row.id,
            "role": row.role,
            # O `alt` é o que a lojista escreveu sobre a imagem. É a única descrição que temos
            # dela sem olhar o pixel, e é justamente por isso que basta: `role` + `alt` entregam
            # quase todo o valor de visão por custo nenhum.
            "alt": row.alt,
            "owner": row.owner_type,
        }
        for row in media_rows
    ]

    product_rows = (
        await session.execute(
            select(Product.id, Product.name, Product.short_description)
            .where(Product.status.in_([str(s) for s in PUBLISHED_STATUSES]))
            .order_by(Product.position, Product.name)
            .limit(MAX_INVENTORY_PRODUCTS)
        )
    ).all()
    products = [{"id": pid, "name": name, "about": about} for pid, name, about in product_rows]

    category_rows = (
        await session.execute(
            select(Category.id, Category.name)
            .where(Category.archived_at.is_(None))
            .order_by(Category.position, Category.name)
            .limit(MAX_INVENTORY_CATEGORIES)
        )
    ).all()
    categories = [{"id": cid, "name": name} for cid, name in category_rows]

    return {
        "store_name": tenant.name,
        # A vitrine só mostra catálogo com o módulo ligado: propor blocos de produto numa loja
        # sem catálogo é propor uma página que nasce vazia.
        "catalog": bool(tenant.feature("catalog")),
        "media": media,
        "products": products,
        "categories": categories,
    }
