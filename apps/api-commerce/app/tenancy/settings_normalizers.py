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
    ):
        incoming = (value.get(section) or {}).get(key)
        if isinstance(incoming, list):
            stored = (previous.get(section) or {}).get(key) or []
            value[section][key] = _keep_ids(stored, incoming, label)
    return value


SETTING_NORMALIZERS: dict[str, Normalizer] = {"fulfillment": normalize_fulfillment}
