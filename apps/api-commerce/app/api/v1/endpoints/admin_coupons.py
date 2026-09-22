"""Panel: the store's coupons (module `coupons`, scope `settings:write`).

The code is fixed once created — it is already printed or shared — and everything else can be
adjusted. `redemptions_count` is never edited by hand: it moves only when an order uses or
releases the coupon, under the coupon's row lock.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, status
from pydantic import BaseModel, Field, model_validator

from app.api.deps import CurrentAdmin, DbSession, admin_actor, require_tenant_scopes
from app.core.exceptions import ValidationError
from app.core.pagination import Page, decode_cursor, encode_cursor
from app.core.scopes import Scope
from app.coupons.models import Coupon, CouponKind, CouponStatus
from app.coupons.service import CouponService
from app.schemas.common import StrictModel
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/admin/tenants/{tenant_id}/coupons", tags=["Painel — Cupons"])

CouponWriter = Annotated[
    TenantContext, Depends(require_tenant_scopes(Scope.SETTINGS_WRITE, features=("coupons",)))
]
CouponId = Annotated[str, Path(min_length=36, max_length=36)]
Code = Annotated[str, Field(min_length=3, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")]


class CouponBase(StrictModel):
    kind: CouponKind = CouponKind.PERCENT
    percent_bps: Annotated[int, Field(ge=1, le=10000)] | None = None
    amount_cents: Annotated[int, Field(ge=1, le=100_000_000)] | None = None
    min_subtotal_cents: Annotated[int, Field(ge=0, le=100_000_000)] = 0
    max_discount_cents: Annotated[int, Field(ge=1, le=100_000_000)] | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    max_redemptions: Annotated[int, Field(ge=1, le=1_000_000)] | None = None
    per_customer_limit: Annotated[int, Field(ge=1, le=1000)] | None = None
    status: CouponStatus = CouponStatus.ACTIVE
    note: Annotated[str, Field(max_length=200)] | None = None

    @model_validator(mode="after")
    def _one_kind_of_discount(self) -> CouponBase:
        if self.kind == CouponKind.PERCENT and not self.percent_bps:
            raise ValueError("percent_bps é obrigatório para cupom de porcentagem")
        if self.kind == CouponKind.FIXED and not self.amount_cents:
            raise ValueError("amount_cents é obrigatório para cupom de valor fixo")
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValueError("a validade termina antes de começar")
        return self


class CouponCreate(CouponBase):
    code: Code


class CouponUpdate(StrictModel):
    """Only what is sent changes; the code stays as it was created."""

    percent_bps: Annotated[int, Field(ge=1, le=10000)] | None = None
    amount_cents: Annotated[int, Field(ge=1, le=100_000_000)] | None = None
    min_subtotal_cents: Annotated[int, Field(ge=0, le=100_000_000)] | None = None
    max_discount_cents: Annotated[int, Field(ge=1, le=100_000_000)] | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    max_redemptions: Annotated[int, Field(ge=1, le=1_000_000)] | None = None
    per_customer_limit: Annotated[int, Field(ge=1, le=1000)] | None = None
    status: CouponStatus | None = None
    note: Annotated[str, Field(max_length=200)] | None = None


class CouponRead(BaseModel):
    id: str
    code: str
    kind: str
    percent_bps: int | None
    amount_cents: int | None
    min_subtotal_cents: int
    max_discount_cents: int | None
    starts_at: datetime | None
    ends_at: datetime | None
    max_redemptions: int | None
    per_customer_limit: int | None
    redemptions_count: int
    status: str
    note: str | None


def coupon_read(coupon: Coupon) -> CouponRead:
    return CouponRead(
        id=coupon.id,
        code=coupon.code,
        kind=coupon.kind,
        percent_bps=coupon.percent_bps,
        amount_cents=coupon.amount_cents,
        min_subtotal_cents=coupon.min_subtotal_cents,
        max_discount_cents=coupon.max_discount_cents,
        starts_at=coupon.starts_at,
        ends_at=coupon.ends_at,
        max_redemptions=coupon.max_redemptions,
        per_customer_limit=coupon.per_customer_limit,
        redemptions_count=coupon.redemptions_count,
        status=coupon.status,
        note=coupon.note,
    )


@router.get("", response_model=Page[CouponRead], summary="Cupons da loja")
async def list_coupons(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: CouponWriter,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: Annotated[str | None, Query(max_length=256)] = None,
) -> Page[CouponRead]:
    before = decode_cursor(cursor, "id")["id"] if cursor else None
    service = CouponService(session, tenant, admin_actor(request, user))
    rows = await service.listing(limit=limit, before_id=before)
    page = rows[:limit]
    return Page[CouponRead](
        items=[coupon_read(c) for c in page],
        next_cursor=encode_cursor(id=page[-1].id) if len(rows) > limit else None,
    )


@router.post(
    "", response_model=CouponRead, status_code=status.HTTP_201_CREATED, summary="Cria um cupom"
)
async def create_coupon(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: CouponWriter,
    body: CouponCreate,
) -> CouponRead:
    service = CouponService(session, tenant, admin_actor(request, user))
    changes = body.model_dump(exclude={"code"})
    return coupon_read(await service.create(body.code, changes))


@router.patch("/{coupon_id}", response_model=CouponRead, summary="Ajusta um cupom")
async def update_coupon(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: CouponWriter,
    coupon_id: CouponId,
    body: CouponUpdate,
) -> CouponRead:
    service = CouponService(session, tenant, admin_actor(request, user))
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise ValidationError("Nada para alterar.")
    coupon = await service.get(coupon_id)
    kind = coupon.kind
    percent = changes.get("percent_bps", coupon.percent_bps)
    amount = changes.get("amount_cents", coupon.amount_cents)
    if (kind == CouponKind.PERCENT and not percent) or (kind == CouponKind.FIXED and not amount):
        raise ValidationError("O desconto do cupom não pode ficar vazio.")
    return coupon_read(await service.update(coupon_id, changes))
