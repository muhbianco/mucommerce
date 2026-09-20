"""Import every model so `Base.metadata` is complete (Alembic, tests, create_all)."""

from app.audit.models import (
    AuditLog,
    IdempotencyKey,
    OutboxDelivery,
    OutboxEvent,
    ProcessedEvent,
)
from app.identity.models import (
    AdminRefreshToken,
    AdminUser,
    Customer,
    CustomerIdentity,
    CustomerSession,
    CustomerTenantAccess,
    TenantMembership,
)
from app.models.base import Base
from app.tenancy.models import (
    Tenant,
    TenantDomain,
    TenantFeatureFlag,
    TenantIntegrationCredential,
    TenantSequence,
    TenantSetting,
)

__all__ = [
    "AdminRefreshToken",
    "AdminUser",
    "AuditLog",
    "Base",
    "Customer",
    "CustomerIdentity",
    "CustomerSession",
    "CustomerTenantAccess",
    "IdempotencyKey",
    "OutboxDelivery",
    "OutboxEvent",
    "ProcessedEvent",
    "Tenant",
    "TenantDomain",
    "TenantFeatureFlag",
    "TenantIntegrationCredential",
    "TenantMembership",
    "TenantSequence",
    "TenantSetting",
]
