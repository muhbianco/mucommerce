"""Import every model so `Base.metadata` is complete (Alembic, tests, create_all)."""

from app.audit.models import (
    AuditLog,
    IdempotencyKey,
    OutboxDelivery,
    OutboxEvent,
    ProcessedEvent,
)
from app.catalog.models import Category, Product, ProductCategory, ProductVariant
from app.customers.legal_models import Consent, LegalDocument
from app.customers.models import CustomerAuthFlow, CustomerPhoneChallenge
from app.identity.models import (
    AdminRefreshToken,
    AdminUser,
    Customer,
    CustomerIdentity,
    CustomerSession,
    CustomerTenantAccess,
    TenantMembership,
)
from app.inventory.models import InventoryBalance, InventoryMovement, StockAdjustment
from app.media.models import MediaAsset
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
    "Category",
    "Consent",
    "Customer",
    "CustomerAuthFlow",
    "CustomerIdentity",
    "CustomerPhoneChallenge",
    "CustomerSession",
    "CustomerTenantAccess",
    "IdempotencyKey",
    "InventoryBalance",
    "InventoryMovement",
    "LegalDocument",
    "MediaAsset",
    "OutboxDelivery",
    "OutboxEvent",
    "ProcessedEvent",
    "Product",
    "ProductCategory",
    "ProductVariant",
    "StockAdjustment",
    "Tenant",
    "TenantDomain",
    "TenantFeatureFlag",
    "TenantIntegrationCredential",
    "TenantMembership",
    "TenantSequence",
    "TenantSetting",
]
