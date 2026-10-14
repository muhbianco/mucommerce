"""What each transactional e-mail says (pt-BR). Fixed in the code for now; editable templates
per store are a later stage (ADR 0011 §11)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.notifications.render import Line, Message
from app.orders.models import Order, OrderItem
from app.payments.models import Payment, Refund
from app.pricing.quote import MILLI

ORDER_STATUS_LABEL = {
    "awaiting_payment": "aguardando pagamento",
    "payment_confirmed": "pagamento confirmado",
    "accepted": "aceito pela loja",
    "in_production": "em preparo",
    "ready_for_pickup": "pronto para retirar",
    "shipped": "saiu para entrega",
    "delivered": "entregue",
    "cancelled": "cancelado",
    "failed": "expirado sem pagamento",
}


def status_label(status: str, fulfillment_type: str = "") -> str:
    """O nome da situação para o cliente. Na entrega, "pronto" é para o entregador retirar."""
    if status == "ready_for_pickup" and fulfillment_type == "delivery":
        return "pronto e aguardando o entregador"
    return ORDER_STATUS_LABEL.get(status, status)


# Changes the customer hears about (the others are internal steps).
TOLD_ABOUT = ("accepted", "in_production", "ready_for_pickup", "shipped", "delivered")


@dataclass(frozen=True, slots=True)
class MailContext:
    store_name: str
    timezone: str
    order: Order
    items: list[OrderItem]
    url: str | None = None  # the customer's page for this order
    panel_url: str | None = None  # the store's page for this order
    payment: Payment | None = None
    refund: Refund | None = None
    online_url: str | None = None  # a ticket's online address, once paid
    #: [(código, nome do lote)] dos ingressos do MuTickets emitidos neste pedido.
    tickets: list[tuple[str, str]] = field(default_factory=list)
    #: Origem da loja (`https://host`), para montar o link de cada ingresso.
    store_origin: str | None = None
    #: Como o ingresso chega, quando quem emite é o lojista (`ticketing=own`). Com MuTickets
    #: esta fica vazia: quem fala do ingresso é o e-mail próprio dele, com o link.
    own_ticket_note: str | None = None
    #: O link da Uber para acompanhar o entregador, quando a entrega é por ela (docs/15).
    courier_tracking_url: str | None = None
    #: Por que a corrida da Uber precisa da loja (aviso `courier.needs_attention`).
    courier_problem: str | None = None


def money(cents: int, currency: str = "BRL") -> str:
    symbol = "R$ " if currency == "BRL" else f"{currency} "
    return symbol + f"{Decimal(cents) / 100:,.2f}".replace(",", "@").replace(".", ",").replace(
        "@", "."
    )


def when(value: datetime, timezone: str) -> str:
    try:
        zone = ZoneInfo(timezone)
    except (ValueError, KeyError):  # a store with a bad timezone still gets its e-mail
        zone = ZoneInfo("America/Sao_Paulo")
    return value.astimezone(zone).strftime("%d/%m/%Y às %H:%M")


def _lines(ctx: MailContext) -> list[Line]:
    lines = [
        Line(
            label=f"{Decimal(item.quantity_milli) / MILLI:g} x {item.product_name}",
            value=money(item.total_cents, ctx.order.currency),
        )
        for item in ctx.items[:20]
    ]
    if ctx.order.delivery_fee_cents:
        lines.append(Line("Entrega", money(ctx.order.delivery_fee_cents, ctx.order.currency)))
    if ctx.order.discount_cents:
        lines.append(Line("Desconto", "-" + money(ctx.order.discount_cents, ctx.order.currency)))
    lines.append(Line("Total", money(ctx.order.total_cents, ctx.order.currency)))
    return lines


def _where(ctx: MailContext) -> str | None:
    place = (ctx.order.fulfillment or {}).get("name")
    if ctx.order.fulfillment_type == "pickup" and place:
        return f"Retirada em {place}."
    if ctx.order.fulfillment_type == "delivery" and place:
        return f"Entrega: {place}."
    return None


def order_placed(ctx: MailContext) -> Message:
    paragraphs = [f"Recebemos o seu pedido #{ctx.order.number}."]
    if ctx.order.expires_at and ctx.order.status == "awaiting_payment":
        paragraphs.append(
            f"Ele fica reservado até {when(ctx.order.expires_at, ctx.timezone)}; depois disso, o "
            "estoque volta para a loja."
        )
    where = _where(ctx)
    if where:
        paragraphs.append(where)
    return Message(
        template_key="order_placed",
        subject=f"Pedido #{ctx.order.number} recebido — {ctx.store_name}",
        heading=f"Pedido #{ctx.order.number} recebido",
        paragraphs=paragraphs,
        items=_lines(ctx),
        cta_url=ctx.url,
        cta_label="Pagar e acompanhar",
    )


def payment_pix_pending(ctx: MailContext) -> Message:
    payment = ctx.payment
    deadline = payment.expires_at if payment else None
    paragraphs = ["Copie o código abaixo e pague no app do seu banco."]
    if deadline:
        paragraphs.append(f"O código vale até {when(deadline, ctx.timezone)}.")
    return Message(
        template_key="payment_pix_pending",
        subject=f"Pix do pedido #{ctx.order.number} — {ctx.store_name}",
        heading=f"Pague o pedido #{ctx.order.number} com Pix",
        paragraphs=paragraphs,
        code=payment.pix_copy_paste if payment else None,
        code_label="Pix copia e cola:",
        cta_url=ctx.url,
        cta_label="Abrir o pedido",
        note="A confirmação chega sozinha assim que o banco avisar.",
    )


def order_paid(ctx: MailContext) -> Message:
    paragraphs = [f"O pagamento do pedido #{ctx.order.number} foi confirmado."]
    where = _where(ctx)
    if where:
        paragraphs.append(where)
    if ctx.online_url:
        paragraphs.append("O acesso online está no botão abaixo.")
    if ctx.own_ticket_note:
        # Ingresso que a loja entrega por fora: sem esta linha o comprador lê "pagamento
        # confirmado" e não faz ideia de como o ingresso chega até ele.
        paragraphs.append(ctx.own_ticket_note)
    return Message(
        template_key="order_paid",
        subject=f"Pagamento confirmado — pedido #{ctx.order.number}",
        heading="Pagamento confirmado",
        paragraphs=paragraphs,
        items=_lines(ctx),
        cta_url=ctx.online_url or ctx.url,
        cta_label="Acessar" if ctx.online_url else "Ver pedido",
    )


def ticket_issued(ctx: MailContext) -> Message:
    """O e-mail que leva o ingresso.

    Manda **link**, não imagem: cliente de e-mail bloqueia imagem por padrão, e um QR bloqueado
    é um ingresso que não existe na hora da porta. O link abre a página do ingresso, no host da
    loja, com o QR e o código — e funciona de novo semanas depois, quando a pessoa procurar o
    e-mail no celular na fila.

    Um por ingresso na lista, porque cada um vale uma entrada e pode ser repassado.
    """
    plural = len(ctx.tickets) > 1
    linhas = [
        f"{lote} — {codigo}: {ctx.store_origin or ''}/ingresso/{codigo}"
        for codigo, lote in ctx.tickets
    ]
    primeiro = ctx.tickets[0][0] if ctx.tickets else ""
    return Message(
        template_key="ticket_issued",
        subject=(
            f"Seus ingressos — pedido #{ctx.order.number}"
            if plural
            else f"Seu ingresso — pedido #{ctx.order.number}"
        ),
        heading="Ingressos emitidos" if plural else "Ingresso emitido",
        paragraphs=[
            (
                "Seus ingressos estão prontos. Abra cada link abaixo e mostre o QR na entrada."
                if plural
                else "Seu ingresso está pronto. Abra o link abaixo e mostre o QR na entrada."
            ),
            (
                "Cada ingresso vale uma entrada — dá para repassar o link de um deles para quem "
                "vai com você."
                if plural
                else "Guarde este e-mail: o link funciona até o dia do evento."
            ),
            *linhas,
        ],
        cta_url=f"{ctx.store_origin or ''}/ingresso/{primeiro}" if primeiro else ctx.url,
        cta_label="Ver ingresso",
    )


def order_failed(ctx: MailContext) -> Message:
    return Message(
        template_key="order_failed",
        subject=f"Pedido #{ctx.order.number} expirou sem pagamento",
        heading=f"O pedido #{ctx.order.number} expirou",
        paragraphs=[
            "O prazo de pagamento passou e os itens voltaram para a loja.",
            "Se ainda quiser comprar, é só fazer um novo pedido.",
        ],
        cta_url=ctx.url,
        cta_label="Ver o pedido",
    )


def order_cancelled(ctx: MailContext) -> Message:
    paragraphs = [f"O pedido #{ctx.order.number} foi cancelado."]
    if ctx.order.refunded_cents:
        paragraphs.append(
            f"A devolução de {money(ctx.order.refunded_cents, ctx.order.currency)} foi feita pelo "
            "meio de pagamento usado na compra."
        )
    elif ctx.order.paid_at:
        paragraphs.append("A devolução do valor pago está em andamento.")
    return Message(
        template_key="order_cancelled",
        subject=f"Pedido #{ctx.order.number} cancelado",
        heading=f"Pedido #{ctx.order.number} cancelado",
        paragraphs=paragraphs,
        cta_url=ctx.url,
    )


def order_status_changed(ctx: MailContext) -> Message:
    label = status_label(ctx.order.status, ctx.order.fulfillment_type)
    paragraphs = [f"O seu pedido #{ctx.order.number} está {label}."]
    where = _where(ctx)
    # Retirada pronta diz onde buscar; entrega pronta ainda não saiu (o "onde" vem no "saiu").
    pronto_para_retirar = ctx.order.status == "ready_for_pickup" and (
        ctx.order.fulfillment_type == "pickup"
    )
    if where and (pronto_para_retirar or ctx.order.status == "shipped"):
        paragraphs.append(where)
    if ctx.order.status == "shipped" and ctx.courier_tracking_url:
        paragraphs.append(f"Acompanhe o entregador pela Uber: {ctx.courier_tracking_url}")
    return Message(
        template_key="order_status_changed",
        subject=f"Pedido #{ctx.order.number}: {label}",
        heading=f"Pedido #{ctx.order.number}: {label}",
        paragraphs=paragraphs,
        cta_url=ctx.url,
    )


def refund_completed(ctx: MailContext) -> Message:
    refund = ctx.refund
    amount = money(refund.amount_cents if refund else 0, ctx.order.currency)
    return Message(
        template_key="refund_completed",
        subject=f"Devolução de {amount} — pedido #{ctx.order.number}",
        heading="Devolução concluída",
        paragraphs=[
            f"Devolvemos {amount} do pedido #{ctx.order.number} pelo mesmo meio de pagamento.",
            "O prazo até o dinheiro aparecer depende do banco ou do cartão.",
        ],
        cta_url=ctx.url,
    )


def store_courier_attention(ctx: MailContext) -> Message:
    """A corrida da Uber parou numa decisão da loja: chamar de novo, confirmar o valor ou levar."""
    motivo = (ctx.courier_problem or "a Uber não completou a chamada").rstrip(".")
    return Message(
        template_key="store_courier_attention",
        subject=f"Pedido #{ctx.order.number}: a entrega pela Uber precisa de você",
        heading=f"Pedido #{ctx.order.number}: entrega pela Uber",
        paragraphs=[
            f"A corrida não seguiu sozinha: {motivo}.",
            "No pedido você escolhe chamar a Uber de novo, confirmar o valor cotado ou levar "
            "com a sua equipe.",
        ],
        cta_url=ctx.panel_url,
        cta_label="Abrir o pedido",
    )


def store_order_paid(ctx: MailContext) -> Message:
    name = (ctx.order.customer_snapshot or {}).get("name") or "Cliente"
    where = _where(ctx) or "Sem entrega definida."
    total = money(ctx.order.total_cents, ctx.order.currency)
    return Message(
        template_key="store_order_paid",
        subject=f"Novo pedido pago #{ctx.order.number} — {total}",
        heading=f"Pedido #{ctx.order.number} pago",
        paragraphs=[f"Cliente: {name}.", where],
        items=_lines(ctx),
        cta_url=ctx.panel_url,
        cta_label="Abrir no painel",
    )
