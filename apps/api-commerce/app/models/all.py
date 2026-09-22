"""Import every model so `Base.metadata` is complete (Alembic, tests, create_all)."""

from app.audit.models import (
    AuditLog,
    IdempotencyKey,
    OutboxDelivery,
    OutboxEvent,
    ProcessedEvent,
)
from app.cart.models import Cart, CartItem
from app.catalog.models import Category, Product, ProductCategory, ProductVariant
from app.customers.address_models import CustomerAddress
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
from app.inventory.models import (
    InventoryBalance,
    InventoryMovement,
    InventoryReservation,
    StockAdjustment,
)
from app.media.models import MediaAsset
from app.models.base import Base
from app.orders.models import Order, OrderItem, OrderStatusHistory
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
    "Cart",
    "CartItem",
    "Category",
    "Consent",
    "Customer",
    "CustomerAddress",
    "CustomerAuthFlow",
    "CustomerIdentity",
    "CustomerPhoneChallenge",
    "CustomerSession",
    "CustomerTenantAccess",
    "IdempotencyKey",
    "InventoryBalance",
    "InventoryMovement",
    "InventoryReservation",
    "LegalDocument",
    "MediaAsset",
    "Order",
    "OrderItem",
    "OrderStatusHistory",
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
