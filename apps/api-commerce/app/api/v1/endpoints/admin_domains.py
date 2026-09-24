"""O endereço da loja, na mão do lojista.

O que a plataforma decide (a loja existe, quanto custa, quem é o dono) fica no admin do site; o
endereço é do cliente: ele registra o domínio dele, vê o que apontar no DNS e acompanha quando
entrou no ar. O subdomínio `.loja.muhbianco.com.br` é o chão que não some — não dá para desativar
nem deixar de ser o endereço reserva enquanto o domínio próprio não ativa.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request, status

from app.api.deps import CurrentAdmin, DbSession, admin_actor, require_tenant_scopes
from app.api.v1.endpoints.ops_tenants import _domain_read
from app.core.exceptions import NotFoundError, ValidationError
from app.core.scopes import Scope
from app.schemas.tenant import DnsInstructionsRead, DomainCheckRead, DomainCreate, DomainRead
from app.tenancy.context import TenantContext
from app.tenancy.dns import DnsVerifier
from app.tenancy.models import DomainKind, DomainPurpose, DomainRole, DomainStatus
from app.tenancy.service import TenantService

router = APIRouter(prefix="/admin/tenants/{tenant_id}/domains", tags=["Painel — Domínios"])

DomainId = Annotated[str, Path(min_length=36, max_length=36)]

DomainsReader = Annotated[TenantContext, Depends(require_tenant_scopes(Scope.DOMAINS_WRITE))]
DomainsWriter = Annotated[
    TenantContext, Depends(require_tenant_scopes(Scope.DOMAINS_WRITE, members_only=True))
]

# Enough for "loja.empresa.com.br" + "empresa.com.br" + um extra; segura engano de digitação
# virando dezenas de hosts mortos no Traefik.
MAX_CUSTOM_DOMAINS = 3


@router.get("", response_model=list[DomainRead], summary="Endereços da loja e o DNS de cada um")
async def list_domains(session: DbSession, tenant: DomainsReader) -> list[DomainRead]:
    service = TenantService(session)
    return [
        _domain_read(d, with_instructions=True) for d in await service.repo.list_domains(tenant.id)
    ]


@router.post(
    "",
    response_model=DomainRead,
    status_code=status.HTTP_201_CREATED,
    summary="Registra o domínio do cliente e devolve o que criar no DNS",
)
async def add_domain(
    request: Request,
    session: DbSession,
    tenant: DomainsWriter,
    user: CurrentAdmin,
    body: DomainCreate,
) -> DomainRead:
    service = TenantService(session)
    row = await service.get_or_404(tenant.id)
    # Desativado não conta: o endereço segue registrado (ninguém rouba o host), mas uma
    # tentativa abandonada não pode consumir a vaga do cliente para sempre.
    customs = [
        d
        for d in await service.repo.list_domains(tenant.id)
        if d.kind != DomainKind.PLATFORM_SUBDOMAIN and d.status != DomainStatus.DISABLED
    ]
    if len(customs) >= MAX_CUSTOM_DOMAINS:
        raise ValidationError(
            f"A loja já tem {MAX_CUSTOM_DOMAINS} endereços próprios. Desative um antes de somar.",
            limit=MAX_CUSTOM_DOMAINS,
        )
    domain, instructions = await service.register_domain(
        row,
        hostname=body.hostname,
        purpose=DomainPurpose(body.purpose),
        # Entra sempre como alias: só vira principal depois que responder de verdade.
        role=DomainRole.ALIAS,
        actor=admin_actor(request, user),
    )
    read = _domain_read(domain)
    read.instructions = DnsInstructionsRead(**asdict(instructions))
    return read


@router.post(
    "/{domain_id}/verify",
    response_model=DomainCheckRead,
    summary="Conferir o DNS agora, sem esperar a checagem automática",
)
async def verify_domain(
    session: DbSession, tenant: DomainsWriter, domain_id: DomainId
) -> DomainCheckRead:
    service = TenantService(session)
    domain = await service.repo.get_domain(tenant.id, domain_id)
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


@router.post(
    "/{domain_id}/primary",
    response_model=DomainRead,
    summary="Passa a ser o endereço principal da loja",
)
async def set_primary(
    request: Request,
    session: DbSession,
    tenant: DomainsWriter,
    user: CurrentAdmin,
    domain_id: DomainId,
) -> DomainRead:
    service = TenantService(session)
    row = await service.get_or_404(tenant.id)
    domain = await service.repo.get_domain(tenant.id, domain_id)
    if domain is None:
        raise NotFoundError("Domínio não encontrado.")
    await service.set_primary_domain(row, domain, admin_actor(request, user))
    return _domain_read(domain, with_instructions=True)


@router.delete(
    "/{domain_id}",
    response_model=DomainRead,
    summary="Desativa um endereço próprio (o da plataforma nunca sai)",
)
async def disable_domain(
    request: Request,
    session: DbSession,
    tenant: DomainsWriter,
    user: CurrentAdmin,
    domain_id: DomainId,
) -> DomainRead:
    service = TenantService(session)
    row = await service.get_or_404(tenant.id)
    domain = await service.repo.get_domain(tenant.id, domain_id)
    if domain is None:
        raise NotFoundError("Domínio não encontrado.")
    if domain.kind == DomainKind.PLATFORM_SUBDOMAIN:
        raise ValidationError(
            "O endereço .loja.muhbianco.com.br é o endereço reserva da loja e não sai do ar.",
            hostname=domain.hostname,
        )
    await service.disable_domain(row, domain, admin_actor(request, user))
    return _domain_read(domain)
