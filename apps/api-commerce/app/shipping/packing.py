"""Das linhas do carrinho para volumes (puro, sem I/O).

Empacotar de verdade é NP-difícil e ninguém precisa disso aqui: a loja define uma caixa padrão
e nós enchemos caixas por peso e por volume cúbico, respeitando o limite de peso da caixa. Peça
que não cabe na caixa padrão vira volume próprio, com as medidas dela.

O resultado alimenta a cotação e a etiqueta, então erra para o lado seguro: **arredonda para
cima**. Frete cotado a menos sai do bolso da loja no despacho.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from app.shipping.provider import Parcel

#: Sem caixa configurada, um envelope/caixa pequena de correio. Só evita quebrar a cotação.
DEFAULT_BOX_MM = (200, 150, 100)
DEFAULT_BOX_MAX_GRAMS = 30_000


@dataclass(frozen=True, slots=True)
class Box:
    width_mm: int
    height_mm: int
    depth_mm: int
    max_weight_grams: int = DEFAULT_BOX_MAX_GRAMS
    #: Tara da embalagem: caixa vazia também pesa.
    empty_weight_grams: int = 0

    @property
    def volume_mm3(self) -> int:
        return self.width_mm * self.height_mm * self.depth_mm

    @property
    def usable_grams(self) -> int:
        """Quanto cabe de produto: o teto menos a embalagem vazia.

        É **este** número que decide se a peça entra, e não `max_weight_grams`. Enquanto a
        peneira olhava o teto cheio e o enchimento olhava o útil, uma peça no meio do caminho
        (numa caixa de 300 g com 100 g de tara, qualquer coisa entre 201 g e 300 g) passava na
        peneira e abria uma caixa sozinha que estourava o próprio limite declarado. Uma
        definição só de "cabe em peso", usada pelos dois lados.
        """
        return max(0, self.max_weight_grams - self.empty_weight_grams)

    def fits(self, item: PackItem) -> bool:
        """Cabe na caixa girando o item? Compara as dimensões ordenadas e o peso útil."""
        caixa = sorted((self.width_mm, self.height_mm, self.depth_mm))
        peca = sorted((item.width_mm, item.height_mm, item.depth_mm))
        return all(p <= c for p, c in zip(peca, caixa, strict=True)) and (
            item.weight_grams <= self.usable_grams
        )


@dataclass(frozen=True, slots=True)
class PackItem:
    """Uma unidade a embalar. Quantidade vira repetição: o empacotador não precisa saber."""

    weight_grams: int
    width_mm: int
    height_mm: int
    depth_mm: int
    value_cents: int = 0

    @property
    def volume_mm3(self) -> int:
        return self.width_mm * self.height_mm * self.depth_mm


class MissingDimensions(ValueError):
    """Item sem peso ou sem as três medidas: não dá para cotar, e chutar sai caro.

    Carrega quais variantes travaram, para a tela dizer o que falta em vez de "erro".
    """

    def __init__(self, message: str, variants: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.variants = variants


DEFAULT_BOX = Box(*DEFAULT_BOX_MM)


def pack(items: Sequence[PackItem], box: Box | None = None) -> tuple[Parcel, ...]:
    """Volumes para a cotação. Lista vazia devolve vazio (quem chama decide o que fazer)."""
    if not items:
        return ()
    box = box or DEFAULT_BOX
    soltos = [item for item in items if not box.fits(item)]
    na_caixa = [item for item in items if box.fits(item)]
    volumes: list[Parcel] = [_own_parcel(item) for item in soltos]
    volumes.extend(_fill_boxes(na_caixa, box))
    return tuple(volumes)


def _own_parcel(item: PackItem) -> Parcel:
    """Peça grande demais para a caixa padrão viaja sozinha, com as medidas dela."""
    return Parcel(
        weight_grams=item.weight_grams,
        width_mm=item.width_mm,
        height_mm=item.height_mm,
        depth_mm=item.depth_mm,
        value_cents=item.value_cents,
    )


def _fill_boxes(items: Sequence[PackItem], box: Box) -> list[Parcel]:
    """Caixas cheias por peso e por volume, das peças maiores para as menores.

    Não é bin packing ótimo — é o suficiente para não subestimar: o volume cúbico da caixa é o
    teto, e a caixa fecha quando o próximo item não cabe em peso ou em volume.
    """
    if not items:
        return []
    restantes = sorted(items, key=lambda i: (i.volume_mm3, i.weight_grams), reverse=True)
    caixas: list[list[PackItem]] = []
    peso: list[int] = []
    volume: list[int] = []
    # Todo item que chega aqui passou pela peneira, então cabe no peso útil: abrir caixa
    # nova é sempre seguro.
    util = box.usable_grams
    for item in restantes:
        for indice in range(len(caixas)):
            cabe_peso = peso[indice] + item.weight_grams <= util
            cabe_volume = volume[indice] + item.volume_mm3 <= box.volume_mm3
            if cabe_peso and cabe_volume:
                caixas[indice].append(item)
                peso[indice] += item.weight_grams
                volume[indice] += item.volume_mm3
                break
        else:
            caixas.append([item])
            peso.append(item.weight_grams)
            volume.append(item.volume_mm3)
    return [
        Parcel(
            weight_grams=peso[i] + box.empty_weight_grams,
            width_mm=box.width_mm,
            height_mm=box.height_mm,
            depth_mm=box.depth_mm,
            value_cents=sum(item.value_cents for item in caixas[i]),
        )
        for i in range(len(caixas))
    ]


def item_from_variant(
    *,
    weight_grams: int | None,
    width_mm: int | None,
    height_mm: int | None,
    depth_mm: int | None,
    value_cents: int,
    quantity: int,
) -> list[PackItem]:
    """Uma variante do catálogo vira N itens iguais. Sem medida completa, recusa.

    Recusar é melhor do que assumir: a cotação com medida inventada só aparece como prejuízo
    no dia do despacho, quando a transportadora cobra pelo volume real.
    """
    if not weight_grams or not width_mm or not height_mm or not depth_mm:
        raise MissingDimensions("variante sem peso ou dimensões")
    unidades = max(1, math.ceil(quantity))
    # Divide o valor declarado sem sobra: a ultima unidade leva o resto, senao a soma dos
    # volumes nao bate com o valor da linha.
    base, resto = divmod(max(value_cents, 0), unidades)
    return [
        PackItem(
            weight_grams=weight_grams,
            width_mm=width_mm,
            height_mm=height_mm,
            depth_mm=depth_mm,
            value_cents=base + (1 if indice < resto else 0),
        )
        for indice in range(unidades)
    ]
