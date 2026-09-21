from __future__ import annotations

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Path, Query, Request, status

from app.api.deps import DbSession, PlatformOperator, admin_actor
from app.audit.idempotency import idempotent
from app.core.exceptions import NotFoundError
from app.core.pagination import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    Page,
    decode_cursor,
    encode_cursor,
)
from app.schemas.tenant import (
    DnsInstructionsRead,
    DomainCheckRead,
    DomainCreate,
    DomainRead,
    FeatureFlagsUpdate,
    SettingUpdate,
    TenantCreate,
    TenantRead,
    TenantStatusChange,
)
from app.tenancy.dns import DnsVerifier, instructions_for
from app.tenancy.models import (
    DEFAULT_FEATURE_FLAGS,
    DomainKind,
    DomainPurpose,
    DomainRole,
    TenantDomain,
    TenantStatus,
)
from app.tenancy.service import TenantService

router = APIRouter(prefix="/ops/tenants", tags=["Ops — Tenants"])

TenantId = Annotated[str, Path(min_length=36, max_length=36)]


def _domain_read(domain: TenantDomain, with_instructions: bool = False) -> DomainRead:
    instructions = None
    if with_instructions and domain.kind != DomainKind.PLATFORM_SUBDOMAIN:
        instr = instructions_for(
            domain.hostname, domain.verification_token, domain.kind == DomainKind.CUSTOM_APEX
        )
        instructions = DnsInstructionsRead(**asdict(instr))
    return DomainRead(
        id=domain.id,
        tenant_id=domain.tenant_id,
        hostname=domain.hostname,
        kind=domain.kind,
        purpose=domain.purpose,
        role=domain.role,
        status=domain.status,
        tls_status=domain.tls_status,
        verified_at=domain.verified_at,
        last_check_at=domain.last_check_at,
        last_error=domain.last_error,
        instructions=instructions,
    )


@router.get("", response_model=Page[TenantRead], summary="Lista tenants (mais novos primeiro)")
async def list_tenants(
    session: DbSession,
    _: PlatformOperator,
    status_filter: str | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query(max_length=256)] = None,
) -> Page[TenantRead]:
    before_id = decode_cursor(cursor, "id")["id"] if cursor else None
    rows = await TenantService(session).repo.list_page(
        limit=limit, before_id=before_id, status=status_filter
    )
    items = [TenantRead.model_validate(t, from_attributes=True) for t in rows[:limit]]
    next_cursor = encode_cursor(id=rows[limit - 1].id) if len(rows) > limit else None
    return Page[TenantRead](items=items, next_cursor=next_cursor)


@router.post(
    "",
    response_model=TenantRead,
    status_code=status.HTTP_201_CREATED,
    summary="Cria tenant (draft) com flags, settings e subdomínio de plataforma",
)
@idempotent("ops.tenant.create", status_code=201)
async def create_tenant(
    *, request: Request, session: DbSession, user: PlatformOperator, body: TenantCreate
) -> TenantRead:
    tenant = await TenantService(session).create(
        slug=body.slug,
        name=body.name,
        legal_name=body.legal_name,
        document=body.document,
        timezone=body.timezone,
        plan=body.plan,
        actor=admin_actor(request, user),
    )
    return TenantRead.model_validate(tenant, from_attributes=True)


@router.get("/{tenant_id}", response_model=TenantRead, summary="Detalhe do tenant")
async def get_tenant(session: DbSession, _: PlatformOperator, tenant_id: TenantId) -> TenantRead:
    tenant = await TenantService(session).get_or_404(tenant_id)
    return TenantRead.model_validate(tenant, from_attributes=True)


@router.post("/{tenant_id}/status", response_model=TenantRead, summary="Muda status do tenant")
async def change_status(
    request: Request,
    session: DbSession,
    user: PlatformOperator,
    tenant_id: TenantId,
    body: TenantStatusChange,
) -> TenantRead:
    service = TenantService(session)
    tenant = await service.get_or_404(tenant_id)
    await service.set_status(
        tenant, TenantStatus(body.status), admin_actor(request, user), body.reason
    )
    return TenantRead.model_validate(tenant, from_attributes=True)


@router.get("/{tenant_id}/features", response_model=dict[str, bool], summary="Feature flags")
async def get_features(
    session: DbSession, _: PlatformOperator, tenant_id: TenantId
) -> dict[str, bool]:
    service = TenantService(session)
    await service.get_or_404(tenant_id)
    stored = await service.repo.feature_flags(tenant_id)
    # Effective value of every known flag: a flag added after the tenant was created has no
    # row yet and is off (TenantContext.feature), so the panel still lists it.
    return {key: stored.get(key, False) for key in DEFAULT_FEATURE_FLAGS}


@router.put(
    "/{tenant_id}/features", response_model=dict[str, bool], summary="Atualiza feature flags"
)
async def put_features(
    request: Request,
    session: DbSession,
    user: PlatformOperator,
    tenant_id: TenantId,
    body: FeatureFlagsUpdate,
) -> dict[str, bool]:
    service = TenantService(session)
    tenant = await service.get_or_404(tenant_id)
    return await service.set_features(tenant, body.flags, admin_actor(request, user))


@router.put(
    "/{tenant_id}/settings/{key}",
    response_model=dict[str, object],
    summary="Atualiza uma configuração",
)
async def put_setting(
    request: Request,
    session: DbSession,
    user: PlatformOperator,
    tenant_id: TenantId,
    key: Annotated[str, Path(max_length=64)],
    body: SettingUpdate,
) -> dict[str, object]:
    service = TenantService(session)
    tenant = await service.get_or_404(tenant_id)
    return await service.set_setting(tenant, key, body.value, admin_actor(request, user))


@router.get("/{tenant_id}/domains", response_model=list[DomainRead], summary="Domínios do tenant")
async def list_domains(
    session: DbSession, _: PlatformOperator, tenant_id: TenantId
) -> list[DomainRead]:
    service = TenantService(session)
    await service.get_or_404(tenant_id)
    return [
        _domain_read(d, with_instructions=True) for d in await service.repo.list_domains(tenant_id)
    ]


@router.post(
    "/{tenant_id}/domains",
    response_model=DomainRead,
    status_code=status.HTTP_201_CREATED,
    summary="Registra domínio e devolve instruções de DNS",
)
async def add_domain(
    request: Request,
    session: DbSession,
    user: PlatformOperator,
    tenant_id: TenantId,
    body: DomainCreate,
) -> DomainRead:
    service = TenantService(session)
    tenant = await service.get_or_404(tenant_id)
    domain, instructions = await service.register_domain(
        tenant,
        hostname=body.hostname,
        purpose=DomainPurpose(body.purpose),
        role=DomainRole(body.role),
        actor=admin_actor(request, user),
    )
    read = _domain_read(domain)
    read.instructions = DnsInstructionsRead(**asdict(instructions))
    return read


@router.post(
    "/{tenant_id}/domains/{domain_id}/verify",
    response_model=DomainCheckRead,
    summary="Verifica DNS agora (TXT + A/CNAME)",
)
async def verify_domain(
    session: DbSession, _: PlatformOperator, tenant_id: TenantId, domain_id: str
) -> DomainCheckRead:
    service = TenantService(session)
    await service.get_or_404(tenant_id)
    domain = await service.repo.get_domain(tenant_id, domain_id)
    if domain is None:
        raise NotFoundError("Domínio não encontrado.")
    check = await service.verify_domain(domain, DnsVerifier())
    return DomainCheckRead(
        domain=_domain_read(domain, with_instructions=True),
        txt_ok=check.txt_ok,
        target_ok=check.target_ok,
        observed_a=check.observed_a,
        observed_cname=check.observed_cname,
        errors=check.errors,
    )


@router.delete(
    "/{tenant_id}/domains/{domain_id}",
    response_model=DomainRead,
    summary="Desativa um domínio (sai do Traefik)",
)
async def disable_domain(
    request: Request,
    session: DbSession,
    user: PlatformOperator,
    tenant_id: TenantId,
    domain_id: str,
) -> DomainRead:
    service = TenantService(session)
    tenant = await service.get_or_404(tenant_id)
    domain = await service.repo.get_domain(tenant_id, domain_id)
    if domain is None:
        raise NotFoundError("Domínio não encontrado.")
    await service.disable_domain(tenant, domain, admin_actor(request, user))
    return _domain_read(domain)
