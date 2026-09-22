"""The signed-in customer's delivery addresses in this store (checkout picks one)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Response, status

from app.api.deps import CheckoutShopper, DbSession, customer_rate_key, require_same_origin
from app.core.rate_limit import rate_limit
from app.customers.addresses import (
    AddressCreate,
    AddressRead,
    AddressService,
    AddressUpdate,
    address_read,
)

router = APIRouter(tags=["Clientes"])

AddressId = Annotated[str, Path(min_length=36, max_length=36)]
_WRITE = [
    Depends(require_same_origin),
    Depends(rate_limit("customer_addresses", 30, 60, key_fn=customer_rate_key)),
]


@router.get("/me/addresses", response_model=list[AddressRead], summary="Meus endereços")
async def list_addresses(session: DbSession, shopper: CheckoutShopper) -> list[AddressRead]:
    addresses = await AddressService(session).list(shopper.viewer.customer_id)
    return [address_read(a) for a in addresses]


@router.post(
    "/me/addresses",
    response_model=AddressRead,
    status_code=status.HTTP_201_CREATED,
    summary="Novo endereço (o primeiro vira o padrão)",
    dependencies=_WRITE,
)
async def create_address(
    session: DbSession, shopper: CheckoutShopper, body: AddressCreate
) -> AddressRead:
    address = await AddressService(session).create(shopper.viewer.customer_id, body)
    return address_read(address)


@router.patch(
    "/me/addresses/{address_id}",
    response_model=AddressRead,
    summary="Altera um endereço",
    dependencies=_WRITE,
)
async def update_address(
    session: DbSession, shopper: CheckoutShopper, address_id: AddressId, body: AddressUpdate
) -> AddressRead:
    address = await AddressService(session).update(shopper.viewer.customer_id, address_id, body)
    return address_read(address)


@router.delete(
    "/me/addresses/{address_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Apaga um endereço (pedidos antigos guardam a cópia)",
    dependencies=_WRITE,
)
async def delete_address(
    session: DbSession, shopper: CheckoutShopper, address_id: AddressId
) -> Response:
    await AddressService(session).delete(shopper.viewer.customer_id, address_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
