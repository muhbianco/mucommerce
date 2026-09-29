"""Normalization that runs before a setting is validated and stored.

Settings with lists the panel edits (pickup locations, delivery zones) keep stable ids: an
entry sent with an id keeps it (it must exist); one sent without takes the id of the entry with
the same name (ignoring case) in the stored value, else a new one. Orders snapshot these ids,
so renaming a zone does not lose it.
"""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from typing import Any

from app.core.exceptions import ValidationError
from app.core.ids import new_id

Normalizer = Callable[[dict[str, Any] | None, dict[str, Any]], dict[str, Any]]


def _keep_ids(previous: list[Any], incoming: list[Any], label: str) -> list[Any]:
    old = [p for p in previous if isinstance(p, dict)]
    by_id = {p.get("id"): p for p in old if p.get("id")}
    by_name = {str(p.get("name", "")).casefold(): p for p in old}
    result = []
    for item in incoming:
        if not isinstance(item, dict):
            result.append(item)  # the schema rejects it with a proper message
            continue
        item = dict(item)
        given = item.get("id")
        if given is not None and given not in by_id:
            raise ValidationError(f"{label} desconhecido.", fields=["id"], id=given)
        if given is None:
            match = by_name.get(str(item.get("name", "")).casefold())
            item["id"] = match["id"] if match and match.get("id") else new_id()
        result.append(item)
    return result


def normalize_fulfillment(previous: dict[str, Any] | None, value: dict[str, Any]) -> dict[str, Any]:
    value = deepcopy(value)
    previous = previous or {}
    for section, key, label in (
        ("pickup", "locations", "Local de retirada"),
        ("delivery", "zones", "Zona de entrega"),
        # O produto aponta para a caixa pelo id, então ele precisa sobreviver a uma edição que
        # mexa em qualquer outra caixa da lista — é a mesma razão dos locais e das zonas.
        ("shipping", "boxes", "Embalagem"),
    ):
        incoming = (value.get(section) or {}).get(key)
        if isinstance(incoming, list):
            stored = (previous.get(section) or {}).get(key) or []
            value[section][key] = _keep_ids(stored, incoming, label)
    return value


def normalize_landing(previous: dict[str, Any] | None, value: dict[str, Any]) -> dict[str, Any]:
    """Dá id a bloco novo e deixa o id de bloco que já existia como está.

    Sem isso o editor identificaria bloco por posição na lista, e mover o terceiro para cima
    com outra aba aberta corromperia a página — a segunda aba gravaria "o terceiro" querendo
    dizer outro bloco. Aqui, subir, duplicar e remover falam de um id.

    Id desconhecido é recusado, e não silenciosamente trocado: um id que a loja nunca teve veio
    de formulário adulterado ou de página velha, e os dois merecem erro em vez de um bloco
    fantasma.
    """
    value = deepcopy(value)
    blocks = value.get("blocks")
    if not isinstance(blocks, list):
        return value
    stored = [b for b in ((previous or {}).get("blocks") or []) if isinstance(b, dict)]
    known = {b["id"] for b in stored if b.get("id")}
    seen: set[str] = set()
    result: list[Any] = []
    for block in blocks:
        if not isinstance(block, dict):
            result.append(block)  # o esquema recusa com mensagem decente
            continue
        block = dict(block)
        given = block.get("id")
        if given is not None:
            if given not in known:
                raise ValidationError("Bloco desconhecido.", fields=["id"], id=given)
            # Duplicar manda o mesmo id duas vezes; a cópia ganha o dela.
            block["id"] = new_id() if given in seen else given
            seen.add(given)
        else:
            block["id"] = new_id()
        result.append(block)
    value["blocks"] = result
    return value


SETTING_NORMALIZERS: dict[str, Normalizer] = {
    "fulfillment": normalize_fulfillment,
    "landing": normalize_landing,
}
