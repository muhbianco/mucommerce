"""Operational CLI. Runs in the `commerce_migrate` job and by hand.

python -m app.cli db ensure            # CREATE DATABASE IF NOT EXISTS (migration user)
python -m app.cli db upgrade           # alembic upgrade head (migration user)
python -m app.cli admin bootstrap      # first superadmin from BOOTSTRAP_ADMIN_* env
python -m app.cli tenant seed-platform # tenant `muhbianco` served only at loja.muhbianco.com.br
python -m app.cli tenant disable-domain <hostname>  # take a host out of the edge (audited)
python -m app.cli outbox ping          # emits `system.ping`; its delivery proves the outbox runs
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.audit import outbox
from app.core.bootstrap import ensure_database_exists, upgrade_head
from app.core.config import settings
from app.core.exceptions import ConflictError
from app.core.hosts import InvalidHostnameError, normalize_hostname
from app.core.logging import configure_logging, get_logger
from app.core.scopes import PlatformRole
from app.identity.repository import AdminUserRepository
from app.identity.service import AdminAuthService
from app.models.base import utcnow
from app.tenancy.models import (
    DomainKind,
    DomainPurpose,
    DomainRole,
    DomainStatus,
    TenantDomain,
    TenantStatus,
)
from app.tenancy.orm_filter import register_tenant_filter
from app.tenancy.service import Actor, TenantService

logger = get_logger("cli")


def _session_factory(url: str) -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(url)
    return async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


async def admin_bootstrap(email: str, password: str, full_name: str) -> int:
    factory = _session_factory(settings.database_url)
    async with factory() as session:
        repo = AdminUserRepository(session)
        if await repo.get_by_email(email):
            logger.info("Bootstrap admin already exists", extra={"email": email})
            return 0
        if len(password) < 12:
            logger.error("Bootstrap admin password must have at least 12 characters")
            return 2
        await AdminAuthService(session).create_user(
            email=email,
            full_name=full_name,
            password=password,
            platform_role=PlatformRole.SUPERADMIN,
            actor="system:cli",
        )
        await session.commit()
    logger.info("Bootstrap admin created", extra={"email": email})
    return 0


async def _seed_platform_in_session(session: AsyncSession) -> None:
    service = TenantService(session)
    tenant = await service.repo.get_by_slug(settings.platform_tenant_slug)
    if tenant is None:
        tenant = await service.create(
            slug=settings.platform_tenant_slug,
            name="MuhBianco",
            actor=Actor.system("cli"),
            plan="platform",
        )
        tenant.status = TenantStatus.ACTIVE
        tenant.activated_at = utcnow()
    if await service.repo.get_domain_by_hostname(settings.platform_base_domain) is None:
        current_primary = await service.repo.primary_domain(tenant.id)
        if current_primary is not None:
            current_primary.role = DomainRole.ALIAS
        session.add(
            TenantDomain(
                tenant_id=tenant.id,
                hostname=settings.platform_base_domain,
                kind=DomainKind.CUSTOM_SUBDOMAIN,
                purpose=DomainPurpose.STOREFRONT,
                role=DomainRole.PRIMARY,
                status=DomainStatus.ACTIVE,
                verified_at=utcnow(),
            )
        )
        await session.flush()
    # `create` also gave the tenant `{slug}.{base}`; the loja modelo lives only at the base
    # domain, and without wildcard DNS that host would just fail ACME in the edge.
    platform_subdomain = await service.repo.get_domain_by_hostname(
        f"{tenant.slug}.{settings.platform_base_domain}"
    )
    if platform_subdomain is not None and platform_subdomain.status != DomainStatus.DISABLED:
        await service.disable_domain(tenant, platform_subdomain, Actor.system("cli"))


async def seed_platform_tenant(session: AsyncSession | None = None) -> int:
    if session is not None:
        await _seed_platform_in_session(session)
        await session.commit()
    else:
        factory = _session_factory(settings.database_url)
        async with factory() as owned:
            await _seed_platform_in_session(owned)
            await owned.commit()
    logger.info("Platform tenant ready", extra={"slug": settings.platform_tenant_slug})
    return 0


async def disable_domain(hostname: str, session: AsyncSession | None = None) -> int:
    """Idempotent: disabling an already disabled host is a no-op."""
    try:
        host = normalize_hostname(hostname)
    except InvalidHostnameError:
        logger.error("Invalid hostname", extra={"hostname": hostname})
        return 2
    if session is None:
        async with _session_factory(settings.database_url)() as owned:
            code = await _disable_domain_in_session(owned, host)
            await owned.commit()
            return code
    code = await _disable_domain_in_session(session, host)
    await session.commit()
    return code


async def _disable_domain_in_session(session: AsyncSession, host: str) -> int:
    service = TenantService(session)
    domain = await service.repo.get_domain_by_hostname(host)
    if domain is None:
        logger.error("Domain not found", extra={"hostname": host})
        return 1
    if domain.status == DomainStatus.DISABLED:
        logger.info("Domain already disabled", extra={"hostname": host})
        return 0
    tenant = await service.get_or_404(domain.tenant_id)
    try:
        await service.disable_domain(tenant, domain, Actor.system("cli"))
    except ConflictError as exc:
        logger.error("Domain not disabled", extra={"hostname": host, "reason": exc.message})
        return 3
    logger.info("Domain disabled", extra={"hostname": host, "tenant": tenant.slug})
    return 0


async def outbox_ping() -> int:
    """Emit a no-op event; audit_projector consumes it (outbox_deliveries + processed_events)."""
    factory = _session_factory(settings.database_url)
    async with factory() as session:
        event = await outbox.emit(
            session,
            aggregate_type="system",
            aggregate_id="ping",
            event_type="system.ping",
            payload={"emitted_at": utcnow().isoformat()},
            tenant_id=None,
        )
        await session.commit()
    logger.info("Outbox ping emitted", extra={"event_id": event.id})
    return 0


def main(argv: list[str] | None = None) -> int:
    configure_logging(settings.log_level)
    register_tenant_filter()

    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="group", required=True)

    db = sub.add_parser("db").add_subparsers(dest="cmd", required=True)
    db.add_parser("ensure")
    db.add_parser("upgrade")

    admin = sub.add_parser("admin").add_subparsers(dest="cmd", required=True)
    bootstrap = admin.add_parser("bootstrap")
    bootstrap.add_argument("--email", default=settings.bootstrap_admin_email)
    bootstrap.add_argument(
        "--password", default=settings.bootstrap_admin_password.get_secret_value()
    )
    bootstrap.add_argument("--name", default="MuhBianco Ops")

    tenant = sub.add_parser("tenant").add_subparsers(dest="cmd", required=True)
    tenant.add_parser("seed-platform")
    disable = tenant.add_parser("disable-domain")
    disable.add_argument("hostname")

    outbox_cmd = sub.add_parser("outbox").add_subparsers(dest="cmd", required=True)
    outbox_cmd.add_parser("ping")

    args = parser.parse_args(argv)

    if args.group == "db" and args.cmd == "ensure":
        asyncio.run(ensure_database_exists())
        return 0
    if args.group == "db" and args.cmd == "upgrade":
        upgrade_head()
        return 0
    if args.group == "admin" and args.cmd == "bootstrap":
        if not args.email or not args.password:
            logger.error("Provide --email/--password or BOOTSTRAP_ADMIN_EMAIL/PASSWORD")
            return 2
        return asyncio.run(admin_bootstrap(args.email, args.password, args.name))
    if args.group == "tenant" and args.cmd == "seed-platform":
        return asyncio.run(seed_platform_tenant())
    if args.group == "tenant" and args.cmd == "disable-domain":
        return asyncio.run(disable_domain(args.hostname))
    if args.group == "outbox" and args.cmd == "ping":
        return asyncio.run(outbox_ping())
    parser.error("unknown command")


if __name__ == "__main__":
    sys.exit(main())
