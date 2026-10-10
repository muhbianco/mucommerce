"""Emissão de ingressos: acontece quando o dinheiro entra, e só uma vez.

Pendurado no pagamento, não no fechamento do pedido: pedido com Pix pendente geraria ingresso
que depois teria de ser desfeito, e ingresso desfeito é o tipo de coisa que já foi parar no
celular de alguém.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.models import Event, ProductKind, Ticketing
from app.core.logging import get_logger
from app.models.base import utcnow
from app.orders.models import Order, OrderItem
from app.tickets.models import Ticket, TicketStatus

logger = get_logger(__name__)

#: Sem `O`/`0` e sem `I`/`1`: o código é ditado por telefone e lido de um papel amassado na
#: porta de um show. Ambiguidade visual aqui custa uma pessoa parada na fila.
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_CODE_LENGTH = 10
#: Tentativas antes de desistir de achar código livre. Com 32^10 (~10^15) combinações, duas
#: colisões seguidas significam que algo está errado — não que o espaço acabou.
_CODE_ATTEMPTS = 8


def mint_code() -> str:
    """`ING-XXXXXXXXXX` de `secrets`: imprevisível, não sequencial, sem id de pedido dentro.

    Quem vê um código não adivinha o próximo nem conta quantos ingressos a loja vendeu. Não é
    senha — é identificador. Quem valida precisa estar autenticado (ADR 0020, decisão 4).
    """
    corpo = "".join(secrets.choice(_ALPHABET) for _ in range(_CODE_LENGTH))
    return f"ING-{corpo}"


class TicketService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def issue_for_order(self, order: Order) -> list[Ticket]:
        """Emite o que falta para este pedido. Chamar de novo não emite de novo.

        A idempotência é da tabela, não de um `if` antes: a única `(tenant, order_item, seq)`
        barra a segunda inserção mesmo se dois webhooks de pagamento chegarem ao mesmo tempo.
        Aqui a gente só evita o trabalho quando dá para ver que já foi feito.
        """
        itens = (
            await self.session.execute(
                select(OrderItem).where(
                    OrderItem.order_id == order.id,
                    OrderItem.product_kind == ProductKind.TICKET.value,
                )
            )
        ).scalars()
        emitidos: list[Ticket] = []
        for item in itens:
            emitidos.extend(await self._issue_item(order, item))
        if emitidos:
            await self.session.flush()
            logger.info(
                "Ingressos emitidos",
                extra={"order_id": order.id, "quantidade": len(emitidos)},
            )
        return emitidos

    async def _issue_item(self, order: Order, item: OrderItem) -> list[Ticket]:
        evento = item.event if isinstance(item.event, dict) else None
        if not evento:
            # Linha de ingresso sem o retrato do evento é pedido antigo ou dado torto: não dá
            # para emitir um ingresso que não sabe para que evento vale.
            logger.warning(
                "Linha de ingresso sem evento; nada emitido",
                extra={"order_id": order.id, "order_item_id": item.id},
            )
            return []
        if not await self._is_mutickets(str(evento.get("event_id") or "")):
            return []
        ja = set(
            (await self.session.execute(select(Ticket.seq).where(Ticket.order_item_id == item.id)))
            .scalars()
            .all()
        )
        # Quantidade vem em milésimos porque há produto vendido por peso; ingresso é por
        # unidade, e meio ingresso não existe.
        quantidade = max(0, item.quantity_milli // 1000)
        novos: list[Ticket] = []
        for seq in range(1, quantidade + 1):
            if seq in ja:
                continue
            novos.append(
                Ticket(
                    order_id=order.id,
                    order_item_id=item.id,
                    seq=seq,
                    code=await self._free_code(),
                    event_id=str(evento.get("event_id") or ""),
                    lot_id=str(evento.get("lot_id") or ""),
                    # Congelados: o ingresso vale pelo que estava escrito nele, mesmo que a
                    # loja renomeie o lote depois.
                    event_name=str(item.product_name or "")[:200],
                    lot_name=str(evento.get("lot_name") or item.variant_name or "")[:200],
                    status=TicketStatus.ISSUED,
                )
            )
        for ticket in novos:
            self.session.add(ticket)
        return novos

    async def _is_mutickets(self, event_id: str) -> bool:
        if not event_id:
            return False
        evento = (
            await self.session.execute(select(Event).where(Event.id == event_id))
        ).scalar_one_or_none()
        return evento is not None and evento.ticketing == Ticketing.MUTICKETS

    async def _free_code(self) -> str:
        for _ in range(_CODE_ATTEMPTS):
            code = mint_code()
            taken = (
                await self.session.execute(select(Ticket.id).where(Ticket.code == code).limit(1))
            ).first()
            if taken is None:
                return code
        # Não inventa um código "provavelmente livre": deixa o erro aparecer. Colisão repetida
        # em 10^15 combinações é sintoma, não azar.
        raise RuntimeError("Não foi possível gerar código de ingresso livre.")


def ticket_view(ticket: Ticket) -> dict[str, Any]:
    return {
        "code": ticket.code,
        "status": ticket.status,
        "event_name": ticket.event_name,
        "lot_name": ticket.lot_name,
        "holder_name": ticket.holder_name,
        "used_at": ticket.used_at.isoformat() if ticket.used_at else None,
    }


@dataclass(frozen=True, slots=True)
class Validation:
    """O que a porta vê depois de encostar o leitor.

    `admitted` é a única coisa que decide se a pessoa entra. O resto existe para a tela dizer
    *por que* não, que é o que evita discussão na entrada: "já usado às 20:14" encerra o
    assunto; "inválido" começa um.
    """

    admitted: bool
    reason: str
    ticket: Ticket | None = None


class TicketValidator:
    """Queima o ingresso uma vez, e conta quem queimou.

    Quem valida é a porta autenticada, não o cliente (ADR 0020, decisão 2): deixar o próprio
    comprador marcar entrada significa que ele marca em casa, e que uma captura de tela verde
    serve para qualquer um.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def validate(self, code: str, *, actor_id: str) -> Validation:
        limpo = (code or "").strip().upper()
        if not limpo:
            return Validation(admitted=False, reason="not_found")
        agora = utcnow()
        # Um `UPDATE ... WHERE status='issued'` decide tudo: duas leituras simultâneas do mesmo
        # QR (dois leitores, ou o dedo escorregando) não podem **ambas** admitir. Quem não
        # alterou linha nenhuma perdeu a corrida e vai ler o motivo abaixo.
        resultado = await self.session.execute(
            update(Ticket)
            .where(Ticket.code == limpo, Ticket.status == TicketStatus.ISSUED)
            .values(status=TicketStatus.USED, used_at=agora, used_by=actor_id)
            .execution_options(synchronize_session=False)
        )
        ticket = (
            await self.session.execute(select(Ticket).where(Ticket.code == limpo))
        ).scalar_one_or_none()
        if ticket is None:
            # Código inexistente **nesta loja**: o filtro de tenant é automático, então
            # ingresso do show do vizinho é indistinguível de código inventado. É o certo.
            logger.info("Ingresso não encontrado na validação", extra={"code": limpo})
            return Validation(admitted=False, reason="not_found")
        if resultado.rowcount == 1:  # type: ignore[attr-defined]
            logger.info(
                "Entrada registrada",
                extra={"code": limpo, "event_id": ticket.event_id, "actor": actor_id},
            )
            return Validation(admitted=True, reason="ok", ticket=ticket)
        logger.info(
            "Ingresso recusado na porta",
            extra={"code": limpo, "status": ticket.status, "actor": actor_id},
        )
        return Validation(admitted=False, reason=str(ticket.status), ticket=ticket)
