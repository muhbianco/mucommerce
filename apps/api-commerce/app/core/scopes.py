from __future__ import annotations

from enum import StrEnum


class PlatformRole(StrEnum):
    """MuhBianco staff roles. Stored in `admin_users.platform_role`; empty for tenant staff."""

    SUPERADMIN = "superadmin"
    OPERATOR = "operator"


class TenantRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    OPS = "ops"
    SUPPORT = "support"


class Scope(StrEnum):
    CATALOG_READ = "catalog:read"
    CATALOG_WRITE = "catalog:write"
    CATALOG_PUBLISH = "catalog:publish"
    MEDIA_WRITE = "media:write"
    INVENTORY_READ = "inventory:read"
    INVENTORY_ADJUST = "inventory:adjust"
    MANUFACTURING_READ = "manufacturing:read"
    MANUFACTURING_WRITE = "manufacturing:write"
    ORDERS_READ = "orders:read"
    ORDERS_WRITE = "orders:write"
    ORDERS_TRANSITION = "orders:transition"
    ORDERS_CANCEL = "orders:cancel"
    PAYMENTS_READ = "payments:read"
    PAYMENTS_REFUND_REQUEST = "payments:refund_request"
    PAYMENTS_REFUND_APPROVE = "payments:refund_approve"
    PAYMENTS_CONFIG = "payments:config"
    CUSTOMERS_READ = "customers:read"
    CUSTOMERS_APPROVE = "customers:approve"
    CUSTOMERS_EXPORT = "customers:export"
    CUSTOMERS_ERASE = "customers:erase"
    EVENTS_WRITE = "events:write"
    SETTINGS_WRITE = "settings:write"
    DOMAINS_WRITE = "domains:write"
    MEMBERS_WRITE = "members:write"
    NOTIFICATIONS_READ = "notifications:read"
    NOTIFICATIONS_WRITE = "notifications:write"
    AUDIT_READ = "audit:read"
    REPORTS_READ = "reports:read"


_ALL_TENANT_SCOPES = frozenset(Scope)

_TENANT_ROLE_SCOPES: dict[TenantRole, frozenset[Scope]] = {
    TenantRole.OWNER: _ALL_TENANT_SCOPES,
    # Payment credentials are the owner's alone (ADR 0011 §8).
    TenantRole.ADMIN: _ALL_TENANT_SCOPES
    - {Scope.CUSTOMERS_ERASE, Scope.MEMBERS_WRITE, Scope.PAYMENTS_CONFIG},
    TenantRole.OPS: frozenset(
        {
            Scope.CATALOG_READ,
            Scope.CATALOG_WRITE,
            Scope.MEDIA_WRITE,
            Scope.INVENTORY_READ,
            Scope.INVENTORY_ADJUST,
            Scope.MANUFACTURING_READ,
            Scope.MANUFACTURING_WRITE,
            Scope.ORDERS_READ,
            Scope.ORDERS_WRITE,
            Scope.ORDERS_TRANSITION,
            Scope.CUSTOMERS_READ,
            Scope.NOTIFICATIONS_READ,
        }
    ),
    TenantRole.SUPPORT: frozenset(
        {
            Scope.CATALOG_READ,
            Scope.ORDERS_READ,
            Scope.ORDERS_WRITE,
            Scope.CUSTOMERS_READ,
            Scope.CUSTOMERS_APPROVE,
            Scope.NOTIFICATIONS_READ,
            Scope.NOTIFICATIONS_WRITE,
        }
    ),
}


def scopes_for_tenant_role(role: TenantRole | str) -> frozenset[Scope]:
    return _TENANT_ROLE_SCOPES[TenantRole(role)]


def platform_role_covers(actual: PlatformRole | str | None, required: PlatformRole) -> bool:
    """Superadmin covers operator. Tenant staff (None) cover nothing."""
    if actual is None or actual == "":
        return False
    actual_role = PlatformRole(actual)
    if actual_role is PlatformRole.SUPERADMIN:
        return True
    return actual_role is required
