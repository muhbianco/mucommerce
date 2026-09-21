"""Operational CLI. Runs in the `commerce_migrate` job and by hand.

python -m app.cli db ensure            # CREATE DATABASE IF NOT EXISTS (migration user)
python -m app.cli db upgrade           # alembic upgrade head (migration user)
python -m app.cli admin bootstrap --email E [--password-stdin]   # first superadmin
python -m app.cli admin grant --email E --tenant-slug S --role owner [--create --name N]
    Passwords never go in argv (ps, shell history): --password-stdin, BOOTSTRAP_ADMIN_PASSWORD
    or an interactive prompt (infra/scripts/commerce-cli.sh allocates a TTY when it has one).
python -m app.cli tenant seed-platform # tenant `muhbianco` served only at loja.muhbianco.com.br
python -m app.cli tenant disable-domain <hostname>  # take a host out of the edge (audited)
python -m app.cli outbox ping          # emits `system.ping`; its delivery proves the outbox runs
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.audit import outbox
from app.audit.writer import audit
from app.core.bootstrap import ensure_database_exists, upgrade_head
from app.core.config import settings
from app.core.exceptions import ConflictError
from app.core.hosts import InvalidHostnameError, normalize_hostname
from app.core.logging import configure_logging, get_logger
from app.core.scopes import PlatformRole, TenantRole
from app.identity.models import TenantMembership
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


MIN_PASSWORD_LENGTH = 12


def read_password(*, from_stdin: bool) -> str | None:
    """Password from stdin (--password-stdin), BOOTSTRAP_ADMIN_PASSWORD or a prompt.

    Never from argv: arguments show up in `ps` and in shell history.
    """
    if from_stdin:
        return sys.stdin.readline().rstrip("\r\n") or None
    from_env = settings.bootstrap_admin_password.get_secret_value()
    if from_env:
        return from_env
    if sys.stdin.isatty():
        first = getpass.getpass("Senha: ")
        if first != getpass.getpass("Repita a senha: "):
            logger.error("Passwords do not match")
            return None
        return first
    return None


def _weak(password: str | None) -> bool:
    if not password or len(password) < MIN_PASSWORD_LENGTH:
        logger.error(
            "Admin password missing or shorter than the minimum",
            extra={"min_length": MIN_PASSWORD_LENGTH},
        )
        return True
    return False


async def admin_bootstrap(
    email: str, password: str | None, full_name: str, session: AsyncSession | None = None
) -> int:
    """First platform superadmin. Idempotent: an existing e-mail is left untouched."""
    if session is None:
        async with _session_factory(settings.database_url)() as owned:
            return await admin_bootstrap(email, password, full_name, owned)
    email = email.strip().lower()
    if await AdminUserRepository(session).get_by_email(email):
        logger.info("Bootstrap admin already exists", extra={"email": email})
        return 0
    if _weak(password):
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


async def admin_grant(
    *,
    email: str,
    tenant_slug: str,
    role: str,
    create: bool = False,
    full_name: str = "",
    password: str | None = None,
    session: AsyncSession | None = None,
) -> int:
    """Give an admin user a role in a tenant (creating the user with --create). Idempotent."""
    if session is None:
        async with _session_factory(settings.database_url)() as owned:
            return await admin_grant(
                email=email,
                tenant_slug=tenant_slug,
                role=role,
                create=create,
                full_name=full_name,
                password=password,
                session=owned,
            )
    email = email.strip().lower()
    try:
        tenant_role = TenantRole(role)
    except ValueError:
        logger.error("Unknown tenant role", extra={"role": role})
        return 2
    tenant = await TenantService(session).repo.get_by_slug(tenant_slug)
    if tenant is None:
        logger.error("Tenant not found", extra={"slug": tenant_slug})
        return 1
    auth = AdminAuthService(session)
    user = await AdminUserRepository(session).get_by_email(email)
    if user is None:
        if not create:
            logger.error("Admin user not found; pass --create", extra={"email": email})
            return 1
        if _weak(password):
            return 2
        user = await auth.create_user(
            email=email,
            full_name=full_name or email,
            password=password,
            platform_role=None,
            actor="system:cli",
        )
    membership = (
        await session.execute(
            select(TenantMembership)
            .where(TenantMembership.tenant_id == tenant.id)
            .where(TenantMembership.admin_user_id == user.id)
        )
    ).scalar_one_or_none()
    if membership is None:
        await auth.add_membership(
            user=user, tenant_id=tenant.id, role=tenant_role, actor="system:cli"
        )
    elif membership.role != tenant_role or membership.status != "active":
        before = {"role": membership.role, "status": membership.status}
        membership.role = tenant_role
        membership.status = "active"
        await audit(
            session,
            actor="system:cli",
            action="membership.updated",
            entity_type="tenant_membership",
            entity_id=membership.id,
            tenant_id=tenant.id,
            before=before,
            after={"role": str(tenant_role), "status": "active"},
        )
    else:
        logger.info("Membership already in place", extra={"email": email, "slug": tenant_slug})
        return 0
    await session.commit()
    logger.info(
        "Membership granted",
        extra={"email": email, "slug": tenant_slug, "role": str(tenant_role)},
    )
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
    bootstrap.add_argument("--name", default="MuhBianco Ops")
    bootstrap.add_argument("--password-stdin", action="store_true")
    grant = admin.add_parser("grant")
    grant.add_argument("--email", required=True)
    grant.add_argument("--tenant-slug", required=True)
    grant.add_argument("--role", required=True, choices=[str(r) for r in TenantRole])
    grant.add_argument("--create", action="store_true", help="create the admin user if missing")
    grant.add_argument("--name", default="")
    grant.add_argument("--password-stdin", action="store_true")

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
        if not args.email:
            logger.error("Provide --email or BOOTSTRAP_ADMIN_EMAIL")
            return 2
        password = read_password(from_stdin=args.password_stdin)
        return asyncio.run(admin_bootstrap(args.email, password, args.name))
    if args.group == "admin" and args.cmd == "grant":
        password = read_password(from_stdin=args.password_stdin) if args.create else None
        return asyncio.run(
            admin_grant(
                email=args.email,
                tenant_slug=args.tenant_slug,
                role=args.role,
                create=args.create,
                full_name=args.name,
                password=password,
            )
        )
    if args.group == "tenant" and args.cmd == "seed-platform":
        return asyncio.run(seed_platform_tenant())
    if args.group == "tenant" and args.cmd == "disable-domain":
        return asyncio.run(disable_domain(args.hostname))
    if args.group == "outbox" and args.cmd == "ping":
        return asyncio.run(outbox_ping())
    parser.error("unknown command")


if __name__ == "__main__":
    sys.exit(main())
