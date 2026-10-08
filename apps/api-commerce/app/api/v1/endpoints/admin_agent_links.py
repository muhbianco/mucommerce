"""Painel: os agentes conectados a esta loja.

O lojista gera um código, dita para quem vai configurar o agente, e vê na lista quem está
conectado — com o que cada um pode fazer e quando usou pela última vez. Revogar é dele e tem
efeito imediato: a credencial some, a conta de ninguém é tocada.

Quem conecta não precisa ser o dono da conta MuhBianco da loja. É o que permite, amanhã, um
assistente de terceiro atender por aqui sem ninguém trocar senha.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel, Field

from app.agent.links import SCOPES, scopes_for
from app.agent.service import AgentLinkService
from app.api.deps import CurrentAdmin, DbSession, admin_actor, require_tenant_scopes
from app.core.rate_limit import rate_limit
from app.core.scopes import Scope
from app.schemas.common import StrictModel
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/admin/tenants/{tenant_id}/agent-links", tags=["Painel — Agentes"])

#: Conectar agente é dar acesso à loja: fica com quem configura pagamento e integrações.
LinkOwner = Annotated[
    TenantContext, Depends(require_tenant_scopes(Scope.SETTINGS_WRITE, members_only=True))
]
LinkId = Annotated[str, Path(min_length=36, max_length=36)]


class CodeIn(StrictModel):
    #: `sales` atende e vende; `operator` é o assistente do próprio lojista.
    tipo: Annotated[str, Field(pattern="^(sales|operator)$")] = "sales"
    nome: Annotated[str, Field(max_length=80)] | None = None


class CodeRead(BaseModel):
    #: O lojista dita isto. Some da tela e não volta: gerar outro é barato.
    codigo: str
    tipo: str
    expira_em: datetime
    permissoes: list[str]


class LinkRead(BaseModel):
    id: str
    tipo: str
    nome: str | None
    conta: str | None
    criado_em: datetime
    ultimo_uso: datetime | None
    revogado_em: datetime | None
    permissoes: list[str]


@router.post(
    "/codes",
    response_model=CodeRead,
    status_code=201,
    summary="Gera o código para conectar um agente",
    dependencies=[Depends(rate_limit("agent_link_code", 10, 300))],
)
async def issue_code(
    request: Request, session: DbSession, user: CurrentAdmin, tenant: LinkOwner, body: CodeIn
) -> CodeRead:
    issued = await AgentLinkService(session).issue(
        tenant_id=tenant.id,
        kind=body.tipo,
        label=body.nome,
        actor=admin_actor(request, user).id,
    )
    await session.commit()
    return CodeRead(
        codigo=issued.code,
        tipo=issued.kind,
        expira_em=issued.expires_at,
        permissoes=sorted(scopes_for(issued.kind)),
    )


@router.get("", response_model=list[LinkRead], summary="Agentes conectados a esta loja")
async def list_links(session: DbSession, user: CurrentAdmin, tenant: LinkOwner) -> Any:
    return [
        LinkRead(
            id=link.id,
            tipo=link.kind,
            nome=link.label,
            conta=link.account_ref,
            criado_em=link.created_at,
            ultimo_uso=link.last_used_at,
            revogado_em=link.revoked_at,
            permissoes=sorted(scopes_for(link.kind)),
        )
        for link in await AgentLinkService(session).list_for_tenant(tenant.id)
    ]


@router.delete(
    "/{link_id}",
    response_model=LinkRead,
    summary="Desconecta o agente (a credencial para de valer na hora)",
)
async def revoke_link(
    request: Request, session: DbSession, user: CurrentAdmin, tenant: LinkOwner, link_id: LinkId
) -> Any:
    link = await AgentLinkService(session).revoke(
        tenant_id=tenant.id, link_id=link_id, actor=admin_actor(request, user).id
    )
    await session.commit()
    return LinkRead(
        id=link.id,
        tipo=link.kind,
        nome=link.label,
        conta=link.account_ref,
        criado_em=link.created_at,
        ultimo_uso=link.last_used_at,
        revogado_em=link.revoked_at,
        permissoes=sorted(scopes_for(link.kind)),
    )


@router.get("/kinds", response_model=dict[str, list[str]], summary="Os tipos e o que cada um faz")
async def list_kinds(user: CurrentAdmin, tenant: LinkOwner) -> dict[str, list[str]]:
    """Para a tela dizer ao lojista o que ele está liberando, em vez de só o nome do tipo."""
    return {kind: sorted(str(scope) for scope in scopes) for kind, scopes in SCOPES.items()}
