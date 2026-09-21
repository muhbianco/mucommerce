"""Store customers: Google sign-in (start/complete from the store's web, central callback) and
the customer's own session and access (`/me/*`).

The browser never calls start/complete: the store's web does it server-side with its internal
token, so the session cookie is set by the store host itself (`__Host-mb_sess`) and the flow is
bound to the browser that began it. `/me/*` accepts the cookie (browser → `/api` on the store
host) or the web's forwarded session; cookie POSTs must come from the store's own origin.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from app.api.deps import (
    ClientIp,
    CurrentCustomer,
    DbSession,
    StorefrontTenant,
    require_internal,
    require_same_origin,
)
from app.core.config import settings
from app.core.exceptions import InvalidLoginStateError, NotFoundError
from app.core.hosts import InvalidHostnameError, normalize_hostname
from app.core.phone import mask_phone
from app.core.rate_limit import rate_limit
from app.customers.access_service import CustomerAccessService
from app.customers.auth_service import CustomerAuthService, mask_email
from app.customers.phone import PhoneVerificationService
from app.customers.repository import CustomerSessionRepository
from app.identity.models import Customer
from app.models.base import utcnow
from app.schemas.common import StrictModel

router = APIRouter(tags=["Clientes"])

Binding = Annotated[str, Field(min_length=32, max_length=128)]


class StartBody(StrictModel):
    return_to: Annotated[str, Field(max_length=512)] = "/"
    binding: Binding
    # Versions of the store's terms/privacy shown next to "Entrar": recorded as consent.
    terms_version: Annotated[int, Field(ge=1, le=100_000)] | None = None
    privacy_version: Annotated[int, Field(ge=1, le=100_000)] | None = None


class StartResponse(BaseModel):
    authorize_url: str


class CompleteBody(StrictModel):
    hc: Annotated[str, Field(min_length=16, max_length=128)]
    binding: Binding
    previous_session: Annotated[str, Field(max_length=128)] | None = None


class CustomerRead(BaseModel):
    id: str
    name: str | None
    email_masked: str | None
    phone_verified: bool


class CompleteResponse(BaseModel):
    session_token: str
    expires_at: datetime
    return_to: str
    customer: CustomerRead
    access_status: str | None


class SessionRead(BaseModel):
    customer: CustomerRead
    access_status: str | None


class AccessRead(BaseModel):
    status: str  # approved | pending | blocked | revoked | none
    requested_at: datetime | None


class AccessRequestBody(StrictModel):
    message: Annotated[str, Field(max_length=500)] | None = None


class LogoutBody(StrictModel):
    all: bool = False


class PhoneStartBody(StrictModel):
    phone: Annotated[str, Field(min_length=8, max_length=32)]


class PhoneStartRead(BaseModel):
    whatsapp_url: str
    expires_at: datetime


class PhoneRead(BaseModel):
    phone_masked: str | None
    verified: bool


class PhoneConfirmBody(StrictModel):
    token: Annotated[str, Field(min_length=8, max_length=64)]
    phone: Annotated[str, Field(min_length=8, max_length=32)]


class PhoneConfirmRead(BaseModel):
    tenant_name: str


def customer_read(customer: Customer) -> CustomerRead:
    return CustomerRead(
        id=customer.id,
        name=customer.full_name,
        email_masked=mask_email(customer.email_normalized),
        phone_verified=customer.phone_verified_at is not None,
    )


# ------------------------------------------------------------------ sign-in (web → API)
@router.post(
    "/internal/customer-auth/google/start",
    response_model=StartResponse,
    summary="Começa o login Google de um cliente da loja (chamado pelo web da loja)",
    dependencies=[
        Depends(require_internal("web")),
        Depends(rate_limit("customer_login_start", limit=10, window_seconds=60)),
    ],
)
async def start_google(
    session: DbSession, tenant: StorefrontTenant, body: StartBody, ip: ClientIp
) -> StartResponse:
    url = await CustomerAuthService(session).start(
        tenant,
        return_to=body.return_to,
        binding=body.binding,
        ip=ip,
        terms_version=body.terms_version,
        privacy_version=body.privacy_version,
    )
    return StartResponse(authorize_url=url)


@router.post(
    "/internal/customer-auth/complete",
    response_model=CompleteResponse,
    summary="Troca o código de retorno pela sessão do cliente (chamado pelo web da loja)",
    dependencies=[
        Depends(require_internal("web")),
        Depends(rate_limit("customer_login_complete", limit=30, window_seconds=60)),
    ],
)
async def complete_google(
    session: DbSession,
    tenant: StorefrontTenant,
    body: CompleteBody,
    ip: ClientIp,
    user_agent: Annotated[str | None, Header()] = None,
) -> CompleteResponse:
    done = await CustomerAuthService(session).complete(
        tenant,
        handoff=body.hc,
        binding=body.binding,
        previous_session=body.previous_session,
        ip=ip,
        user_agent=user_agent,
    )
    return CompleteResponse(
        session_token=done.session_token,
        expires_at=done.expires_at,
        return_to=done.return_to,
        customer=customer_read(done.customer),
        access_status=done.access_status,
    )


_EXPIRED_PAGE = """<!doctype html><html lang="pt-BR"><meta charset="utf-8">
<meta name="robots" content="noindex"><title>Login expirado</title>
<body style="font-family:system-ui;max-width:32rem;margin:4rem auto;padding:0 1rem">
<h1>Login expirado</h1><p>{message}</p></body></html>"""


@router.get("/auth/google/callback", include_in_schema=False)
async def google_callback(
    request: Request,
    session: DbSession,
    code: Annotated[str | None, Query(max_length=2048)] = None,
    state: Annotated[str | None, Query(max_length=256)] = None,
    error: Annotated[str | None, Query(max_length=256)] = None,
) -> Response:
    """Google sends the customer here (the one redirect URI registered for every store)."""
    try:
        host = normalize_hostname(request.headers.get("host"))
    except InvalidHostnameError as exc:
        raise NotFoundError("Recurso não encontrado.") from exc
    if host != settings.api_public_host:
        raise NotFoundError("Recurso não encontrado.")
    headers = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
    try:
        target = await CustomerAuthService(session).callback(state=state, code=code, error=error)
    except InvalidLoginStateError as exc:
        return HTMLResponse(
            _EXPIRED_PAGE.format(message=exc.message), status_code=400, headers=headers
        )
    return RedirectResponse(target, status_code=302, headers=headers)


# ------------------------------------------------------------------ the customer's own session
@router.get("/me/session", response_model=SessionRead, summary="Cliente da sessão atual")
async def my_session(session: DbSession, viewer: CurrentCustomer) -> SessionRead:
    customer = await session.get(Customer, viewer.customer_id)
    if customer is None:
        raise NotFoundError("Cliente não encontrado.")
    return SessionRead(customer=customer_read(customer), access_status=viewer.access_status)


@router.post(
    "/me/logout",
    status_code=204,
    summary="Sai da loja (esta sessão, ou todas as sessões nesta loja)",
    dependencies=[Depends(require_same_origin)],
)
async def logout(
    session: DbSession, tenant: StorefrontTenant, viewer: CurrentCustomer, body: LogoutBody
) -> Response:
    now = utcnow()
    repo = CustomerSessionRepository(session)
    if body.all:
        await repo.revoke_all(tenant.id, viewer.customer_id, reason="logout_all", now=now)
    else:
        row = await repo.by_id(viewer.session_id)
        if row is not None and row.revoked_at is None:
            row.revoked_at, row.revoked_reason = now, "logout"
    return Response(status_code=204)


@router.get("/me/access", response_model=AccessRead, summary="Situação do acesso nesta loja")
async def my_access(session: DbSession, viewer: CurrentCustomer) -> AccessRead:
    row = await CustomerAccessService(session).current(viewer.customer_id)
    return AccessRead(
        status=row.status if row else "none",
        requested_at=row.requested_at if row else None,
    )


@router.post(
    "/me/access/request",
    response_model=AccessRead,
    summary="Pede acesso ao catálogo de uma loja fechada (whitelist)",
    dependencies=[
        Depends(require_same_origin),
        Depends(rate_limit("customer_access_request", limit=5, window_seconds=3600)),
    ],
)
async def request_access(
    session: DbSession,
    tenant: StorefrontTenant,
    viewer: CurrentCustomer,
    body: AccessRequestBody,
    ip: ClientIp,
) -> AccessRead:
    row = await CustomerAccessService(session).request(
        tenant, viewer.customer_id, message=body.message, ip=ip
    )
    return AccessRead(status=row.status, requested_at=row.requested_at)


# ------------------------------------------------------------------ WhatsApp phone confirmation
@router.get(
    "/me/phone", response_model=PhoneRead, summary="WhatsApp do cliente e se está confirmado"
)
async def my_phone(session: DbSession, viewer: CurrentCustomer) -> PhoneRead:
    customer = await session.get(Customer, viewer.customer_id)
    if customer is None:
        raise NotFoundError("Cliente não encontrado.")
    return PhoneRead(
        phone_masked=mask_phone(customer.phone_e164),
        verified=customer.phone_verified_at is not None,
    )


@router.post(
    "/me/phone/start",
    response_model=PhoneStartRead,
    summary="Gera o link do WhatsApp para confirmar o número (CONFIRMAR <código>)",
    dependencies=[
        Depends(require_same_origin),
        Depends(rate_limit("customer_phone_start", limit=10, window_seconds=3600)),
    ],
)
async def start_phone(
    session: DbSession,
    tenant: StorefrontTenant,
    viewer: CurrentCustomer,
    body: PhoneStartBody,
    ip: ClientIp,
) -> PhoneStartRead:
    started = await PhoneVerificationService(session).start(
        tenant, viewer.customer_id, body.phone, ip=ip
    )
    return PhoneStartRead(whatsapp_url=started.whatsapp_url, expires_at=started.expires_at)


@router.post(
    "/internal/agents/phone-confirmations",
    response_model=PhoneConfirmRead,
    summary="api-agents recebeu CONFIRMAR <código> de um número: confirma se o código é daqui",
    dependencies=[Depends(require_internal("agents"))],
)
async def confirm_phone(session: DbSession, body: PhoneConfirmBody) -> PhoneConfirmRead:
    confirmed = await PhoneVerificationService(session).confirm(body.token, body.phone)
    if confirmed is None:
        raise NotFoundError("Código não encontrado.")
    return PhoneConfirmRead(tenant_name=confirmed.tenant_name)
