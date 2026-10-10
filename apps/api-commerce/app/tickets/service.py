"""Emissão de ingressos: acontece quando o dinheiro entra, e só uma vez.

Pendurado no pagamento, não no fechamento do pedido: pedido com Pix pendente geraria ingresso
que depois teria de ser desfeito, e ingresso desfeito é o tipo de coisa que já foi parar no
celular de alguém.
"""

from __future__ import annotations

import secrets
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.models import Event, ProductKind, Ticketing
from app.core.logging import get_logger
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
