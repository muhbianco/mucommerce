"""A customer's delivery addresses in one store.

Every query is scoped by the ORM tenant filter AND by `customer_id`: an address of another
customer (or of this customer in another store) is simply not found (404, never 403).
"""

from __future__ import annotations

import re
from typing import Annotated, Any

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.phone import normalize_br_phone
from app.customers.address_models import MAX_ADDRESSES, CustomerAddress
from app.schemas.common import StrictModel

UF = frozenset(
    [
        "AC",
        "AL",
        "AP",
        "AM",
        "BA",
        "CE",
        "DF",
        "ES",
        "GO",
        "MA",
        "MT",
        "MS",
        "MG",
        "PA",
        "PB",
        "PR",
        "PE",
        "PI",
        "RJ",
        "RN",
        "RS",
        "RO",
        "RR",
        "SC",
        "SP",
        "SE",
        "TO",
    ]
)
_NON_DIGIT = re.compile(r"\D")

Short = Annotated[str, Field(min_length=1, max_length=80)]


class _AddressFields(StrictModel):
    @field_validator("postal_code", check_fields=False)
    @classmethod
    def _cep(cls, value: str | None) -> str | None:
        if value is None:
            return None
        digits = _NON_DIGIT.sub("", value)
        if len(digits) != 8:
            raise ValueError("CEP deve ter 8 dígitos")
        return digits

    @field_validator("state", check_fields=False)
    @classmethod
    def _uf(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value.upper() not in UF:
            raise ValueError("UF inválida")
        return value.upper()

    @field_validator("phone", check_fields=False)
    @classmethod
    def _phone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        phone = normalize_br_phone(value)
        if phone is None:
            raise ValueError("telefone inválido")
        return phone


class AddressCreate(_AddressFields):
    label: Annotated[str, Field(max_length=40)] | None = None
    recipient_name: Annotated[str, Field(min_length=1, max_length=120)]
    phone: str | None = None
    postal_code: str
    street: Annotated[str, Field(min_length=1, max_length=160)]
    number: Annotated[str, Field(min_length=1, max_length=20)]
    complement: Short | None = None
    district: Short
    city: Short
    state: str
    reference: Annotated[str, Field(max_length=160)] | None = None
    is_default: bool = False


class AddressUpdate(_AddressFields):
    """Partial: only the fields present change; `null` clears the optional ones."""

    label: Annotated[str, Field(max_length=40)] | None = None
    recipient_name: Annotated[str, Field(min_length=1, max_length=120)] | None = None
    phone: str | None = None
    postal_code: str | None = None
    street: Annotated[str, Field(min_length=1, max_length=160)] | None = None
    number: Annotated[str, Field(min_length=1, max_length=20)] | None = None
    complement: Short | None = None
    district: Short | None = None
    city: Short | None = None
    state: str | None = None
    reference: Annotated[str, Field(max_length=160)] | None = None
    is_default: bool | None = None


class AddressRead(BaseModel):
    id: str
    label: str | None
    recipient_name: str
    phone: str | None
    postal_code: str
    street: str
    number: str
    complement: str | None
    district: str
    city: str
    state: str
    reference: str | None
    is_default: bool


_REQUIRED = frozenset(
    {"recipient_name", "postal_code", "street", "number", "district", "city", "state"}
)


def address_read(address: CustomerAddress) -> AddressRead:
    return AddressRead(
        id=address.id,
        label=address.label,
        recipient_name=address.recipient_name,
        phone=address.phone_e164,
        postal_code=address.postal_code,
        street=address.street,
        number=address.number,
        complement=address.complement,
        district=address.district,
        city=address.city,
        state=address.state,
        reference=address.reference,
        is_default=address.is_default,
    )


def address_snapshot(address: CustomerAddress) -> dict[str, Any]:
    """What an order keeps of the address (the customer may edit or delete it later)."""
    return address_read(address).model_dump(exclude={"id", "is_default", "label"})


class AddressService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list(self, customer_id: str) -> list[CustomerAddress]:
        stmt = (
            select(CustomerAddress)
            .where(CustomerAddress.customer_id == customer_id)
            .order_by(CustomerAddress.is_default.desc(), CustomerAddress.created_at)
            .limit(MAX_ADDRESSES)
        )
        return list((await self.session.execute(stmt)).scalars())

    async def get_owned(self, customer_id: str, address_id: str) -> CustomerAddress:
        stmt = (
            select(CustomerAddress)
            .where(CustomerAddress.id == address_id)
            .where(CustomerAddress.customer_id == customer_id)
        )
        address = (await self.session.execute(stmt)).scalar_one_or_none()
        if address is None:
            raise NotFoundError("Endereço não encontrado.")
        return address

    async def create(self, customer_id: str, data: AddressCreate) -> CustomerAddress:
        count = await self.session.scalar(
            select(func.count())
            .select_from(CustomerAddress)
            .where(CustomerAddress.customer_id == customer_id)
        )
        if int(count or 0) >= MAX_ADDRESSES:
            raise ConflictError("Limite de endereços atingido.", limit=MAX_ADDRESSES)
        values = data.model_dump(exclude={"phone", "is_default"})
        address = CustomerAddress(
            customer_id=customer_id,
            phone_e164=data.phone,
            is_default=data.is_default or not count,  # the first one is the default
            **values,
        )
        if address.is_default:
            await self._clear_default(customer_id)
        self.session.add(address)
        await self.session.flush()
        return address

    async def update(
        self, customer_id: str, address_id: str, data: AddressUpdate
    ) -> CustomerAddress:
        address = await self.get_owned(customer_id, address_id)
        changes = data.model_dump(exclude_unset=True)
        nulled = sorted(k for k, v in changes.items() if v is None and k in _REQUIRED)
        if nulled:
            raise ValidationError("Campos obrigatórios não podem ser nulos.", fields=nulled)
        if changes.pop("is_default", None):
            await self._clear_default(customer_id)
            address.is_default = True
        if "phone" in changes:
            address.phone_e164 = changes.pop("phone")
        for name, value in changes.items():
            setattr(address, name, value)
        await self.session.flush()
        return address

    async def delete(self, customer_id: str, address_id: str) -> None:
        address = await self.get_owned(customer_id, address_id)
        was_default = address.is_default
        await self.session.delete(address)
        await self.session.flush()
        if was_default:
            remaining = await self.list(customer_id)
            if remaining:
                remaining[0].is_default = True
                await self.session.flush()

    async def _clear_default(self, customer_id: str) -> None:
        await self.session.execute(
            update(CustomerAddress)
            .where(CustomerAddress.customer_id == customer_id)
            .where(CustomerAddress.is_default.is_(True))
            .values(is_default=False)
            .execution_options(synchronize_session="fetch")
        )
