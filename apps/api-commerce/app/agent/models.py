"""As tabelas do vínculo agente ↔ loja."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantScoped, TimestampMixin, UtcDateTime, UUIDPrimaryKeyMixin


class AgentLinkCode(UUIDPrimaryKeyMixin, TimestampMixin, TenantScoped, Base):
    """Código de uso único que o lojista gera para conectar um agente.

    Guarda o hash, não o código: ele é curto e ditado por mensagem, e um banco vazado não pode
    virar uma fila de agentes conectados às lojas dos outros. O prazo e o uso único é que o
    protegem de adivinhação — e o resgate ainda exige o token interno da plataforma.
    """

    __tablename__ = "agent_link_codes"
    __table_args__ = (
        UniqueConstraint("code_hash", name="uq_agent_link_codes_code_hash"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_agent_link_codes_tenant"),
    )

    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    #: `sales` (atende e vende) ou `operator` (o assistente do próprio lojista).
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    #: Como o lojista chama este agente, para reconhecê-lo na lista depois.
    label: Mapped[str | None] = mapped_column(String(80))
    created_by_actor: Mapped[str] = mapped_column(String(120), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    #: O vínculo que nasceu deste código — a trilha de "de onde veio este agente".
    link_id: Mapped[str | None] = mapped_column(String(36))


class AgentLink(UUIDPrimaryKeyMixin, TimestampMixin, TenantScoped, Base):
    """Um agente conectado a uma loja, com a credencial dele.

    A credencial é a identidade do **agente**, não a do dono: ela carrega um conjunto fixo de
    permissões (ver `app.agent.links.SCOPES`) e se revoga sozinha, sem mexer na conta de
    ninguém. Do token guardamos só o hash; ele aparece uma vez, no resgate.
    """

    __tablename__ = "agent_links"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_agent_links_token_hash"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_agent_links_tenant"),
    )

    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    label: Mapped[str | None] = mapped_column(String(80))
    #: A conta MuhBianco que resgatou. Opaca para nós; serve para o lojista reconhecer e para
    #: o histórico dizer quem agiu.
    account_ref: Mapped[str | None] = mapped_column(String(64))
    last_used_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    revoked_by_actor: Mapped[str | None] = mapped_column(String(120))

    @property
    def active(self) -> bool:
        return self.revoked_at is None
