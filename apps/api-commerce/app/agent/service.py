"""Gerar o código, trocá-lo por credencial, e resolver quem está falando.

A assimetria aqui é de propósito: gerar código é ação do **lojista** no painel dele; resgatar é
chamada **interna** da plataforma, com o token de serviço. Assim o código sozinho não abre nada
— quem não fala pela api-agents não tem como trocá-lo por credencial, mesmo tendo ouvido o
lojista ditá-lo.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import links
from app.agent.models import AgentLink, AgentLinkCode
from app.core.exceptions import NotFoundError, ValidationError
from app.models.base import utcnow
from app.tenancy.context import CROSS_TENANT_OPTION


@dataclass(frozen=True, slots=True)
class Issued:
    """O que o painel mostra uma vez só."""

    code: str
    kind: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class Redeemed:
    link_id: str
    token: str
    tenant_id: str
    kind: str
    store_name: str


class InvalidLinkCode(ValidationError):
    """Código errado, vencido ou já usado — e a recusa não diz qual dos três.

    Diferenciar ajudaria quem está tentando adivinhar: "vencido" confirma que o código existiu.
    """

    error_code = "link_code_invalid"
    message = "Código inválido ou vencido. Peça um novo ao dono da loja."


class AgentLinkService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def issue(self, *, tenant_id: str, kind: str, label: str | None, actor: str) -> Issued:
        """Um código novo para o lojista ditar. Não invalida os anteriores."""
        if kind not in links.SCOPES:
            raise ValidationError("Tipo de vínculo desconhecido.", fields=["kind"])
        code = links.new_code()
        agora = utcnow()
        self.session.add(
            AgentLinkCode(
                tenant_id=tenant_id,
                code_hash=links.hash_code(code),
                kind=kind,
                label=(label or "").strip()[:80] or None,
                created_by_actor=actor,
            )
        )
        await self.session.flush()
        return Issued(code=code, kind=kind, expires_at=agora + links.CODE_TTL)

    async def redeem(self, *, code: str, account_ref: str | None) -> Redeemed:
        """Troca o código pela credencial do agente. O código morre aqui.

        Busca fora do escopo de loja de propósito: quem resgata ainda não sabe de qual loja é o
        código — é justamente isso que o código diz.
        """
        limpo = links.normalize_code(code)
        if len(limpo) != links.CODE_LENGTH:
            raise InvalidLinkCode
        stmt = (
            select(AgentLinkCode)
            .where(AgentLinkCode.code_hash == links.hash_code(limpo))
            .execution_options(**{CROSS_TENANT_OPTION: True})
        )
        row = await self.session.scalar(stmt)
        agora = utcnow()
        if row is None or row.used_at is not None or links.expired(row.created_at, agora):
            raise InvalidLinkCode

        from app.tenancy.resolver import TenantResolver

        contexto = await TenantResolver(self.session).resolve_by_id(row.tenant_id)
        token = links.new_token()
        link = AgentLink(
            tenant_id=row.tenant_id,
            token_hash=links.hash_token(token),
            kind=row.kind,
            label=row.label,
            account_ref=(account_ref or None),
        )
        self.session.add(link)
        await self.session.flush()
        row.used_at = agora
        row.link_id = link.id
        await self.session.flush()
        return Redeemed(
            link_id=link.id,
            token=token,
            tenant_id=row.tenant_id,
            kind=row.kind,
            store_name=contexto.name,
        )

    async def resolve(self, token: str) -> AgentLink:
        """O vínculo por trás de um token, se ele ainda vale.

        Fora do escopo de loja: é o token que diz qual loja. Marca o uso para o lojista ver
        agente parado na lista — credencial esquecida viva é credencial que ninguém revoga.
        """
        if not token or len(token) > 128:
            raise NotFoundError("Credencial inválida.")
        stmt = (
            select(AgentLink)
            .where(
                AgentLink.token_hash == links.hash_token(token),
                AgentLink.revoked_at.is_(None),
            )
            .execution_options(**{CROSS_TENANT_OPTION: True})
        )
        link = await self.session.scalar(stmt)
        if link is None:
            raise NotFoundError("Credencial inválida.")
        link.last_used_at = utcnow()
        return link

    async def revoke(self, *, tenant_id: str, link_id: str, actor: str) -> AgentLink:
        stmt = select(AgentLink).where(AgentLink.id == link_id, AgentLink.tenant_id == tenant_id)
        link = await self.session.scalar(stmt)
        if link is None:
            raise NotFoundError("Vínculo não encontrado.")
        if link.revoked_at is None:
            link.revoked_at = utcnow()
            link.revoked_by_actor = actor
            await self.session.flush()
        return link

    async def list_for_tenant(self, tenant_id: str) -> list[AgentLink]:
        stmt = (
            select(AgentLink)
            .where(AgentLink.tenant_id == tenant_id)
            .order_by(AgentLink.created_at.desc())
            .limit(50)
        )
        return list((await self.session.scalars(stmt)).all())
