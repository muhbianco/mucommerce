"""Store provisioning called by the MuhBianco catalog (api-agents) over `chatbot-net`.

Not public and not for the panel: the caller proves itself with the `agents` internal token and
speaks only in terms of a subscription. See `app.provisioning.service` for why a purchase is
reserve → activate (or release).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, status

from app.api.deps import DbSession, require_internal
from app.provisioning.service import StoreProvisioningService
from app.schemas.provisioning import StoreBillingState, StoreRead, StoreReserve

router = APIRouter(
    prefix="/internal/provisioning/stores",
    tags=["Interno"],
    dependencies=[Depends(require_internal("agents"))],
)

Ref = Annotated[str, Path(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_.:-]+$")]


@router.post(
    "/reserve",
    response_model=StoreRead,
    status_code=status.HTTP_201_CREATED,
    summary="Reserva o endereço da loja antes da cobrança (idempotente por assinatura)",
)
async def reserve_store(session: DbSession, body: StoreReserve) -> StoreRead:
    tenant = await StoreProvisioningService(session).reserve(
        subscription_ref=body.subscription_ref,
        account_id=body.account_id,
        email=str(body.email),
        full_name=body.full_name,
        slug=body.slug,
        name=body.name,
        custom_domain=body.custom_domain,
    )
    return StoreRead.of(tenant)


@router.post(
    "/{subscription_ref}/activate",
    response_model=StoreRead,
    summary="Cobrança confirmada: a loja entra no ar",
)
async def activate_store(session: DbSession, subscription_ref: Ref) -> StoreRead:
    return StoreRead.of(await StoreProvisioningService(session).activate(subscription_ref))


@router.post(
    "/{subscription_ref}/billing",
    response_model=StoreRead,
    summary="Assinatura suspensa (com prazo de carência) ou de volta ao ar",
)
async def set_billing_state(
    session: DbSession, subscription_ref: Ref, body: StoreBillingState
) -> StoreRead:
    tenant = await StoreProvisioningService(session).set_billing_state(
        subscription_ref=subscription_ref,
        state=body.state,
        grace_until=body.grace_until,
        reason=body.reason,
    )
    return StoreRead.of(tenant)


@router.delete(
    "/{subscription_ref}",
    response_model=StoreRead | None,
    summary="Cobrança não passou: libera o endereço reservado",
)
async def release_store(session: DbSession, subscription_ref: Ref) -> StoreRead | None:
    tenant = await StoreProvisioningService(session).release(subscription_ref)
    return StoreRead.of(tenant) if tenant else None


@router.get(
    "/{subscription_ref}",
    response_model=StoreRead | None,
    summary="Estado da loja desta assinatura",
)
async def store_state(session: DbSession, subscription_ref: Ref) -> StoreRead | None:
    tenant = await StoreProvisioningService(session).by_subscription(subscription_ref)
    return StoreRead.of(tenant) if tenant else None
