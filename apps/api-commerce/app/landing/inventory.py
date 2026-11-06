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

from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.models import PUBLISHED_STATUSES, Category, Product
from app.landing.brand import LOGO_KINDS, SCENE_KINDS, Material
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


def _usable(item: Mapping[str, Any]) -> bool:
    """Uma imagem pode virar foto de destaque ou de galeria?

    A leitura do perfil da marca (`kind`) vence o papel declarado: é ela que sabe que o arquivo
    "logo.jpeg" da SGPipas, enviado sem papel como material da vitrine, é um emblema. Sem
    leitura e sem papel, só a foto de produto passa — **material sem papel não vira mais hero**,
    que foi o que pôs o emblema esticado no destaque duas vezes (docs/16 §A.2).
    """
    if item.get("owner") == str(MediaOwner.TENANT_BRAND):
        return False
    kind = item.get("kind")
    if kind is not None:
        return kind in SCENE_KINDS and kind not in LOGO_KINDS
    role = item.get("role")
    if role is not None:
        return role in SCENE_ROLES
    return item.get("owner") == str(MediaOwner.PRODUCT)


def usable_media(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """As imagens que podem ir para um bloco visual, na ordem em que o inventário as trouxe."""
    return [item for item in items if _usable(item)]


def with_analysis(
    inventory: Mapping[str, Any],
    *,
    analysis: Mapping[str, Mapping[str, Any]],
    materials: Iterable[Material],
) -> dict[str, Any]:
    """O inventário com a leitura do perfil da marca e as capas de produto. Puro.

    A leitura entra como `kind` e `description` — é o que o compositor lê no lugar da imagem —
    e decide o que pode virar foto de bloco. As capas de produto entram como imagens: eram o
    material novo da SGPipas, e não chegavam à página (docs/16 §A.2).
    """
    saida = dict(inventory)
    media: list[dict[str, Any]] = []
    for item in inventory.get("media") or []:
        novo = dict(item)
        info = analysis.get(str(item.get("id"))) or {}
        if info.get("kind"):
            novo["kind"] = info["kind"]
            if info.get("superseded_by") or info.get("relevance") == "irrelevant":
                novo["kind"] = "other"
        if not novo.get("alt") and info.get("description"):
            novo["alt"] = info["description"]
        media.append(novo)
    ids = {str(m.get("id")) for m in media}
    for material in materials:
        if material.owner != str(MediaOwner.PRODUCT) or material.media_id in ids:
            continue
        info = analysis.get(material.media_id) or {}
        media.append(
            {
                "id": material.media_id,
                "role": material.role or str(MediaRole.PRODUCT_PHOTO),
                "alt": info.get("description") or material.alt or material.product_name,
                "owner": material.owner,
                "kind": info.get("kind") or "product_photo",
            }
        )
    saida["media"] = media
    return saida


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
            # O que a lojista escreveu sobre a imagem. Sem isso, `with_analysis` põe a descrição
            # que o perfil da marca leu nela; sem nenhum dos dois, o modelo trabalha às cegas —
            # foi o caso da SGPipas.
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
