"""Quando e como a loja chama a Uber (docs/15-entrega-uber-direct.md, F2).

Três passos, sempre nesta ordem, para nenhuma transação ficar aberta enquanto a Uber responde:

1. **reservar**: a linha `requested` nasce com o pedido travado (o "Pronto" pelo outbox, ou o
   operador no painel). A UNIQUE da corrida aberta impede dois entregadores;
2. **reivindicar**: o worker trava a linha, confere que o pedido continua pronto, junta o que a
   Uber precisa, marca `creating` com prazo e **grava** (commit);
3. **registrar**: com a resposta da Uber, trava a linha de novo e só escreve se a reivindicação
   ainda é dele (outro worker pode ter assumido depois do prazo).

A cotação é guardada (commit) **antes** da criação: se a resposta da criação se perder, a corrida
`del_X` da cotação `dqt_X` é procurada na Uber antes de qualquer nova tentativa, e a mesma
`idempotency_key` (o id da linha) faz a Uber recusar a duplicata (409). Corrida acima do teto
da loja não é criada: fica esperando a loja confirmar.

Falha não decide sozinha: sem chaves, sem coleta, fora do raio, sem resposta depois das
tentativas — a linha vira `failed` com o motivo e a loja escolhe chamar de novo ou levar.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.audit.writer import audit
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.courier import uber
from app.courier.models import OPEN_COURIER_STATUSES, CourierDelivery, CourierStatus
from app.models.base import utcnow
from app.orders.models import Order, OrderItem
from app.orders.state_machine import OrderStatus
from app.tenancy.context import CROSS_TENANT_OPTION, bind_session_tenant
from app.tenancy.models import Tenant
from app.tenancy.repository import TenantRepository
from app.tenancy.service import Actor
from app.tenancy.settings_schemas import DeliverySettings, fulfillment_settings

logger = get_logger(__name__)

#: Quanto um worker pode ficar com a linha antes de outro assumir (cotar + criar < 1 min).
LEASE = timedelta(minutes=2)
#: Espera entre tentativas quando a Uber não respondeu.
BACKOFF = (
    timedelta(seconds=30),
    timedelta(minutes=1),
    timedelta(minutes=3),
    timedelta(minutes=10),
)
#: Depois disto sem resposta, a loja decide (a corrida pode estar demorando demais para o pedido).
MAX_ATTEMPTS = 5
#: Tentativas anteriores mostradas no pedido.
HISTORY_LIMIT = 10
#: O varredor atende tantas linhas por volta, em todas as lojas.
BATCH = 50

CLOSED = frozenset({CourierStatus.DELIVERED, CourierStatus.FAILED, CourierStatus.CANCELLED})


class CourierInProgressError(ConflictError):
    error_code = "courier_in_progress"
    message = "Este pedido já tem uma corrida da Uber em andamento."


@dataclass(frozen=True, slots=True)
class _Claim:
    """O que o worker leva para fora da transação."""

    row_id: str
    attempt: int
    credentials: uber.UberCredentials
    token: str | None
    request: uber.DeliveryRequest
    max_fee_cents: int | None
    previous_quote: str | None


@dataclass(frozen=True, slots=True)
class _Outcome:
    info: uber.DeliveryInfo | None = None
    quote: uber.Quote | None = None
    error: uber.CourierError | None = None


def uber_ready(cfg: DeliverySettings) -> list[str]:
    """O que falta na configuração para chamar (sem olhar o cofre): `coleta`."""
    return [] if cfg.origin is not None else ["coleta"]


def _phone(raw: Any) -> str | None:
    """E.164 para a Uber. O pedido guarda só dígitos com o 55; a coleta já vem com o +."""
    digits = "".join(ch for ch in str(raw or "") if ch.isdigit())
    if len(digits) in (10, 11):
        digits = "55" + digits
    return f"+{digits}" if 12 <= len(digits) <= 15 else None


def _close(row: CourierDelivery, status: CourierStatus, now: datetime, error: str | None) -> None:
    row.status = status
    row.active_marker = None
    row.closed_at = now
    row.next_attempt_at = None
    if error is not None:
        row.last_error = error[:300]


def _apply(row: CourierDelivery, info: uber.DeliveryInfo, now: datetime) -> None:
    """O que a Uber contou sobre a corrida, na linha. Não mexe no pedido (isso é a F3)."""
    row.provider_delivery_id = info.id or row.provider_delivery_id
    row.provider_status = info.status or row.provider_status
    row.fee_cents = info.fee_cents if info.fee_cents is not None else row.fee_cents
    row.tracking_url = info.tracking_url or row.tracking_url
    row.courier_name = info.courier_name or row.courier_name
    row.courier_vehicle = info.courier_vehicle or row.courier_vehicle
    row.pickup_eta = info.pickup_eta or row.pickup_eta
    row.dropoff_eta = info.dropoff_eta or row.dropoff_eta
    row.dispatched_at = row.dispatched_at or now
    row.tracked_at = now
    if info.status in ("pickup_complete", "dropoff") and row.picked_up_at is None:
        row.picked_up_at = now
    if info.status == "delivered":
        row.delivered_at = row.delivered_at or now
        _close(row, CourierStatus.DELIVERED, now, None)
    elif info.status == "canceled":
        if row.status == CourierStatus.CANCELLING:
            _close(row, CourierStatus.CANCELLED, now, None)
        else:
            motivo = info.undeliverable_reason or "sem motivo"
            _close(row, CourierStatus.FAILED, now, f"a Uber cancelou a corrida ({motivo})")
    elif info.status == "returned":
        _close(row, CourierStatus.FAILED, now, "a corrida voltou para a loja")
    elif row.status != CourierStatus.CANCELLING:
        row.status = CourierStatus.ACTIVE
        row.next_attempt_at = None
        row.last_error = None


class CourierService:
    def __init__(self, session: AsyncSession, tenant_id: str, actor: Actor) -> None:
        self.session = session
        self.tenant_id = tenant_id
        self.actor = actor

    # ------------------------------------------------------------------ leitura
    async def _one(self, stmt: Any) -> CourierDelivery | None:
        return (await self.session.execute(stmt)).scalars().first()

    async def open_for(self, order_id: str) -> CourierDelivery | None:
        return await self._one(
            select(CourierDelivery)
            .where(CourierDelivery.order_id == order_id)
            .where(CourierDelivery.active_marker == 1)
        )

    async def history(self, order_id: str) -> list[CourierDelivery]:
        stmt = (
            select(CourierDelivery)
            .where(CourierDelivery.order_id == order_id)
            .order_by(CourierDelivery.created_at.desc())
            .limit(HISTORY_LIMIT)
        )
        return list((await self.session.execute(stmt)).scalars())

    async def delivery_settings(self) -> DeliverySettings:
        """Lido do banco: o worker não tem contexto de requisição, e o contexto tem cache."""
        valores = await TenantRepository(self.session).settings(self.tenant_id)
        return fulfillment_settings(valores).delivery

    # ------------------------------------------------------------------ reservar
    async def reserve(self, order: Order, *, auto: bool) -> CourierDelivery | None:
        """A linha `requested` para o worker. O chamador travou o pedido.

        `auto` (o "Pronto" pelo outbox): só zona da Uber com chamada automática ligada, e o que
        não se aplica volta None em silêncio. Pelo operador, qualquer pedido de entrega pronto, e
        o que impede vira erro com motivo.
        """
        if order.fulfillment_type != "delivery":
            if auto:
                return None
            raise ValidationError(
                "Só pedido de entrega chama a Uber.", reason="courier_not_delivery"
            )
        cfg = await self.delivery_settings()
        if auto and (
            (order.fulfillment or {}).get("courier") != "uber" or not cfg.uber.auto_dispatch
        ):
            return None
        if order.status != OrderStatus.READY_FOR_PICKUP:
            if auto:
                return None
            raise ValidationError(
                'Marque o pedido como "Pronto" antes de chamar a Uber.', reason="courier_not_ready"
            )
        aberta = await self.open_for(order.id)
        if aberta is not None:
            if auto:
                return aberta
            raise CourierInProgressError
        now = utcnow()
        row = CourierDelivery(
            order_id=order.id,
            provider=uber.PROVIDER,
            environment=cfg.uber.environment,
            status=CourierStatus.REQUESTED,
            active_marker=1,
            requested_by_actor=self.actor.id,
            max_fee_cents=cfg.uber.max_fee_cents,
            attempts=0,
            next_attempt_at=now,
        )
        self.session.add(row)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            # A UNIQUE da corrida aberta falou mais rápido que a leitura acima.
            raise CourierInProgressError from exc
        await audit(
            self.session,
            actor=self.actor.id,
            action="order.courier_requested",
            entity_type="order",
            entity_id=order.id,
            tenant_id=self.tenant_id,
            after={"courier_delivery_id": row.id, "auto": auto, "environment": row.environment},
        )
        return row

    async def confirm_fee(self, order: Order) -> CourierDelivery:
        """ "Chamar mesmo assim": a cotação passou do teto e a loja aceita aquele valor."""
        row = await self._locked_open(order.id)
        if row is None or row.status != CourierStatus.AWAITING_CONFIRMATION:
            raise ConflictError("Não há corrida esperando confirmação neste pedido.")
        # O teto passa a ser o valor que a loja viu; se a Uber subir de novo, pergunta de novo.
        row.max_fee_cents = row.quoted_fee_cents
        row.status = CourierStatus.REQUESTED
        row.next_attempt_at = utcnow()
        row.last_error = None
        await self.session.flush()
        await audit(
            self.session,
            actor=self.actor.id,
            action="order.courier_fee_confirmed",
            entity_type="order",
            entity_id=order.id,
            tenant_id=self.tenant_id,
            after={"courier_delivery_id": row.id, "max_fee_cents": row.max_fee_cents},
        )
        return row

    async def request_cancel(self, order_id: str, *, reason: str) -> CourierDelivery | None:
        """Cancela a corrida aberta. O que ainda não existe na Uber fecha aqui; o que existe (ou
        pode existir) vira `cancelling` e o worker cancela na Uber."""
        row = await self._locked_open(order_id)
        if row is None:
            return None
        now = utcnow()
        if row.status in (CourierStatus.REQUESTED, CourierStatus.AWAITING_CONFIRMATION) and not (
            row.quote_id or row.provider_delivery_id
        ):
            _close(row, CourierStatus.CANCELLED, now, reason)
        elif row.status != CourierStatus.CANCELLING:
            row.status = CourierStatus.CANCELLING
            row.last_error = reason[:300]
            row.next_attempt_at = now
        await self.session.flush()
        await audit(
            self.session,
            actor=self.actor.id,
            action="order.courier_cancel_requested",
            entity_type="order",
            entity_id=order_id,
            tenant_id=self.tenant_id,
            after={"courier_delivery_id": row.id, "reason": reason[:200]},
        )
        return row

    async def _locked_open(self, order_id: str) -> CourierDelivery | None:
        return await self._one(
            select(CourierDelivery)
            .where(CourierDelivery.order_id == order_id)
            .where(CourierDelivery.active_marker == 1)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def _locked(self, row_id: str, *, skip_locked: bool = False) -> CourierDelivery | None:
        return await self._one(
            select(CourierDelivery)
            .where(CourierDelivery.id == row_id)
            .with_for_update(skip_locked=skip_locked)
            .execution_options(populate_existing=True)
        )

    # ------------------------------------------------------------------ worker
    async def process(self, row_id: str, now: datetime | None = None) -> str:
        """Um passo de uma corrida. **Faz commit** (reivindicação, depois resultado); repetir é
        seguro. Devolve o que aconteceu, para log e teste."""
        now = now or utcnow()
        row = await self._locked(row_id, skip_locked=True)
        if row is None:
            await self.session.commit()
            return "busy"
        devido = row.next_attempt_at is not None and row.next_attempt_at <= now
        if not devido:
            await self.session.commit()
            return "not_due"
        if row.status == CourierStatus.CANCELLING:
            return await self._cancel_at_uber(row)
        if row.status in (CourierStatus.REQUESTED, CourierStatus.CREATING):
            # CREATING com prazo vencido: o worker anterior morreu no meio.
            return await self._dispatch(row)
        await self.session.commit()
        return "not_due"

    async def refresh(self, order_id: str) -> CourierDelivery | None:
        """ "Atualizar": pergunta à Uber como está a corrida aberta. Faz commit."""
        row = await self.open_for(order_id)
        if row is None or row.provider_delivery_id is None:
            return row
        row_id, delivery_id = row.id, row.provider_delivery_id
        credenciais = await uber.load_credentials(self.session, self.tenant_id)
        token = await uber.stored_token(self.session, self.tenant_id)
        await self.session.commit()  # nada fica preso enquanto a Uber responde
        if credenciais is None:
            raise ValidationError("Conecte a conta da Uber.", reason="uber_not_connected")
        try:
            info = await (await self._client(credenciais, token)).get(delivery_id)
        except uber.CourierError as exc:
            raise ValidationError(
                f"A Uber não respondeu: {exc}", reason="uber_unavailable"
            ) from exc
        row = await self._locked(row_id)
        if row is not None and info is not None and row.active_marker == 1:
            _apply(row, info, utcnow())
        await self.session.commit()
        return row

    # ------------------------------------------------------------------ chamar
    async def _dispatch(self, row: CourierDelivery) -> str:
        now = utcnow()
        order = await self.session.get(Order, row.order_id)
        if order is None or order.status != OrderStatus.READY_FOR_PICKUP:
            situacao = order.status if order is not None else "sumiu"
            _close(row, CourierStatus.CANCELLED, now, f"o pedido mudou ({situacao}) antes da Uber")
            await self.session.commit()
            return "order_moved"
        claim_or_reason = await self._claim(row, order, now)
        if isinstance(claim_or_reason, str):
            _close(row, CourierStatus.FAILED, now, claim_or_reason)
            await self.session.commit()
            return "failed"
        claim = claim_or_reason
        await self.session.commit()  # reivindicada; nada preso enquanto a Uber responde

        outcome = await self._call(claim)
        return await self._record(claim, outcome)

    async def _claim(self, row: CourierDelivery, order: Order, now: datetime) -> _Claim | str:
        """Junta o que a Uber precisa e marca a linha. Devolve o motivo quando não dá."""
        credenciais = await uber.load_credentials(self.session, self.tenant_id)
        if credenciais is None:
            return "a conta da Uber não está conectada"
        cfg = await self.delivery_settings()
        origem = cfg.origin
        if origem is None:
            return "falta o endereço de coleta da Uber (Entrega e checkout)"
        coleta_fone = _phone(origem.phone)
        if coleta_fone is None:
            return "o telefone da coleta não é um número válido"
        cliente = order.customer_snapshot or {}
        cliente_fone = _phone(cliente.get("phone"))
        if cliente_fone is None:
            return "o pedido não tem o WhatsApp do cliente, e a Uber exige um telefone"
        endereco = (order.fulfillment or {}).get("address")
        if not isinstance(endereco, dict) or not endereco.get("street"):
            return "o pedido não tem o endereço de entrega"
        tenant = await self.session.get(Tenant, self.tenant_id)
        itens = await self._items(order.id)
        notas = " · ".join(
            str(v).strip() for v in (endereco.get("reference"),) if v and str(v).strip()
        )
        pedido = uber.DeliveryRequest(
            pickup_name=origem.name,
            pickup_address=uber.Address(
                street=origem.address,
                number=origem.number,
                district=origem.district,
                city=origem.city,
                state=origem.state,
                postal_code=origem.postal_code,
                complement=origem.complement,
            ),
            pickup_phone=coleta_fone,
            pickup_notes=origem.notes,
            dropoff_name=str(endereco.get("recipient_name") or cliente.get("name") or "Cliente"),
            dropoff_address=uber.Address(
                street=str(endereco.get("street") or ""),
                number=str(endereco.get("number") or ""),
                district=str(endereco.get("district") or ""),
                city=str(endereco.get("city") or ""),
                state=str(endereco.get("state") or ""),
                postal_code=str(endereco.get("postal_code") or ""),
                complement=(str(endereco.get("complement") or "").strip() or None),
            ),
            dropoff_phone=cliente_fone,
            dropoff_notes=notas or None,
            items=tuple(
                uber.ManifestItem(
                    name=(
                        f"{item.product_name} — {item.variant_name}"
                        if item.variant_name
                        else item.product_name
                    ),
                    quantity=max(math.ceil(item.quantity_milli / 1000), 1),
                    price_cents=int(item.unit_price_cents),
                )
                for item in itens
            ),
            total_value_cents=int(order.subtotal_cents),
            reference=f"Pedido #{order.number}",
            external_id=row.id,
            store_ref=tenant.slug if tenant is not None else self.tenant_id,
            simulated=row.environment == "test",
        )
        token = await uber.stored_token(self.session, self.tenant_id)
        row.status = CourierStatus.CREATING
        row.attempts += 1
        row.next_attempt_at = now + LEASE
        await self.session.flush()
        return _Claim(
            row_id=row.id,
            attempt=row.attempts,
            credentials=credenciais,
            token=token,
            request=pedido,
            max_fee_cents=row.max_fee_cents,
            previous_quote=row.quote_id,
        )

    async def _items(self, order_id: str) -> list[OrderItem]:
        stmt = (
            select(OrderItem)
            .where(OrderItem.order_id == order_id)
            .order_by(OrderItem.line_no)
            .limit(uber.MAX_MANIFEST_ITEMS)
        )
        return list((await self.session.execute(stmt)).scalars())

    async def _client(
        self, credenciais: uber.UberCredentials, token: str | None
    ) -> uber.UberDirect:
        """O cliente com o token guardado; sem ele (ou recusado), pede outro e guarda."""

        async def novo() -> str:
            valor, vence_em = await uber.request_token(credenciais)
            await uber.save_token(self.session, self.tenant_id, valor, vence_em)
            await self.session.commit()  # transação curta, entre duas chamadas à Uber
            return valor

        return uber.UberDirect(credenciais, token or await novo(), novo)

    async def _call(self, claim: _Claim) -> _Outcome:
        """Só rede (e o commit curto do token). Nenhuma linha travada aqui."""
        try:
            cliente = await self._client(claim.credentials, claim.token)
            if claim.previous_quote:
                # Tentativa anterior sem resposta: a corrida dela pode existir.
                achada = await self._adopt(cliente, claim, claim.previous_quote)
                if achada is not None:
                    return _Outcome(info=achada)
            cotacao = await cliente.quote(claim.request)
            if claim.max_fee_cents is not None and cotacao.fee_cents > claim.max_fee_cents:
                return _Outcome(quote=cotacao)
            await self._remember_quote(claim, cotacao.id)
            try:
                return _Outcome(info=await cliente.create(claim.request, cotacao.id))
            except uber.CourierError as exc:
                if exc.code != "duplicate_delivery":
                    raise
                achada = await self._adopt(cliente, claim, cotacao.id)
                if achada is None:
                    raise
                return _Outcome(info=achada)
        except uber.CourierError as exc:
            return _Outcome(error=exc)

    async def _adopt(
        self, cliente: uber.UberDirect, claim: _Claim, quote_id: str
    ) -> uber.DeliveryInfo | None:
        """A corrida `del_X` da cotação `dqt_X`, se ela existe e é nossa (external_id)."""
        delivery_id = uber.delivery_id_for_quote(quote_id)
        if delivery_id is None:
            return None
        info = await cliente.get(delivery_id)
        if info is None or info.external_id != claim.row_id:
            return None
        return info

    async def _remember_quote(self, claim: _Claim, quote_id: str) -> None:
        """Grava a cotação antes de criar: é por ela que se acha a corrida se a resposta da
        criação se perder. Transação curta; a linha continua com este worker."""
        row = await self._locked(claim.row_id)
        if row is None or row.attempts != claim.attempt or row.status != CourierStatus.CREATING:
            await self.session.commit()
            raise uber.CourierError("outro worker assumiu a corrida", definitive=False)
        row.quote_id = quote_id
        await self.session.commit()

    async def _record(self, claim: _Claim, outcome: _Outcome) -> str:
        now = utcnow()
        row = await self._locked(claim.row_id)
        if row is None:
            await self.session.commit()
            return "gone"
        if row.status == CourierStatus.CANCELLING:
            # Cancelaram enquanto a Uber criava: guarda o id para o cancelamento achar a corrida.
            if outcome.info is not None:
                row.provider_delivery_id = outcome.info.id or row.provider_delivery_id
                row.tracking_url = outcome.info.tracking_url or row.tracking_url
            row.next_attempt_at = now
            await self.session.commit()
            return "cancelling"
        if row.status != CourierStatus.CREATING or row.attempts != claim.attempt:
            await self.session.commit()
            logger.warning("courier: reivindicação perdida", extra={"courier_id": row.id})
            return "superseded"

        resultado: str
        if outcome.info is not None:
            _apply(row, outcome.info, now)
            resultado = "active"
        elif outcome.quote is not None:
            row.status = CourierStatus.AWAITING_CONFIRMATION
            row.quoted_fee_cents = outcome.quote.fee_cents
            row.next_attempt_at = None
            row.last_error = (
                f"a Uber cotou {outcome.quote.fee_cents} centavos, acima do teto da loja"
            )
            resultado = "awaiting_confirmation"
        else:
            erro = outcome.error or uber.CourierError("sem resposta", definitive=False)
            if erro.definitive or row.attempts >= MAX_ATTEMPTS:
                _close(row, CourierStatus.FAILED, now, str(erro))
                resultado = "failed"
            else:
                row.status = CourierStatus.REQUESTED
                row.last_error = str(erro)[:300]
                row.next_attempt_at = now + BACKOFF[min(row.attempts - 1, len(BACKOFF) - 1)]
                resultado = "retry"
        await self.session.commit()
        logger.info(
            "courier: chamada à Uber",
            extra={
                "courier_id": row.id,
                "order_id": row.order_id,
                "outcome": resultado,
                "attempt": claim.attempt,
                "environment": row.environment,
                "fee_cents": row.fee_cents,
                "error_code": outcome.error.code if outcome.error else None,
            },
        )
        return resultado

    # ------------------------------------------------------------------ cancelar
    async def _cancel_at_uber(self, row: CourierDelivery) -> str:
        now = utcnow()
        row_id = row.id
        delivery_id = row.provider_delivery_id
        quote_id = row.quote_id
        credenciais = await uber.load_credentials(self.session, self.tenant_id)
        token = await uber.stored_token(self.session, self.tenant_id)
        row.attempts += 1
        tentativa = row.attempts
        row.next_attempt_at = now + LEASE
        await self.session.commit()

        if credenciais is None:
            return await self._finish_cancel(
                row_id, tentativa, None, "conta da Uber desconectada: cancele no painel da Uber"
            )
        info: uber.DeliveryInfo | None = None
        erro: uber.CourierError | None = None
        try:
            cliente = await self._client(credenciais, token)
            if delivery_id is None and quote_id:
                derivado = uber.delivery_id_for_quote(quote_id)
                achada = await cliente.get(derivado) if derivado else None
                delivery_id = achada.id if achada and achada.external_id == row_id else None
            if delivery_id is not None:
                info = await cliente.cancel_delivery(delivery_id)
        except uber.CourierError as exc:
            erro = exc
        return await self._finish_cancel(row_id, tentativa, info, None, erro)

    async def _finish_cancel(
        self,
        row_id: str,
        tentativa: int,
        info: uber.DeliveryInfo | None,
        aviso: str | None,
        erro: uber.CourierError | None = None,
    ) -> str:
        now = utcnow()
        row = await self._locked(row_id)
        if row is None or row.status != CourierStatus.CANCELLING or row.attempts != tentativa:
            await self.session.commit()
            return "superseded"
        if erro is None:
            if info is not None:
                _apply(row, info, now)
            if row.status == CourierStatus.CANCELLING:
                _close(row, CourierStatus.CANCELLED, now, aviso)
            await self.session.commit()
            return "cancelled"
        if erro.code == "delivery_not_found":
            _close(row, CourierStatus.CANCELLED, now, None)
            resultado = "cancelled"
        elif erro.definitive or row.attempts >= MAX_ATTEMPTS:
            # A Uber não deixou (o entregador já coletou, por exemplo): a corrida segue.
            row.status = CourierStatus.ACTIVE
            row.next_attempt_at = None
            row.last_error = f"não deu para cancelar na Uber: {erro}"[:300]
            resultado = "cancel_refused"
        else:
            row.last_error = str(erro)[:300]
            row.next_attempt_at = now + BACKOFF[min(row.attempts - 1, len(BACKOFF) - 1)]
            resultado = "retry"
        await self.session.commit()
        return resultado


# ------------------------------------------------------------------ varredor


async def due_couriers(session: AsyncSession, now: datetime) -> list[tuple[str, str]]:
    """O que está na hora, em todas as lojas: chamar, tentar de novo, cancelar, ou assumir a
    reivindicação de um worker que morreu (prazo vencido)."""
    stmt = (
        select(CourierDelivery.id, CourierDelivery.tenant_id)
        .where(
            or_(
                CourierDelivery.status == CourierStatus.REQUESTED,
                CourierDelivery.status == CourierStatus.CREATING,
                CourierDelivery.status == CourierStatus.CANCELLING,
            )
        )
        .where(CourierDelivery.next_attempt_at <= now)
        .order_by(CourierDelivery.next_attempt_at)
        .limit(BATCH)
        .execution_options(**{CROSS_TENANT_OPTION: True})
    )
    return [(rid, tid) for rid, tid in (await session.execute(stmt)).all()]


async def process_one(session: AsyncSession, row_id: str, tenant_id: str, now: datetime) -> str:
    bind_session_tenant(session, tenant_id)
    return await CourierService(session, tenant_id, Actor.system("courier")).process(row_id, now)


async def run_couriers(factory: async_sessionmaker[AsyncSession], now: datetime) -> int:
    """Uma volta do varredor. Cada linha na sua sessão: uma loja com problema não trava as
    outras."""
    async with factory() as session:
        due = await due_couriers(session, now)
    feitos = 0
    for row_id, tenant_id in due:
        async with factory() as session:
            try:
                resultado = await process_one(session, row_id, tenant_id, now)
                feitos += resultado in ("active", "cancelled", "awaiting_confirmation", "failed")
            except Exception:
                logger.exception("courier: falha ao processar", extra={"courier_id": row_id})
    return feitos


def is_open(row: CourierDelivery | None) -> bool:
    return row is not None and row.status in OPEN_COURIER_STATUSES


async def get_order(session: AsyncSession, order_id: str, *, lock: bool = False) -> Order:
    stmt = select(Order).where(Order.id == order_id)
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    order = await session.scalar(stmt)
    if order is None:
        raise NotFoundError("Pedido não encontrado.")
    return order
