"""O esquema dos blocos, como o modelo precisa vê-lo.

Sai de `LandingV1.model_json_schema()` — do mesmo Pydantic que valida a escrita. É a única
forma de o prompt não divergir do motor: se alguém acrescentar um bloco e o gerador continuar
descrevendo os antigos, o modelo nunca vai propor o bloco novo, e ninguém vai entender por quê.
Há um teste que cobra que todo membro da união esteja aqui.

O que se tira do esquema bruto:

- **`id`**, porque quem atribui somos nós. Deixar no esquema é convidar o modelo a inventar um,
  e depois recusá-lo por invenção.
- **títulos e descrições** que o Pydantic gera sozinho a partir dos nomes de classe
  (`HeroBlock`, `FeaturedProductsBlock`). São ruído em inglês no meio de um prompt em português
  e só gastam token.
"""

from __future__ import annotations

from typing import Any

from app.landing.blocks import MAX_BLOCKS
from app.tenancy.settings_schemas import LandingV1

#: Chaves que o Pydantic põe para documentação e que não ensinam nada ao modelo.
_NOISE = ("title", "description")


def _clean(node: Any) -> Any:
    """Tira o ruído e o campo `id` de todo lugar, sem mexer no resto da forma."""
    if isinstance(node, dict):
        return {
            key: _clean(value) for key, value in node.items() if key not in _NOISE and key != "id"
        }
    if isinstance(node, list):
        return [_clean(item) for item in node]
    return node


def landing_generation_schema() -> dict[str, Any]:
    """O esquema que viaja no pedido ao modelo."""
    bruto = LandingV1.model_json_schema()
    limpo: dict[str, Any] = _clean(bruto)
    # `id` some das propriedades, então também não pode continuar na lista de obrigatórios.
    for definicao in (limpo.get("$defs") or {}).values():
        obrigatorios = definicao.get("required")
        if isinstance(obrigatorios, list):
            definicao["required"] = [campo for campo in obrigatorios if campo != "id"]
    limpo["maxBlocks"] = MAX_BLOCKS
    return limpo


def schema_block_types() -> set[str]:
    """Os tipos de bloco que o esquema exportado realmente oferece.

    É o que o teste compara com a união do Pydantic: bloco que existe no motor e não chega aqui
    é um bloco que o modelo nunca vai propor.
    """
    schema = landing_generation_schema()
    tipos: set[str] = set()
    for definicao in (schema.get("$defs") or {}).values():
        propriedade = (definicao.get("properties") or {}).get("type") or {}
        valores = propriedade.get("enum")
        if valores is None and "const" in propriedade:
            valores = [propriedade["const"]]
        for valor in valores or []:
            tipos.add(str(valor))
    return tipos
