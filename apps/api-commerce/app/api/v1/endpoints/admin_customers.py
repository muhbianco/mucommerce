"""Store panel: customers who signed in to the store, and the store's access decisions."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, Request
from pydantic import BaseModel, Field

from app.api.deps import CurrentAdmin, DbSession, admin_actor, require_tenant_scopes
from app.core.exceptions import NotFoundError
from app.core.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Page, decode_cursor, encode_cursor
from app.core.scopes import Scope
from app.customers.access_service import CustomerAccessService
from app.customers.repository import AccessRepository
from app.identity.models import Customer, CustomerTenantAccess
from app.schemas.common import StrictModel
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/admin/tenants/{tenant_id}/customers", tags=["Painel — Clientes"])

CustomersReader = Annotated[TenantContext, Depends(require_tenant_scopes(Scope.CUSTOMERS_READ))]
CustomersApprover = Annotated[
    TenantContext, Depends(require_tenant_scopes(Scope.CUSTOMERS_APPROVE))
]
CustomerId = Annotated[str, Path(min_length=36, max_length=36)]
AccessFilter = Literal["pending", "approved", "blocked", "revoked"]


class CustomerAccessItem(BaseModel):
    customer_id: str
    name: str | None
    # Staff of the store see the full e-mail: they approve people they know.
    email: str | None
    phone_e164: str | None
    phone_verified: bool
    status: str
    source: str
    requested_at: datetime | None
    request_message: str | None
    status_changed_at: datetime | None
    note: str | None
    created_at: datetime


class AccessDecision(StrictModel):
    status: Literal["approved", "blocked", "revoked"]
    note: Annotated[str, Field(max_length=500)] | None = None


def item(access: CustomerTenantAccess, customer: Customer) -> CustomerAccessItem:
    return CustomerAccessItem(
        customer_id=customer.id,
        name=customer.full_name,
        email=customer.email_normalized,
        phone_e164=customer.phone_e164,
        phone_verified=customer.phone_verified_at is not None,
        status=access.status,
        source=access.source,
        requested_at=access.requested_at,
        request_message=access.request_message,
        status_changed_at=access.status_changed_at,
        note=access.note,
        created_at=access.created_at,
    )


@router.get(
    "",
    response_model=Page[CustomerAccessItem],
    summary="Clientes da loja com a situação de acesso (mais recentes primeiro)",
)
async def list_customers(
    session: DbSession,
    tenant: CustomersReader,
    status: Annotated[AccessFilter | None, Query()] = None,
    q: Annotated[str | None, Query(min_length=2, max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query(max_length=256)] = None,
) -> Page[CustomerAccessItem]:
    del tenant  # the session is bound to it by the guard
    before_id = decode_cursor(cursor, "id")["id"] if cursor else None
    rows = await AccessRepository(session).list_page(
        limit=limit, before_id=before_id, status=status, q=q
    )
    page = rows[:limit]
    next_cursor = encode_cursor(id=page[-1][0].id) if len(rows) > limit and page else None
    return Page(items=[item(a, c) for a, c in page], next_cursor=next_cursor)


@router.get("/{customer_id}", response_model=CustomerAccessItem, summary="Um cliente da loja")
async def get_customer(
    session: DbSession, tenant: CustomersReader, customer_id: CustomerId
) -> CustomerAccessItem:
    del tenant
    found = await AccessRepository(session).with_customer(customer_id)
    if found is None:
        raise NotFoundError("Cliente não encontrado nesta loja.")
    return item(*found)


@router.post(
    "/{customer_id}/access",
    response_model=CustomerAccessItem,
    summary="Aprova, bloqueia ou revoga o acesso do cliente ao catálogo",
)
async def decide_access(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: CustomersApprover,
    customer_id: CustomerId,
    body: AccessDecision,
) -> CustomerAccessItem:
    await CustomerAccessService(session).decide(
        tenant,
        customer_id,
        status=body.status,
        note=body.note,
        actor=admin_actor(request, user),
    )
    found = await AccessRepository(session).with_customer(customer_id)
    if found is None:  # the row was just updated in this transaction
        raise NotFoundError("Cliente não encontrado nesta loja.")
    return item(*found)
