"""Modifier groups (stage D): stable ids, limits, and the pure price of a choice."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.catalog.pricing import EffectivePrice, price_with_modifiers
from app.core.exceptions import ValidationError
from app.tenancy.models import Tenant
from tests.test_catalog import base, create_product
from tests.test_storefront_catalog import published, store  # noqa: F401 (fixture)

CATALOG = "/api/v1/storefront/catalog"
GROUPS: list[dict[str, Any]] = [
    {
        "id": "g-cobertura",
        "name": "Cobertura",
        "min_select": 1,
        "max_select": 1,
        "modifiers": [
            {"id": "m-choc", "name": "Chocolate", "price_cents": 300, "active": True},
            {"id": "m-nada", "name": "Sem cobertura", "price_cents": 0, "active": True},
            {"id": "m-old", "name": "Caramelo", "price_cents": 400, "active": False},
        ],
    },
    {
        "id": "g-extras",
        "name": "Extras",
        "min_select": 0,
        "max_select": 2,
        "modifiers": [
            {"id": "m-vela", "name": "Vela", "price_cents": 150, "active": True},
            {"id": "m-cartao", "name": "Cartão", "price_cents": 500, "active": True},
            {"id": "m-laco", "name": "Laço", "price_cents": 200, "active": True},
        ],
    },
]
BASE = EffectivePrice(2000, None, False, None)


# ----------------------------------------------------------------------------- pure pricing
def test_modifiers_add_to_the_unit_price_in_the_products_order() -> None:
    priced = price_with_modifiers(BASE, GROUPS, ["m-cartao", "m-choc", "m-vela"])
    assert priced.unit_cents == 2000 + 300 + 500 + 150
    assert [m.modifier_id for m in priced.modifiers] == ["m-choc", "m-vela", "m-cartao"]
    assert price_with_modifiers(BASE, None, []).unit_cents == 2000


@pytest.mark.parametrize(
    ("chosen", "code"),
    [
        ([], "modifier_group_limit"),  # Cobertura is required
        (["m-choc", "m-nada"], "modifier_group_limit"),  # at most one
        (["m-choc", "m-vela", "m-cartao", "m-laco"], "modifier_group_limit"),
        (["m-choc", "m-old"], "modifier_unavailable"),  # inactive
        (["m-choc", "m-nope"], "modifier_unavailable"),
        (["m-choc", "m-choc"], "modifier_repeated"),
    ],
)
def test_an_invalid_choice_is_refused(chosen: list[str], code: str) -> None:
    with pytest.raises(ValidationError) as caught:
        price_with_modifiers(BASE, GROUPS, chosen)
    assert caught.value.details["code"] == code


# ----------------------------------------------------------------------------- panel API
def group(name: str, *items: tuple[str, int], **limits: int) -> dict[str, Any]:
    return {
        "name": name,
        **limits,
        "modifiers": [{"name": n, "price_cents": cents} for n, cents in items],
    }


async def test_ids_survive_edits_by_name_and_limits_are_checked(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    store: tuple[Tenant, dict[str, str], dict[str, str]],  # noqa: F811
) -> None:
    tenant, headers, _ = store
    product = await create_product(client, tenant, headers)
    url = f"{base(tenant)}/products/{product['id']}/modifiers"

    first = await client.put(
        url,
        json={"groups": [group("Cobertura", ("Chocolate", 300), ("Doce de leite", 350))]},
        headers=headers,
    )
    assert first.status_code == 200, first.text
    created = first.json()["modifier_groups"][0]
    assert (created["min_select"], created["max_select"]) == (0, 1)

    edited = await client.put(
        url,
        json={
            "groups": [
                group("cobertura", ("chocolate", 400), ("Morango", 300), max_select=2),
            ]
        },
        headers=headers,
    )
    after = edited.json()["modifier_groups"][0]
    assert after["id"] == created["id"]
    assert after["modifiers"][0]["id"] == created["modifiers"][0]["id"]  # same name, same id
    assert after["modifiers"][0]["price_cents"] == 400
    assert after["modifiers"][1]["id"] not in {m["id"] for m in created["modifiers"]}

    for bad in (
        [group("A", ("x", 1)), group("a", ("y", 1))],
        [group("A", ("x", 1), ("X", 2))],
        [group("A", ("x", 1), min_select=2, max_select=2)],
        [group("A", ("x", 1), max_select=3)],
        [{**group("A", ("x", 1)), "id": "0" * 36}],
    ):
        assert (await client.put(url, json={"groups": bad}, headers=headers)).status_code == 422

    cleared = await client.put(url, json={"groups": []}, headers=headers)
    assert cleared.json()["modifier_groups"] == []


async def test_storefront_lists_only_active_modifiers(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    store: tuple[Tenant, dict[str, str], dict[str, str]],  # noqa: F811
) -> None:
    tenant, headers, shopper = store
    product = await published(client, session_factory, tenant, headers)
    await client.put(
        f"{base(tenant)}/products/{product['id']}/modifiers",
        json={
            "groups": [
                {
                    "name": "Extras",
                    "max_select": 2,
                    "modifiers": [
                        {"name": "Vela", "price_cents": 150},
                        {"name": "Caramelo", "price_cents": 400, "active": False},
                    ],
                }
            ]
        },
        headers=headers,
    )
    detail = (await client.get(f"{CATALOG}/products/{product['slug']}", headers=shopper)).json()
    [extras] = detail["modifier_groups"]
    assert [(m["name"], m["price_cents"]) for m in extras["modifiers"]] == [("Vela", 150)]
    assert (extras["min_select"], extras["max_select"]) == (0, 2)
