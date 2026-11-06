"""O que a loja conta sobre si, as propostas de vitrine e a conta do que já foi usado."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, ForeignKeyConstraint, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    ActorStampMixin,
    Base,
    TenantScoped,
    TimestampMixin,
    UtcDateTime,
    UUIDPrimaryKeyMixin,
)


class LandingBrief(UUIDPrimaryKeyMixin, TimestampMixin, ActorStampMixin, TenantScoped, Base):
    """O que a lojista conta sobre o negócio dela.

    Mora em tabela, e não numa chave de `tenant_settings`, por uma razão medida: o resolvedor de
    tenant carrega **todas** as linhas de settings em `TenantContext.settings` e as guarda no
    cache por hostname. Um brief com alguns quilos de texto livre seria desserializado em toda
    requisição da vitrine, para benefício zero — fora que setting inteira vai para o log de
    auditoria a cada escrita, ida e volta.

    Uma linha por loja. O histórico que interessa não é o do brief: é o `brief_snapshot` dentro
    de cada rascunho, que deixa a proposta explicar a si mesma.
    """

    __tablename__ = "landing_briefs"
    __table_args__ = (
        # Uma por loja: o brief é o retrato de agora, não uma coleção.
        UniqueConstraint("tenant_id", name="uq_landing_briefs_tenant"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_landing_briefs_tenant"),
    )

    #: `BriefV1` inteiro. JSON porque o formato é do domínio da vitrine, não do banco: crescer o
    #: questionário não pode virar migração.
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class DraftStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    READY = "ready"
    FAILED = "failed"
    APPLIED = "applied"
    DISCARDED = "discarded"
    #: Foi publicada e depois desfeita: o que estava no ar antes dela voltou.
    REVERTED = "reverted"


class DraftSource(StrEnum):
    #: Proposta montada a partir do brief.
    LLM = "llm"
    #: Reescrita de uma proposta anterior, a partir de uma instrução curta.
    REFINE = "refine"


class LandingDraft(UUIDPrimaryKeyMixin, TimestampMixin, ActorStampMixin, TenantScoped, Base):
    """Uma proposta de página inicial, esperando a lojista decidir.

    **O modelo nunca escreve na loja.** O que ele produz para aqui; publicar é a lojista
    apertando um botão, e a publicação passa pela mesma porta da edição à mão — validação,
    conferência de referências, auditoria.

    Os `*_snapshot` congelam o que o modelo viu. Sem eles, uma proposta de ontem seria explicada
    pelo brief de hoje, e "por que ele escreveu isso?" viraria adivinhação.
    """

    __tablename__ = "landing_drafts"
    __table_args__ = (
        Index("ix_landing_drafts_tenant_status", "tenant_id", "status", "created_at"),
        # O varredor de travados cruza as lojas: procura `running` velho em qualquer uma.
        Index("ix_landing_drafts_status_updated", "status", "updated_at"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_landing_drafts_tenant"),
    )

    status: Mapped[str] = mapped_column(String(16), nullable=False, default=DraftStatus.QUEUED)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default=DraftSource.LLM)
    #: De qual proposta esta nasceu, quando é um refinamento.
    parent_draft_id: Mapped[str | None] = mapped_column(String(36))
    #: A instrução curta que pediu o refinamento ("mais curto", "mais sério").
    instruction: Mapped[str | None] = mapped_column(String(200))

    brief_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    #: As imagens e o catálogo que o modelo tinha à disposição.
    inventory_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    #: Os blocos propostos, no formato de `LandingV1`. Nulo até a proposta ficar pronta.
    blocks: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    #: Cores sugeridas junto, quando houve logotipo para extrair.
    branding_suggestion: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    #: A marca proposta (só as chaves que a IA pode mudar — `design.AI_BRANDING_KEYS`). Nulo em
    #: proposta anterior ao motor de identidade, que só tinha blocos.
    branding: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    #: O desenho por área proposto (`DesignV1`).
    design: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    #: O que a lojista lê: explicação, decisões e a lista do que muda contra o que está no ar.
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    #: O perfil da marca que a proposta usou.
    profile_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    #: O que estava no ar quando foi publicada (`branding`, `design`, `landing`). É o restaurar.
    previous_settings: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    model: Mapped[str | None] = mapped_column(String(64))
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: A saída precisou de conserto determinístico (id inventado, lista comprida demais).
    repaired: Mapped[bool] = mapped_column(nullable=False, default=False)
    failure_reason: Mapped[str | None] = mapped_column(String(300))
    #: Mês que esta proposta consumiu, para a devolução da cota achar a linha certa.
    quota_period: Mapped[str] = mapped_column(String(7), nullable=False)
    applied_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


class LandingGenerationUsage(UUIDPrimaryKeyMixin, TimestampMixin, TenantScoped, Base):
    """Quantas propostas a loja já pediu neste mês.

    Tabela própria, e não um `COUNT(*)` sobre os rascunhos: rascunho velho é podado, e contar
    linha impediria devolver a unidade quando a geração falha. Também não vai para o JSON da
    flag de módulo — JSON não incrementa sob concorrência.

    A reserva acontece **antes** da chamada ao modelo. Cobrar depois é como se descobre, no fim
    do mês, que o limite nunca segurou nada.
    """

    __tablename__ = "landing_generation_usage"
    __table_args__ = (
        UniqueConstraint("tenant_id", "period", name="uq_landing_usage_tenant_period"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_landing_usage_tenant"),
    )

    #: "AAAA-MM", em UTC.
    period: Mapped[str] = mapped_column(String(7), nullable=False)
    used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class BrandProfile(UUIDPrimaryKeyMixin, TimestampMixin, ActorStampMixin, TenantScoped, Base):
    """Como a marca se expressa, e o que cada arquivo da loja disse sobre isso.

    Uma linha por loja: o perfil é o retrato de agora. O histórico que interessa mora em
    `landing_drafts.profile_snapshot` — cada proposta guarda o perfil que usou.

    É cache, não fonte da verdade: se um dia não validar mais, o gerador refaz a partir dos
    arquivos. Por isso não há migração de dado para ele.
    """

    __tablename__ = "brand_profiles"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_brand_profiles_tenant"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_brand_profiles_tenant"),
    )

    #: `BrandProfileV1`.
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    #: `{media_id: {checksum, kind, relevance, description, usable_on_page, accents, ...}}`.
    materials: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    #: O brief que o perfil considerou. Mudou, o perfil é revisto.
    brief_digest: Mapped[str | None] = mapped_column(String(64))
    #: Sobe a cada revisão. A proposta guarda qual usou.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: `llm` (o modelo olhou as imagens) ou `deterministic` (Pillow + brief, sem modelo).
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="deterministic")
    model: Mapped[str | None] = mapped_column(String(64))
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: O que a última revisão mudou e as contradições achadas, em frases curtas.
    changes: Mapped[dict[str, Any] | None] = mapped_column(JSON)
