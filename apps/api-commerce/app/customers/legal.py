"""Store legal documents (terms, privacy) and consents.

A document is published as a new immutable version; the storefront shows the latest one. When
a customer signs in, the versions the "Entrar" page showed are recorded as consents (with time,
IP and user agent), so the store can prove what was accepted and when.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.outbox import emit
from app.audit.writer import audit
from app.core.exceptions import ConflictError
from app.customers.legal_models import Consent, LegalDocument, LegalKind
from app.models.base import utcnow
from app.tenancy.context import TenantContext
from app.tenancy.service import Actor

MAX_HISTORY = 20


@dataclass(frozen=True, slots=True)
class Acceptance:
    kind: str
    version: int


class LegalService:
    def __init__(self, session: AsyncSession, tenant: TenantContext) -> None:
        self.session = session
        self.tenant = tenant

    async def latest(self, kind: str) -> LegalDocument | None:
        stmt = (
            select(LegalDocument)
            .where(LegalDocument.kind == kind)
            .order_by(LegalDocument.version.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def latest_versions(self) -> dict[str, LegalDocument]:
        found: dict[str, LegalDocument] = {}
        for kind in LegalKind:
            doc = await self.latest(kind)
            if doc is not None:
                found[kind] = doc
        return found

    async def history(self, kind: str) -> list[LegalDocument]:
        stmt = (
            select(LegalDocument)
            .where(LegalDocument.kind == kind)
            .order_by(LegalDocument.version.desc())
            .limit(MAX_HISTORY)
        )
        return list((await self.session.execute(stmt)).scalars())

    async def publish(self, kind: str, content: str, actor: Actor) -> LegalDocument:
        """New version = latest + 1. Publishing the same text again is a no-op."""
        text = content.strip()
        digest = hashlib.sha256(text.encode()).hexdigest()
        current = await self.latest(kind)
        if current is not None and current.sha256 == digest:
            return current
        doc = LegalDocument(
            kind=kind,
            version=(current.version if current else 0) + 1,
            content=text,
            sha256=digest,
            published_at=utcnow(),
            created_by_actor=actor.id,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(doc)
                await self.session.flush()
        except IntegrityError as exc:
            raise ConflictError(
                "Outra versão foi publicada ao mesmo tempo. Recarregue e tente de novo."
            ) from exc
        await audit(
            self.session,
            actor=actor.id,
            action="legal_document.published",
            entity_type="legal_document",
            entity_id=doc.id,
            tenant_id=self.tenant.id,
            after={"kind": kind, "version": doc.version, "sha256": digest},
            ip=actor.ip,
            user_agent=actor.user_agent,
        )
        await emit(
            self.session,
            aggregate_type="legal_document",
            aggregate_id=doc.id,
            event_type="tenant.legal_document_published",
            payload={"kind": kind, "version": doc.version},
            tenant_id=self.tenant.id,
        )
        return doc

    async def record_acceptance(
        self,
        customer_id: str,
        accepted: list[Acceptance],
        *,
        at: datetime,
        ip: str | None,
        user_agent: str | None,
        channel: str = "web",
    ) -> int:
        """Store one consent per accepted document version that exists in this store."""
        recorded = 0
        for item in accepted:
            doc = (
                await self.session.execute(
                    select(LegalDocument)
                    .where(LegalDocument.kind == item.kind)
                    .where(LegalDocument.version == item.version)
                )
            ).scalar_one_or_none()
            if doc is None:
                continue
            self.session.add(
                Consent(
                    customer_id=customer_id,
                    kind=doc.kind,
                    document_id=doc.id,
                    document_version=doc.version,
                    accepted_at=at,
                    channel=channel,
                    ip=ip,
                    user_agent=(user_agent or "")[:300] or None,
                )
            )
            recorded += 1
        if recorded:
            await self.session.flush()
        return recorded

    async def consents_of(self, customer_id: str) -> list[Consent]:
        stmt = (
            select(Consent)
            .where(Consent.customer_id == customer_id)
            .order_by(Consent.accepted_at.desc())
            .limit(MAX_HISTORY)
        )
        return list((await self.session.execute(stmt)).scalars())
