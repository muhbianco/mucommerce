from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import ForeignKey, ForeignKeyConstraint, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantScoped, TimestampMixin, UtcDateTime, UUIDPrimaryKeyMixin


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


class CustomerPhoneChallenge(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A pending "CONFIRMAR <token>" for a customer's WhatsApp (see app/customers/phone.py).

    Global: api-agents confirms it with no store in hand. The token is stored as SHA-256.
    """

    __tablename__ = "customer_phone_challenges"
    __table_args__ = (Index("ix_customer_phone_challenges_customer", "customer_id", "created_at"),)

    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id"), nullable=False, index=True
    )
    customer_id: Mapped[str] = mapped_column(String(36), ForeignKey("customers.id"), nullable=False)
    phone_e164: Mapped[str] = mapped_column(String(20), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    ip: Mapped[str | None] = mapped_column(String(45))


class CustomerPassword(UUIDPrimaryKeyMixin, TimestampMixin, TenantScoped, Base):
    """A senha que a pessoa criou **nesta loja** (docs/14-pedidos-lunares.md §2).

    Por loja, e não no `Customer` global: a senha criada na Lunares não abre outra loja, e uma
    loja não fica sabendo que o e-mail tem cadastro em outra. Só existe depois que a pessoa
    provou ser dona do e-mail (link do `CustomerEmailChallenge`): é o que impede alguém de
    amarrar uma senha ao e-mail de outra pessoa antes dela.
    """

    __tablename__ = "customer_passwords"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_customer_passwords_tenant"),
        ForeignKeyConstraint(
            ["customer_id"], ["customers.id"], name="fk_customer_passwords_customer"
        ),
        UniqueConstraint("customer_id", "tenant_id", name="uq_customer_passwords_customer"),
    )

    customer_id: Mapped[str] = mapped_column(String(36), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)


class EmailChallengePurpose(StrEnum):
    SIGNUP = "signup"
    RESET = "reset"


class CustomerEmailChallenge(UUIDPrimaryKeyMixin, TimestampMixin, TenantScoped, Base):
    """Um link mandado por e-mail: confirmar o cadastro, ou trocar a senha.

    Nenhum segredo fica aqui nem na fila de e-mail: o token do link é
    `HMAC(chave do servidor, propósito:id)`, calculado na hora de montar o e-mail e de novo na
    hora de conferir. A senha escolhida no cadastro espera aqui, já em hash, até o clique.
    """

    __tablename__ = "customer_email_challenges"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_customer_email_challenges_tenant"
        ),
        Index("ix_customer_email_challenges_email", "tenant_id", "email_normalized", "created_at"),
    )

    purpose: Mapped[str] = mapped_column(String(16), nullable=False)
    email_normalized: Mapped[str] = mapped_column(String(320), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(200))
    password_hash: Mapped[str | None] = mapped_column(String(255))
    return_to: Mapped[str] = mapped_column(String(512), nullable=False, default="/")
    terms_version: Mapped[str | None] = mapped_column(String(32))
    privacy_version: Mapped[str | None] = mapped_column(String(32))
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    ip: Mapped[str | None] = mapped_column(String(45))
