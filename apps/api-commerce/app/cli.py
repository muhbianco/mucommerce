"""Operational CLI. Runs in the `commerce_migrate` job and by hand.

python -m app.cli db ensure            # CREATE DATABASE IF NOT EXISTS (migration user)
python -m app.cli db upgrade           # alembic upgrade head (migration user)
python -m app.cli admin bootstrap      # first superadmin from BOOTSTRAP_ADMIN_* env
python -m app.cli tenant seed-platform # tenant `muhbianco` + loja.muhbianco.com.br
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.bootstrap import ensure_database_exists, upgrade_head
from app.core.config import settings
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


async def seed_platform_tenant() -> int:
    factory = _session_factory(settings.database_url)
    async with factory() as session:
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
        await session.commit()
    logger.info("Platform tenant ready", extra={"slug": settings.platform_tenant_slug})
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
    parser.error("unknown command")


if __name__ == "__main__":
    sys.exit(main())
