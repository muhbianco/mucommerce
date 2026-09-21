from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UtcDateTime, UUIDPrimaryKeyMixin


class CustomerAuthFlow(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One Google sign-in attempt, from the store's "Entrar" to the session.

    Global, not TenantScoped: the central callback reads it by `state` before any tenant is
    known; the tenant and host it was started for are columns and are checked again on the way
    back. Secrets are stored as SHA-256 (state, nonce, browser binding, handoff code); only the
    PKCE verifier is kept as is, for the 10 minutes the flow lives. Each step is consumed with a
    conditional UPDATE, so a code or state works once even with several API workers.
    """

    __tablename__ = "customer_auth_flows"

    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id"), nullable=False, index=True
    )
    host: Mapped[str] = mapped_column(String(253), nullable=False)
    return_to: Mapped[str] = mapped_column(String(512), nullable=False, default="/")
    state_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    code_verifier: Mapped[str] = mapped_column(String(128), nullable=False)
    nonce_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Hash of a random value kept in a cookie of the browser that started the flow: the handoff
    # only completes in that same browser (login CSRF).
    binding_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    state_consumed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    customer_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("customers.id"))
    handoff_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    handoff_expires_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    handoff_consumed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    # Legal document versions shown on the "Entrar" page; recorded as consent on completion.
    terms_version: Mapped[str | None] = mapped_column(String(32))
    privacy_version: Mapped[str | None] = mapped_column(String(32))
    ip: Mapped[str | None] = mapped_column(String(45))
