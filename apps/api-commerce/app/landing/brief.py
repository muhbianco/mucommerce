"""Ler e gravar o retrato que a lojista faz do próprio negócio.

Duas decisões moram aqui, e as duas existem por causa da tela.

**A gravação é parcial.** O questionário são quatro passos curtos, cada um salvando sozinho; um
`PUT` que exigisse o brief inteiro faria o passo 2 apagar o que o passo 1 acabou de guardar. O
serviço funde o que chega sobre o que já está gravado, e **só nas chaves que chegaram** — `None`
explícito limpa o campo, chave ausente não mexe nele. Esse é o contrato que deixa a tela
mandar `{"city": "Contagem"}` sem carregar o resto.

**Ler nunca falha por causa de dado velho.** O brief é texto que a loja escreveu, não setting
crítica: se um campo saiu do `Literal` ou o formulário gravou algo que a versão nova recusa, a
resposta é devolver o que dá e ignorar o resto, porque a alternativa é a loja ficar trancada
fora do próprio questionário. É diferente de `tenant_settings`, onde um valor inválido precisa
gritar — lá ele muda o que o cliente vê.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.landing.models import LandingBrief
from app.landing.schemas import BriefV1
from app.tenancy.context import TenantContext
from app.tenancy.service import Actor

#: Os campos do primeiro passo. Progresso na tela é quantos passos têm alguma coisa dentro,
#: não quantos campos foram preenchidos: contar campo faria "3 de 4" andar sozinho quando a
#: lojista respondesse dois de um passo só.
STEP_FIELDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("negocio", ("segment", "segment_other", "sells")),
    ("publico", ("audience", "differentials", "voice")),
    (
        "onde",
        (
            "city",
            "state",
            "neighborhood",
            "serves",
            "hours_note",
            # Os contatos moram aqui porque é a tela de "onde me achar", e porque são eles que o
            # bloco de contato precisa: sem eles o gerador escreve "fale com a gente" sem dizer
            # como.
            "whatsapp_e164",
            "instagram",
            "email",
        ),
    ),
    ("jeito", ("keywords", "avoid", "references", "notes")),
)
#: Os passos, na ordem em que a tela os mostra.
STEP_KEYS: tuple[str, ...] = tuple(nome for nome, _ in STEP_FIELDS)


def parse_brief(data: dict[str, Any]) -> BriefV1:
    """O brief gravado, tolerante a campo que não valida mais.

    Um `Literal` que perdeu um valor, ou um texto que ficou acima do limite novo, não pode
    trancar a lojista fora do questionário dela. Então o campo problemático volta ao padrão e o
    resto continua.
    """
    try:
        return BriefV1.model_validate(data)
    except ValidationError as erro:
        recusados = {str(detalhe["loc"][0]) for detalhe in erro.errors() if detalhe.get("loc")}
        return BriefV1.model_validate(
            {chave: valor for chave, valor in data.items() if chave not in recusados}
        )


def filled_steps(brief: BriefV1) -> tuple[str, ...]:
    """Quais passos já têm alguma resposta. Alimenta o "3 de 4" acima do formulário."""
    dados = brief.model_dump(exclude_defaults=True)
    return tuple(nome for nome, campos in STEP_FIELDS if any(dados.get(campo) for campo in campos))


class LandingBriefService:
    def __init__(self, session: AsyncSession, tenant: TenantContext) -> None:
        self.session = session
        self.tenant = tenant

    async def read(self) -> BriefV1:
        """O brief de agora. Loja que nunca respondeu nada recebe o brief vazio, não 404 — a
        tela é a mesma nos dois casos, e um 404 só a obrigaria a tratar o vazio duas vezes."""
        row = await self._row()
        return parse_brief(row.data) if row else BriefV1()

    async def patch(self, patch: dict[str, Any], actor: Actor) -> BriefV1:
        """Funde o que chegou sobre o que está gravado e valida o resultado inteiro.

        Validar só o pedaço que chegou deixaria passar combinação inválida entre campos — hoje
        não há nenhuma, mas a primeira que aparecer (`segment_other` sem `segment="outro"`, por
        exemplo) tem de ser pega aqui, não na leitura.
        """
        row = await self._row(for_update=True)
        atual = parse_brief(row.data).model_dump(mode="json") if row else {}
        atual.update(patch)
        brief = BriefV1.model_validate(atual)
        data = brief.model_dump(mode="json")

        if row is None:
            row = LandingBrief(
                tenant_id=self.tenant.id,
                data=data,
                created_by_actor=actor.id,
                updated_by_actor=actor.id,
            )
            self.session.add(row)
            try:
                await self.session.flush()
            except IntegrityError:
                # Duas abas abrindo o questionário ao mesmo tempo; a segunda encontra a linha da
                # primeira em vez de estourar, e escreve em cima.
                await self.session.rollback()
                row = await self._row(for_update=True)
                if row is None:  # pragma: no cover - só se a linha sumir entre as duas idas
                    raise
                row.data = data
                row.updated_by_actor = actor.id
                await self.session.flush()
        else:
            row.data = data
            row.updated_by_actor = actor.id
            await self.session.flush()
        return brief

    async def _row(self, *, for_update: bool = False) -> LandingBrief | None:
        stmt = select(LandingBrief)
        if for_update:
            stmt = stmt.with_for_update()
        return (await self.session.execute(stmt)).scalar_one_or_none()
