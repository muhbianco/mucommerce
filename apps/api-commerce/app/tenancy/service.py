from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.outbox import emit
from app.audit.writer import audit
from app.core.config import settings
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.hosts import InvalidHostnameError, is_subdomain_of, normalize_hostname
from app.models.base import utcnow
from app.tenancy.context import CROSS_TENANT_OPTION, bind_session_tenant
from app.tenancy.dns import DnsCheck, DnsInstructions, DnsVerifier, instructions_for
from app.tenancy.models import (
    DEFAULT_FEATURE_FLAGS,
    DEFAULT_SETTINGS,
    DomainKind,
    DomainPurpose,
    DomainRole,
    DomainStatus,
    Tenant,
    TenantDomain,
    TenantFeatureFlag,
    TenantSetting,
    TenantStatus,
)
from app.tenancy.repository import TenantRepository
from app.tenancy.resolver import invalidate_host_cache
from app.tenancy.setting_refs import check_setting_references
from app.tenancy.settings_schemas import validate_setting

_SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])?$")

RESERVED_SLUGS = {"www", "api", "painel", "admin", "loja", "chat", "chatwoot", "edge", "staging"}

# Second-level public suffixes common in Brazil: `x.com.br` is an apex, `www.x.com.br` is not.
_SECOND_LEVEL_SUFFIXES = {
    "com.br",
    "net.br",
    "org.br",
    "art.br",
    "eco.br",
    "blog.br",
    "app.br",
    "dev.br",
    "eng.br",
    "ind.br",
    "inf.br",
    "adv.br",
    "med.br",
    "odo.br",
    "psc.br",
    "srv.br",
    "tur.br",
    "vet.br",
    "log.br",
    "esp.br",
    "far.br",
    "flog.br",
    "wiki.br",
    "tv.br",
}


def classify_kind(host: str) -> DomainKind:
    labels = host.split(".")
    if len(labels) == 2:
        return DomainKind.CUSTOM_APEX
    if len(labels) == 3 and ".".join(labels[-2:]) in _SECOND_LEVEL_SUFFIXES:
        return DomainKind.CUSTOM_APEX
    return DomainKind.CUSTOM_SUBDOMAIN


AUDIT_VALUE_MAX_CHARS = 4000


def _audit_value(value: Any) -> Any:
    """Settings go to the audit log whole, except large ones (landing text): a summary."""
    if value is None:
        return None
    size = len(json.dumps(value, ensure_ascii=False))
    if size <= AUDIT_VALUE_MAX_CHARS:
        return value
    digest = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:16]
    return {"_summary": True, "chars": size, "sha256": digest, "keys": sorted(value)}


@dataclass(frozen=True, slots=True)
class Actor:
    """Who performs the action, in `type:id` form plus request metadata for audit."""

    id: str
    ip: str | None = None
    user_agent: str | None = None

    @classmethod
    def system(cls, name: str = "api") -> Actor:
        return cls(id=f"system:{name}")


class TenantService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = TenantRepository(session)

    # ------------------------------------------------------------------ tenants
    async def create(
        self,
        *,
        slug: str,
        name: str,
        actor: Actor,
        legal_name: str | None = None,
        document: str | None = None,
        timezone: str = "America/Sao_Paulo",
        plan: str = "standard",
    ) -> Tenant:
        slug = slug.strip().lower()
        if not _SLUG.match(slug) or slug in RESERVED_SLUGS:
            raise ValidationError("Slug inválido ou reservado.", slug=slug)
        if await self.repo.get_by_slug(slug):
            raise ConflictError("Já existe um tenant com este slug.", slug=slug)

        tenant = Tenant(
            slug=slug,
            name=name.strip(),
            legal_name=legal_name,
            document=document,
            timezone=timezone,
            plan=plan,
            status=TenantStatus.DRAFT,
        )
        self.session.add(tenant)
        await self.session.flush()

        # Tenant-scoped defaults are written with the tenant bound to the session.
        bind_session_tenant(self.session, tenant.id)
        for key, enabled in DEFAULT_FEATURE_FLAGS.items():
            self.session.add(TenantFeatureFlag(tenant_id=tenant.id, key=key, enabled=enabled))
        for key, value in DEFAULT_SETTINGS.items():
            self.session.add(TenantSetting(tenant_id=tenant.id, key=key, value=dict(value)))

        platform_host = f"{slug}.{settings.platform_base_domain}"
        self.session.add(
            TenantDomain(
                tenant_id=tenant.id,
                hostname=platform_host,
                kind=DomainKind.PLATFORM_SUBDOMAIN,
                purpose=DomainPurpose.STOREFRONT,
                role=DomainRole.PRIMARY,
                status=DomainStatus.ACTIVE,
                verified_at=utcnow(),
            )
        )
        await self.session.flush()

        await audit(
            self.session,
            actor=actor.id,
            action="tenant.created",
            entity_type="tenant",
            entity_id=tenant.id,
            tenant_id=tenant.id,
            after={"slug": slug, "name": tenant.name, "plan": plan},
            ip=actor.ip,
            user_agent=actor.user_agent,
        )
        await emit(
            self.session,
            aggregate_type="tenant",
            aggregate_id=tenant.id,
            event_type="tenant.created",
            payload={"slug": slug, "name": tenant.name},
            tenant_id=tenant.id,
        )
        return tenant

    async def get_or_404(self, tenant_id: str) -> Tenant:
        tenant = await self.repo.get(tenant_id)
        if tenant is None:
            raise NotFoundError("Tenant não encontrado.")
        return tenant

    async def set_status(
        self, tenant: Tenant, status: TenantStatus, actor: Actor, reason: str | None = None
    ) -> Tenant:
        allowed: dict[str, set[str]] = {
            TenantStatus.DRAFT: {
                TenantStatus.PROVISIONING,
                TenantStatus.ACTIVE,
                TenantStatus.ARCHIVED,
            },
            TenantStatus.PROVISIONING: {TenantStatus.ACTIVE, TenantStatus.DRAFT},
            TenantStatus.ACTIVE: {TenantStatus.SUSPENDED},
            TenantStatus.SUSPENDED: {TenantStatus.ACTIVE, TenantStatus.ARCHIVED},
            TenantStatus.ARCHIVED: set(),
        }
        if status not in allowed[tenant.status]:
            raise ConflictError(
                "Transição de status do tenant não permitida.",
                from_status=tenant.status,
                to_status=str(status),
            )
        before = tenant.status
        tenant.status = status
        if status == TenantStatus.ACTIVE and tenant.activated_at is None:
            tenant.activated_at = utcnow()
        await audit(
            self.session,
            actor=actor.id,
            action=f"tenant.{status}",
            entity_type="tenant",
            entity_id=tenant.id,
            tenant_id=tenant.id,
            before={"status": before},
            after={"status": str(status), "reason": reason},
            ip=actor.ip,
            user_agent=actor.user_agent,
        )
        await emit(
            self.session,
            aggregate_type="tenant",
            aggregate_id=tenant.id,
            event_type=f"tenant.{status}",
            payload={"from": before, "reason": reason},
            tenant_id=tenant.id,
        )
        await invalidate_host_cache([d.hostname for d in tenant.domains])
        return tenant

    # ------------------------------------------------------------------ features / settings
    async def set_features(
        self, tenant: Tenant, flags: dict[str, bool], actor: Actor
    ) -> dict[str, bool]:
        unknown = sorted(set(flags) - set(DEFAULT_FEATURE_FLAGS))
        if unknown:
            raise ValidationError("Feature flag desconhecida.", unknown=unknown)
        bind_session_tenant(self.session, tenant.id)
        current = await self.repo.feature_flags(tenant.id)
        rows = {
            row.key: row
            for row in (
                await self.session.execute(
                    select(TenantFeatureFlag).where(TenantFeatureFlag.tenant_id == tenant.id)
                )
            ).scalars()
        }
        for key, enabled in flags.items():
            if key in rows:
                rows[key].enabled = enabled
            else:
                self.session.add(TenantFeatureFlag(tenant_id=tenant.id, key=key, enabled=enabled))
        await self.session.flush()
        await audit(
            self.session,
            actor=actor.id,
            action="tenant.features_changed",
            entity_type="tenant",
            entity_id=tenant.id,
            tenant_id=tenant.id,
            before=current,
            after={**current, **flags},
            ip=actor.ip,
            user_agent=actor.user_agent,
        )
        await invalidate_host_cache([d.hostname for d in tenant.domains])
        return {**current, **flags}

    async def set_setting(
        self, tenant: Tenant, key: str, value: dict[str, Any], actor: Actor
    ) -> dict[str, Any]:
        schema_version, value = validate_setting(key, value)
        bind_session_tenant(self.session, tenant.id)
        await check_setting_references(self.session, key, value)
        current = await self.repo.settings(tenant.id)
        row = (
            await self.session.execute(
                select(TenantSetting)
                .where(TenantSetting.tenant_id == tenant.id)
                .where(TenantSetting.key == key)
            )
        ).scalar_one_or_none()
        if row is None:
            row = TenantSetting(
                tenant_id=tenant.id, key=key, value=value, schema_version=schema_version
            )
            self.session.add(row)
        else:
            row.value = value
            row.schema_version = schema_version
        await self.session.flush()
        await emit(
            self.session,
            aggregate_type="tenant",
            aggregate_id=tenant.id,
            event_type="tenant.settings_changed",
            payload={"key": key, "schema_version": schema_version},
            tenant_id=tenant.id,
        )
        await audit(
            self.session,
            actor=actor.id,
            action="tenant.settings_changed",
            entity_type="tenant_setting",
            entity_id=row.id,
            tenant_id=tenant.id,
            before={key: _audit_value(current.get(key))},
            after={key: _audit_value(value)},
            ip=actor.ip,
            user_agent=actor.user_agent,
        )
        await invalidate_host_cache([d.hostname for d in tenant.domains])
        return value

    # ------------------------------------------------------------------ domains
    async def register_domain(
        self,
        tenant: Tenant,
        *,
        hostname: str,
        purpose: DomainPurpose,
        role: DomainRole,
        actor: Actor,
    ) -> tuple[TenantDomain, DnsInstructions]:
        try:
            host = normalize_hostname(hostname)
        except InvalidHostnameError as exc:
            raise ValidationError(str(exc), hostname=hostname) from exc

        reserved = {settings.panel_host, settings.api_public_host, settings.platform_base_domain}
        if host in reserved or (
            is_subdomain_of(host, settings.platform_base_domain)
            and host != f"{tenant.slug}.{settings.platform_base_domain}"
        ):
            raise ValidationError("Hostname reservado pela plataforma.", hostname=host)

        if role == DomainRole.PRIMARY and purpose == DomainPurpose.STOREFRONT:
            # The primary host feeds canonical URLs, sitemap and redirects: a host that does not
            # answer yet must never take that place. Promote it once it is active.
            raise ValidationError(
                "Domínio novo entra como alias; torne-o primário depois de ativo.", hostname=host
            )

        existing = await self.repo.get_domain_by_hostname(host)
        if existing is not None:
            raise ConflictError("Hostname já registrado.", hostname=host)

        kind = classify_kind(host)
        domain = TenantDomain(
            tenant_id=tenant.id,
            hostname=host,
            kind=kind,
            purpose=purpose,
            role=role,
            status=DomainStatus.PENDING_DNS,
        )
        self.session.add(domain)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError("Hostname já registrado.", hostname=host) from exc

        await audit(
            self.session,
            actor=actor.id,
            action="domain.registered",
            entity_type="tenant_domain",
            entity_id=domain.id,
            tenant_id=tenant.id,
            after={"hostname": host, "purpose": str(purpose), "role": str(role)},
            ip=actor.ip,
            user_agent=actor.user_agent,
        )
        await emit(
            self.session,
            aggregate_type="tenant_domain",
            aggregate_id=domain.id,
            event_type="domain.registered",
            payload={"hostname": host},
            tenant_id=tenant.id,
        )
        return domain, instructions_for(
            host, domain.verification_token, kind == DomainKind.CUSTOM_APEX
        )

    async def disable_domain(
        self, tenant: Tenant, domain: TenantDomain, actor: Actor
    ) -> TenantDomain:
        if domain.role == DomainRole.PRIMARY and domain.purpose == DomainPurpose.STOREFRONT:
            raise ConflictError("Não é possível desativar o domínio primário. Promova outro antes.")
        before = domain.status
        domain.status = DomainStatus.DISABLED
        await audit(
            self.session,
            actor=actor.id,
            action="domain.disabled",
            entity_type="tenant_domain",
            entity_id=domain.id,
            tenant_id=tenant.id,
            before={"status": before},
            after={"status": str(DomainStatus.DISABLED)},
        )
        await invalidate_host_cache([domain.hostname])
        return domain

    async def set_primary_domain(
        self, tenant: Tenant, domain: TenantDomain, actor: Actor
    ) -> TenantDomain:
        """Make an active storefront host the canonical one; the previous primary becomes alias."""
        if domain.purpose != DomainPurpose.STOREFRONT:
            raise ValidationError("Só domínios da loja podem ser primários.")
        if domain.role == DomainRole.PRIMARY:
            return domain  # idempotent
        if domain.status != DomainStatus.ACTIVE:
            raise ConflictError(
                "Só um domínio ativo pode ser primário. Verifique o DNS antes.",
                code="domain_not_active",
            )
        # Serialise role changes per tenant: two promotions at once must not leave two primaries.
        await self.session.execute(
            select(Tenant.id).where(Tenant.id == tenant.id).with_for_update()
        )
        previous = await self.repo.primary_domain(tenant.id, DomainPurpose.STOREFRONT)
        if previous is not None:
            previous.role = DomainRole.ALIAS
        domain.role = DomainRole.PRIMARY
        await self.session.flush()
        await audit(
            self.session,
            actor=actor.id,
            action="domain.primary_changed",
            entity_type="tenant_domain",
            entity_id=domain.id,
            tenant_id=tenant.id,
            before={"primary": previous.hostname if previous else None},
            after={"primary": domain.hostname},
            ip=actor.ip,
            user_agent=actor.user_agent,
        )
        await emit(
            self.session,
            aggregate_type="tenant_domain",
            aggregate_id=domain.id,
            event_type="domain.primary_changed",
            payload={
                "hostname": domain.hostname,
                "previous": previous.hostname if previous else None,
            },
            tenant_id=tenant.id,
        )
        await invalidate_host_cache([domain.hostname] + ([previous.hostname] if previous else []))
        return domain

    async def verify_domain(self, domain: TenantDomain, verifier: DnsVerifier) -> DnsCheck:
        """One verification round. Called by the beat job and by the ops "verify now" button."""
        check = await verifier.check(domain.hostname, domain.verification_token)
        domain.last_check_at = utcnow()
        previous = domain.status

        if domain.status == DomainStatus.PENDING_DNS:
            domain.status = DomainStatus.VERIFYING

        if check.txt_ok and domain.verified_at is None:
            domain.verified_at = utcnow()
        if check.txt_ok and check.target_ok:
            domain.status = DomainStatus.ACTIVE
            domain.failed_checks = 0
            domain.last_error = None
        elif check.txt_ok:
            domain.status = DomainStatus.VERIFIED
            domain.last_error = "TXT ok; A/CNAME ainda não aponta para a plataforma"
        else:
            domain.last_error = "; ".join(check.errors) or "TXT de verificação não encontrado"
            age = utcnow() - domain.created_at
            if age > timedelta(hours=settings.domain_verify_max_age_hours):
                domain.status = DomainStatus.FAILED

        if domain.status != previous:
            event_type = {
                DomainStatus.ACTIVE: "domain.activated",
                DomainStatus.VERIFIED: "domain.verified",
                DomainStatus.FAILED: "domain.failed",
            }.get(DomainStatus(domain.status), "domain.status_changed")
            await emit(
                self.session,
                aggregate_type="tenant_domain",
                aggregate_id=domain.id,
                event_type=event_type,
                payload={"hostname": domain.hostname, "from": previous, "to": domain.status},
                tenant_id=domain.tenant_id,
            )
            await invalidate_host_cache([domain.hostname])
        return check

    async def recheck_active_domain(self, domain: TenantDomain, verifier: DnsVerifier) -> None:
        """Periodic re-check: three consecutive failures take the host out of the edge."""
        if domain.hostname in settings.static_edge_hosts:
            # Our own zone, routed by stack labels: a DNS hiccup must not 404 the loja modelo.
            return
        check = await verifier.check(domain.hostname, domain.verification_token)
        domain.last_check_at = utcnow()
        if check.target_ok:
            domain.failed_checks = 0
            return
        domain.failed_checks += 1
        domain.last_error = "; ".join(check.errors) or "A/CNAME não aponta mais para a plataforma"
        if domain.failed_checks >= 3 and domain.kind != DomainKind.PLATFORM_SUBDOMAIN:
            domain.status = DomainStatus.VERIFYING
            await emit(
                self.session,
                aggregate_type="tenant_domain",
                aggregate_id=domain.id,
                event_type="domain.deactivated",
                payload={"hostname": domain.hostname, "reason": domain.last_error},
                tenant_id=domain.tenant_id,
            )
            await invalidate_host_cache([domain.hostname])


__all__ = ["CROSS_TENANT_OPTION", "Actor", "TenantService", "classify_kind"]
