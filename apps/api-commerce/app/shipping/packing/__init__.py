"""Empacotamento: das linhas do carrinho para volumes (puro, sem I/O).

`legacy` é o motor v1 (caixa padrão + enchimento por volume), que continua valendo para toda loja
sem a flag `shipping.packing_v2`. O v2 entra aos poucos nos módulos ao lado (ver
docs/13-frete-v2.md); os nomes do v1 seguem exportados daqui para não mexer em quem já importa.
"""

from app.shipping.packing.legacy import (
    DEFAULT_BOX,
    DEFAULT_BOX_MAX_GRAMS,
    DEFAULT_BOX_MM,
    Box,
    MissingDimensions,
    PackItem,
    item_from_variant,
    pack,
)

__all__ = [
    "DEFAULT_BOX",
    "DEFAULT_BOX_MAX_GRAMS",
    "DEFAULT_BOX_MM",
    "Box",
    "MissingDimensions",
    "PackItem",
    "item_from_variant",
    "pack",
]
