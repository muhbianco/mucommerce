"""Customer addresses (stage E, S3): owned by the customer, per store, behind the checkout gate."""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.identity.models import AccessStatus
from tests.shoppers import as_shopper, selling_store, signed_in

URL = "/api/v1/me/addresses"


def address(**overrides: Any) -> dict[str, Any]:
    return {
        "recipient_name": "Maria Silva",
        "phone": "(11) 98765-4321",
        "postal_code": "01310-100",
        "street": "Avenida Paulista",
        "number": "1000",
        "district": "Bela Vista",
        "city": "São Paulo",
        "state": "sp",
    } | overrides


async def test_crud_default_and_normalization(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    store = await selling_store(session_factory)
    me = as_shopper(store, await signed_in(session_factory, store))

    first = await client.post(URL, json=address(), headers=me)
    assert first.status_code == 201, first.text
    body = first.json()
    assert (body["postal_code"], body["state"], body["phone"]) == (
        "01310100",
        "SP",
        "5511987654321",
    )
    assert body["is_default"] is True  # the first one

    second = (await client.post(URL, json=address(label="Trabalho"), headers=me)).json()
    assert second["is_default"] is False
    promoted = await client.patch(f"{URL}/{second['id']}", json={"is_default": True}, headers=me)
    assert promoted.json()["is_default"] is True
    listed = (await client.get(URL, headers=me)).json()
    assert [a["id"] for a in listed] == [second["id"], body["id"]]
    assert [a["is_default"] for a in listed] == [True, False]

    nulled = await client.patch(f"{URL}/{body['id']}", json={"street": None}, headers=me)
    assert nulled.status_code == 422
    for bad in (address(postal_code="123"), address(state="XX"), address(phone="123")):
        assert (await client.post(URL, json=bad, headers=me)).status_code == 422

    assert (await client.delete(f"{URL}/{second['id']}", headers=me)).status_code == 204
    [left] = (await client.get(URL, headers=me)).json()
    assert (left["id"], left["is_default"]) == (body["id"], True)  # default moved over


async def test_another_customer_or_store_never_sees_the_address(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    alpha = await selling_store(session_factory, "alpha")
    beta = await selling_store(session_factory, "beta")
    mine = as_shopper(alpha, await signed_in(session_factory, alpha))
    other = as_shopper(alpha, await signed_in(session_factory, alpha))
    created = (await client.post(URL, json=address(), headers=mine)).json()

    assert (await client.get(URL, headers=other)).json() == []
    for method in ("patch", "delete"):
        response = await client.request(
            method,
            f"{URL}/{created['id']}",
            json={"city": "X"} if method == "patch" else None,
            headers=other,
        )
        assert response.status_code == 404, method
    # The same customer's session does not open another store (sessions are per store).
    beta_headers = as_shopper(beta, mine["X-Customer-Session"])
    assert (await client.get(URL, headers=beta_headers)).status_code == 401


async def test_the_checkout_gate(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    closed = await selling_store(session_factory, "fechada", mode="whitelist")
    pending = as_shopper(
        closed, await signed_in(session_factory, closed, access=AccessStatus.PENDING)
    )
    assert (await client.get(URL, headers=pending)).json()["error"]["code"] == "access_pending"

    public = await selling_store(session_factory, "aberta")
    blocked = as_shopper(
        public, await signed_in(session_factory, public, access=AccessStatus.BLOCKED)
    )
    assert (await client.get(URL, headers=blocked)).json()["error"]["code"] == "access_blocked"
    anonymous = as_shopper(public, None)
    assert (await client.get(URL, headers=anonymous)).status_code == 401

    off = await selling_store(session_factory, "semcheckout", flags={"checkout": False})
    me = as_shopper(off, await signed_in(session_factory, off))
    assert (await client.get(URL, headers=me)).status_code == 404


async def test_browser_writes_need_the_store_origin(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    store = await selling_store(session_factory)
    token = await signed_in(session_factory, store)
    # A browser call (cookie, no web token) from another origin is refused.
    client.cookies.set("__Host-mb_sess", token)
    forged = await client.post(
        URL,
        json=address(),
        headers={"host": "alpha.loja.test", "origin": "https://evil.test"},
    )
    assert forged.status_code == 403 and forged.json()["error"]["code"] == "csrf_origin"
    client.cookies.clear()
