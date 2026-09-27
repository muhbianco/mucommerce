"""Store provisioning called by the MuhBianco catalog (api-agents) over `chatbot-net`.

Not public and not for the panel: the caller proves itself with the `agents` internal token and
speaks only in terms of a subscription. See `app.provisioning.service` for why a purchase is
reserve → activate (or release).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, status

from app.api.deps import DbSession, require_internal
from app.api.v1.endpoints.internal import _panel_host
from app.core.exceptions import NotFoundError
from app.provisioning.service import StoreProvisioningService
from app.schemas.internal import PanelHostRead
from app.schemas.provisioning import (
    ChatwootDomainsRead,
    ChatwootEnable,
    StoreBillingState,
    StoreRead,
    StoreReserve,
)

router = APIRouter(
    prefix="/internal/provisioning/stores",
    tags=["Interno"],
    dependencies=[Depends(require_internal("agents"))],
)

Ref = Annotated[str, Path(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_.:-]+$")]


@router.get(
    "/panel-hosts/{host}",
    response_model=PanelHostRead,
    summary="Este host é o painel de alguma loja? (volta do login MuhBianco)",
)
async def panel_host(
    session: DbSession, host: Annotated[str, Path(min_length=3, max_length=253)]
) -> PanelHostRead:
    return await _panel_host(session, host)


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


@router.post(
    "/{subscription_ref}/chatwoot",
    response_model=ChatwootDomainsRead,
    summary="Liga os endereços do painel do Chatwoot da loja (idempotente)",
)
async def enable_chatwoot(
    session: DbSession, subscription_ref: Ref, body: ChatwootEnable
) -> ChatwootDomainsRead:
    tenant = await StoreProvisioningService(session).enable_chatwoot(
        subscription_ref, body.custom_domain
    )
    return ChatwootDomainsRead.of(tenant)


@router.delete(
    "/{subscription_ref}/chatwoot",
    response_model=ChatwootDomainsRead,
    summary="Add-on cancelado: tira os endereços do Chatwoot do ar",
)
async def disable_chatwoot(session: DbSession, subscription_ref: Ref) -> ChatwootDomainsRead:
    tenant = await StoreProvisioningService(session).disable_chatwoot(subscription_ref)
    return ChatwootDomainsRead.of(tenant)


@router.get(
    "/{subscription_ref}/chatwoot",
    response_model=ChatwootDomainsRead,
    summary="Endereços do Chatwoot da loja e o estado do DNS",
)
async def chatwoot_state(session: DbSession, subscription_ref: Ref) -> ChatwootDomainsRead:
    tenant = await StoreProvisioningService(session).by_subscription(subscription_ref)
    if tenant is None:
        raise NotFoundError("Nenhuma loja para esta assinatura.")
    await session.refresh(tenant, ["domains"])
    return ChatwootDomainsRead.of(tenant)


@router.get(
    "/{subscription_ref}",
    response_model=StoreRead | None,
    summary="Estado da loja desta assinatura",
)
async def store_state(session: DbSession, subscription_ref: Ref) -> StoreRead | None:
    tenant = await StoreProvisioningService(session).by_subscription(subscription_ref)
    return StoreRead.of(tenant) if tenant else None
