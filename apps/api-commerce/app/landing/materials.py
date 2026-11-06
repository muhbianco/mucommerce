"""O material que diz algo sobre a marca: o que existe (banco) e como é (pixel).

Fatiado como o resto da geração, pela mesma razão — nunca segurar transação durante I/O:

    collect_materials   dentro da transação de claim: só linhas, nenhum byte.
    load_thumbnails     fora de transação: lê as miniaturas do storage e roda o Pillow.

Entram três origens, nesta ordem de peso: a marca (`tenant_brand`, ou papel logotipo), o material
da vitrine (`landing`) e as capas dos primeiros produtos publicados. As fotos de produto ficavam
de fora, e eram justamente o material novo da SGPipas (docs/16 §A.2).

Lê só a menor rendição pública (320px, WebP, dezenas de KB): é o bastante para cor e para o
modelo reconhecer o que é, e a conta de visão cresce com o tamanho da imagem.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Iterable
from dataclasses import replace
from typing import Any

from minio.error import S3Error
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from urllib3.exceptions import HTTPError as Urllib3HTTPError

from app.catalog.models import PUBLISHED_STATUSES, Product
from app.core.config import settings
from app.core.logging import get_logger
from app.core.storage import ObjectMissingError, ObjectStorage, ObjectTooLargeError
from app.landing.brand import MAX_PRODUCT_MATERIALS, Material
from app.landing.color import brand_accents, extract_palette, extract_swatches
from app.media.models import MediaAsset, MediaOwner, MediaStatus

logger = get_logger(__name__)

#: Teto de bytes por miniatura lida. Uma rendição de 320px em WebP passa longe disto; o teto
#: existe para um objeto errado no bucket não virar meio megabyte no pedido ao modelo.
MAX_THUMB_BYTES = 300_000
#: Material da vitrine e da marca considerado por proposta (o inventário tem o mesmo teto).
MAX_PAGE_MATERIALS = 24
#: Rendições preferidas para a miniatura, da menor para a maior.
THUMB_RENDITIONS = ("w320", "w600")


def _thumb_path(media: MediaAsset) -> str | None:
    renditions = media.renditions or {}
    for nome in THUMB_RENDITIONS:
        info = renditions.get(nome)
        if isinstance(info, dict) and info.get("key"):
            return str(info["key"])
    return None


def _material(media: MediaAsset, product_name: str | None = None) -> Material:
    return Material(
        media_id=media.id,
        owner=str(media.owner_type),
        role=media.role,
        alt=media.alt,
        filename=media.filename,
        checksum=media.checksum_sha256,
        width=media.width,
        height=media.height,
        product_name=product_name,
        thumb_path=_thumb_path(media),
        stored_palette=media.palette,
    )


async def collect_materials(session: AsyncSession) -> list[Material]:
    """Os arquivos da loja que contam para a identidade. Só linhas: nenhum byte é lido aqui."""
    pagina = (
        (
            await session.execute(
                select(MediaAsset)
                .where(
                    MediaAsset.owner_type.in_(
                        [str(MediaOwner.TENANT_BRAND), str(MediaOwner.LANDING)]
                    )
                )
                .where(MediaAsset.status == MediaStatus.READY)
                .order_by(MediaAsset.owner_type.desc(), MediaAsset.position, MediaAsset.id)
                .limit(MAX_PAGE_MATERIALS)
            )
        )
        .scalars()
        .all()
    )
    materiais = [_material(m) for m in pagina]

    # A capa de cada um dos primeiros produtos publicados: a primeira foto pronta, na ordem do
    # produto. Busca um punhado a mais para sobrar capa quando um produto não tem foto.
    linhas = (
        await session.execute(
            select(MediaAsset, Product.name)
            .join(Product, Product.id == MediaAsset.owner_id)
            .where(MediaAsset.owner_type == str(MediaOwner.PRODUCT))
            .where(MediaAsset.status == MediaStatus.READY)
            .where(Product.status.in_([str(s) for s in PUBLISHED_STATUSES]))
            .order_by(Product.position, Product.name, MediaAsset.position, MediaAsset.id)
            .limit(MAX_PRODUCT_MATERIALS * 4)
        )
    ).all()
    vistos: set[str] = set()
    for media, nome in linhas:
        if media.owner_id in vistos:
            continue
        vistos.add(str(media.owner_id))
        materiais.append(_material(media, product_name=str(nome)))
        if len(vistos) >= MAX_PRODUCT_MATERIALS:
            break
    return materiais


async def load_thumbnails(
    storage: ObjectStorage | None,
    materials: Iterable[Material],
    *,
    send: set[str],
) -> tuple[list[Material], dict[str, str], dict[str, dict[str, Any]]]:
    """Lê as miniaturas e devolve `(materiais com cor, data URLs a mandar, paletas a gravar)`.

    - toda imagem de marca e da vitrine ganha amostras de cor (o perfil sem modelo depende
      delas); foto de produto só quando vai ao modelo;
    - `send` são os arquivos que vão na chamada de visão: viram data URL. Data URL, e não o
      endereço público, porque assim nenhum provedor precisa buscar nada na nossa rede;
    - logotipo sem paleta gravada (os que subiram antes de 29/09 — a SGPipas) ganha a paleta
      agora: é o backfill que faltou.

    Falha de leitura de um arquivo não derruba nada: ele segue sem cor, e o resto continua.
    """
    saida: list[Material] = []
    data_urls: dict[str, str] = {}
    paletas: dict[str, dict[str, Any]] = {}
    for material in materials:
        if material.thumb_path is None or storage is None:
            saida.append(material)
            continue
        if material.priority >= 2 and material.media_id not in send:
            saida.append(material)
            continue
        try:
            dados = await storage.read(
                settings.storage_public_bucket, material.thumb_path, max_bytes=MAX_THUMB_BYTES
            )
        except (ObjectMissingError, ObjectTooLargeError, S3Error, Urllib3HTTPError, OSError) as exc:
            logger.warning(
                "brand material unreadable",
                extra={"media_id": material.media_id, "error": type(exc).__name__},
            )
            saida.append(material)
            continue
        # Pillow é CPU: fora do laço de eventos, que também conduz o driver do banco.
        swatches, accents = await asyncio.to_thread(_colors, dados)
        saida.append(replace(material, swatches=swatches, accents=accents))
        if material.media_id in send:
            data_urls[material.media_id] = "data:image/webp;base64," + base64.b64encode(
                dados
            ).decode("ascii")
        if material.priority == 0 and not material.stored_palette:
            paleta = await asyncio.to_thread(extract_palette, dados)
            if paleta is not None:
                paletas[material.media_id] = paleta.as_dict()
    return saida, data_urls, paletas


def _colors(data: bytes) -> tuple[tuple[dict[str, Any], ...], tuple[str, ...]]:
    return (
        tuple(s.as_dict() for s in extract_swatches(data)),
        tuple(brand_accents(data)),
    )


async def save_palettes(session: AsyncSession, palettes: dict[str, dict[str, Any]]) -> None:
    """Grava a paleta que faltava nos logotipos. Roda na transação que grava o perfil."""
    if not palettes:
        return
    linhas = (
        (await session.execute(select(MediaAsset).where(MediaAsset.id.in_(list(palettes)))))
        .scalars()
        .all()
    )
    for media in linhas:
        if not media.palette:
            media.palette = palettes[media.id]
