"""Mercado Pago pela **Orders API** (`/v1/orders`): Pix e cartão tokenizado pelo Card Payment
Brick, cobrados com o access token da própria loja — o dinheiro cai na conta dela.

Migrado de `/v1/payments` em 29/09/2026: o Mercado Pago está desativando a API de Pagamentos, e
uma aplicação nova já nasce sem ela. O contrato foi conferido contra a referência oficial
(create, refund e os formatos de id) e contra o adaptador do mu-tower, que já roda em produção
sobre a mesma API — inclusive o susto que ele tomou e que está no `map_status` abaixo.

O que muda em relação à API antiga, e que sangra se passar batido:

- **O id não é mais número.** Vem `ORD01J…` para a order e `PAY01J…` para o pagamento dentro
  dela. Um validador de dígitos recusaria tudo.
- **`processed` não é aprovação.** A order conclui com `status: processed` e o dinheiro é
  afirmado pelo `status_detail: accredited`. No mu-tower um mapeamento que só conhecia
  `approved` devolveu *pendente para um Pix já pago*: dinheiro dentro, produto não entregue.
- **O que importa está dentro da transação.** `transactions.payments[0]` carrega status, QR do
  Pix e valor; a order por fora é rede de segurança.
- **Estorno é da order**, `POST /v1/orders/{id}/refund`, e o parcial identifica o pagamento de
  dentro (`transactions: [{id, amount}]`). Total vai sem corpo — a lista vazia leva 400.

O que **não** muda: todo create leva `X-Idempotency-Key` = o id do nosso pagamento, então
tentativa repetida não cobra duas vezes; e o webhook continua sendo só uma dica — quem diz o que
aconteceu é a consulta. A assinatura segue o mesmo manifesto `ts`/`v1`.

Dinheiro: centavo inteiro do nosso lado, string decimal ("19.90") do lado deles.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.payments.models import PaymentStatus
from app.payments.provider import (
    ChargeRequest,
    ChargeResult,
    CredentialTest,
    InboundWebhook,
    PixData,
    ProviderCapabilities,
    ProviderCredentials,
    ProviderError,
    ProviderRef,
    RefundResult,
    WebhookHint,
    WebhookVerdict,
)

logger = get_logger(__name__)

#: Id da Orders API (`ORD01J…`, `PAY01J…`) e também o id numérico da API antiga, que ainda
#: aparece em pagamento criado antes da migração e em notificação de tópico `payment`.
MP_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_WAITING_METHODS = frozenset({"pix", "bank_transfer", "ticket"})


def to_amount_str(cents: int) -> str:
    """Dinheiro como a Orders API quer: string decimal, duas casas, sem float no caminho."""
    return str((Decimal(cents) / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def to_cents(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int((Decimal(str(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except ArithmeticError:
        return None


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def map_status(status: str | None, detail: str | None, payment_type: str | None) -> str:
    """MP status → ours. `in_mediation` is a dispute on a paid payment: still approved here.

    `accredited` entra como aprovação ao lado de `approved` porque é assim que a Orders API
    afirma que o dinheiro entrou: ela conclui com `status: processed`, que sozinho só diz que a
    order foi processada. No mu-tower, um mapeamento que só conhecia `approved` devolveu
    pendente para um Pix já pago — o pior desfecho possível, e por isso está no topo aqui.
    """
    if detail == "partially_refunded":
        return PaymentStatus.PARTIALLY_REFUNDED
    if detail == "accredited":
        return PaymentStatus.APPROVED
    # A Orders API diz "o pagador tem de fazer alguma coisa" com estas duas palavras, e é
    # exatamente o Pix esperando transferência. Sem elas a cobrança virava `pending`, e a tela
    # só mostra o QR em `requires_action`: o cliente ficava com um código válido que a loja
    # escondia dele. Aconteceu em produção no mesmo dia da migração.
    if status == "action_required" or detail == "waiting_transfer":
        return PaymentStatus.REQUIRES_ACTION
    match status:
        case "approved" | "in_mediation":
            return PaymentStatus.APPROVED
        case "pending":
            return (
                PaymentStatus.REQUIRES_ACTION
                if payment_type in _WAITING_METHODS
                else PaymentStatus.PENDING
            )
        case "authorized" | "in_process":
            return PaymentStatus.PENDING
        case "rejected" | "failed":
            return PaymentStatus.REJECTED
        # A Orders API escreve com um `l` só; a antiga, com dois. As duas grafias chegam.
        case "cancelled" | "canceled":
            return (
                PaymentStatus.EXPIRED
                if detail in ("expired", "expired_transaction")
                else PaymentStatus.CANCELLED
            )
        case "refunded":
            return PaymentStatus.REFUNDED
        case "charged_back":
            return PaymentStatus.CHARGEBACK
    return PaymentStatus.PENDING


def first_payment(order: Mapping[str, Any]) -> Mapping[str, Any]:
    """O pagamento de dentro da order. Vazio quando ainda não há nenhum."""
    transactions = order.get("transactions")
    payments = transactions.get("payments") if isinstance(transactions, Mapping) else None
    if isinstance(payments, list):
        for item in payments:
            if isinstance(item, Mapping):
                return item
    return {}


def _refunded_cents(order: Mapping[str, Any], payment: Mapping[str, Any]) -> int | None:
    """Quanto já voltou, somando os estornos da order.

    A order lista cada estorno em `transactions.refunds`; o pagamento também carrega o
    acumulado. Somar a lista é o que permite distinguir devolução parcial de total sem
    depender de um único campo.
    """
    transactions = order.get("transactions")
    refunds = transactions.get("refunds") if isinstance(transactions, Mapping) else None
    if isinstance(refunds, list) and refunds:
        total = 0
        for item in refunds:
            if isinstance(item, Mapping):
                total += to_cents(item.get("amount")) or 0
        return total or None
    return to_cents(payment.get("refunded_amount"))


def order_status(order: Mapping[str, Any]) -> tuple[str, str | None, str | None, str | None]:
    """Estado da order do ponto de vista do dinheiro.

    Ordem importa, e cada passo custou um erro de alguém:

    1. **Estorno e chargeback mandam.** Quem devolveu dinheiro não está aprovado, por mais que o
       pagamento de dentro ainda diga que sim.
    2. **O pagamento de dentro manda sobre a order.** É ele que usa o vocabulário conhecido; o
       `processed` de fora não afirma que entrou dinheiro.
    3. **A order é a rede de segurança.** Já apareceu resposta em que o status de dentro ainda
       não tinha virado e o de fora já dizia `accredited`.
    """
    payment = first_payment(order)
    raw_status = str(payment.get("status") or order.get("status") or "") or None
    raw_detail = str(payment.get("status_detail") or order.get("status_detail") or "") or None
    method = payment.get("payment_method")
    payment_type = (
        str((method or {}).get("type") or "") or None if isinstance(method, Mapping) else None
    )

    transactions = order.get("transactions")
    chargebacks = transactions.get("chargebacks") if isinstance(transactions, Mapping) else None
    if isinstance(chargebacks, list) and chargebacks:
        return PaymentStatus.CHARGEBACK, raw_status, raw_detail, payment_type

    devolvido = _refunded_cents(order, payment) or 0
    pago = to_cents(payment.get("amount") or order.get("total_amount")) or 0
    if devolvido > 0:
        virou = PaymentStatus.PARTIALLY_REFUNDED if 0 < devolvido < pago else PaymentStatus.REFUNDED
        return virou, raw_status, raw_detail, payment_type

    ours = map_status(
        str(payment.get("status") or "") or None,
        str(payment.get("status_detail") or "") or None,
        payment_type,
    )
    if ours == PaymentStatus.PENDING:
        ours = map_status(
            str(order.get("status") or "") or None,
            str(order.get("status_detail") or "") or None,
            payment_type,
        )
    return ours, raw_status, raw_detail, payment_type


def result_from(data: Mapping[str, Any], http_status: int | None = None) -> ChargeResult:
    """Uma order do Mercado Pago vira o nosso resultado de cobrança."""
    payment = first_payment(data)
    ours, raw_status, raw_detail, _ = order_status(data)
    method = payment.get("payment_method")
    method = method if isinstance(method, Mapping) else {}

    pix = None
    if method.get("qr_code"):
        pix = PixData(
            copy_paste=str(method["qr_code"]),
            qr_base64=str(method["qr_code_base64"]) if method.get("qr_code_base64") else None,
            ticket_url=str(method["ticket_url"]) if method.get("ticket_url") else None,
        )
    approved = ours in (PaymentStatus.APPROVED, PaymentStatus.PARTIALLY_REFUNDED)
    return ChargeResult(
        status=ours,
        provider_payment_id=str(data["id"]) if data.get("id") is not None else None,
        provider_status=raw_status,
        provider_status_detail=raw_detail,
        provider_reference=str(data.get("external_reference") or "") or None,
        paid_amount_cents=(
            to_cents(payment.get("amount") or data.get("total_amount")) if approved else None
        ),
        pix=pix,
        expires_at=_parse_dt(payment.get("date_of_expiration") or data.get("expiration_time")),
        failure_code=raw_detail if ours == PaymentStatus.REJECTED else None,
        failure_message=None,
        # A marca e os quatro últimos do cartão ainda não foram vistos numa resposta real da
        # Orders API. Em vez de adivinhar nome de campo, o resumo abaixo guarda o método e o
        # primeiro cartão de verdade conta a forma — igual ao que o mu-tower fez com o tópico.
        payer=None,
        raw_summary={
            "id": data.get("id"),
            "order_status": data.get("status"),
            "order_status_detail": data.get("status_detail"),
            "payment_id": payment.get("id"),
            "payment_status": payment.get("status"),
            "payment_status_detail": payment.get("status_detail"),
            "payment_method": {"id": method.get("id"), "type": method.get("type")},
            "total_amount": data.get("total_amount"),
            "country_id": data.get("country_id"),
        },
        http_status=http_status,
        refunded_cents=_refunded_cents(data, payment),
    )


def _split_name(name: str | None) -> dict[str, str]:
    parts = (name or "").strip().split()
    if not parts:
        return {}
    return {"first_name": parts[0][:60], "last_name": " ".join(parts[1:])[:60] or parts[0][:60]}


def charge_body(req: ChargeRequest, now: datetime) -> dict[str, Any]:
    """O corpo do `POST /v1/orders`.

    `processing_mode: automatic` porque quem decide aprovar é o Mercado Pago, não nós: no modo
    manual a order fica esperando uma captura nossa, e um Pix pago ficaria parado.

    **Sem URL de notificação no corpo, de propósito.** O campo da Orders API para isso não está
    confirmado — `config.online.callback_url` é, na documentação de Checkout Pro, para onde o
    comprador volta, e mandar uma URL de API para lá jogaria o cliente numa resposta JSON. O
    aviso vem do webhook da conta, configurado no painel do Mercado Pago, e a conciliação por
    varredura cobre a ausência dele. Confirmado o nome do campo, ele entra aqui.
    """
    if not req.payer_email:
        raise ProviderError("payer e-mail missing", code="payer_email_missing", definitive=True)
    payer: dict[str, Any] = {"email": req.payer_email, **_split_name(req.payer_name)}
    if req.payer_identification:
        payer["identification"] = {
            "type": req.payer_identification.get("type"),
            "number": req.payer_identification.get("number"),
        }

    if req.method == "pix":
        method: dict[str, Any] = {"id": "pix", "type": "bank_transfer"}
    elif req.method == "card":
        if req.card is None:
            raise ProviderError("card token missing", code="card_missing", definitive=True)
        method = {
            "id": req.card.payment_method_id,
            "type": "credit_card",
            "token": req.card.token,
            "installments": req.card.installments,
        }
    else:
        raise ProviderError("method not offered", code="method_not_offered", definitive=True)

    valor = to_amount_str(req.amount_cents)
    return {
        "type": "online",
        "processing_mode": "automatic",
        "external_reference": req.provider_reference[:64],
        "total_amount": valor,
        "description": req.description[:120],
        "payer": payer,
        "transactions": {"payments": [{"amount": valor, "payment_method": method}]},
    }


def _signature_parts(header: str) -> dict[str, str]:
    parts: dict[str, str] = {}
    for piece in header.split(","):
        key, _, value = piece.strip().partition("=")
        if key and value:
            parts[key.strip()] = value.strip()
    return parts


def signature_manifest(data_id: str | None, request_id: str | None, ts: str) -> str:
    manifest = ""
    if data_id:
        manifest += f"id:{data_id.lower()};"
    if request_id:
        manifest += f"request-id:{request_id};"
    return manifest + f"ts:{ts};"


class MercadoPagoProvider:
    name = "mercadopago"
    capabilities = ProviderCapabilities(
        mode="embedded",
        methods=("pix", "card"),
        cancel=True,
        refunds=True,
        signed_webhooks=True,
        required_secrets=("access_token", "webhook_secret"),
        required_public=("public_key",),
    )

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport  # tests inject httpx.MockTransport

    # ------------------------------------------------------------------ http
    async def _request(
        self,
        creds: ProviderCredentials,
        method: str,
        path: str,
        *,
        body: Mapping[str, Any] | None = None,
        params: Mapping[str, str | int] | None = None,
        idempotency: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        token = creds.secrets.get("access_token")
        if not token:
            raise ProviderError("access token missing", code="credentials_missing", definitive=True)
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        if idempotency:
            headers["X-Idempotency-Key"] = idempotency
        timeout = httpx.Timeout(settings.payments_http_timeout_seconds, connect=5.0)
        # One client per call: tasks run each on their own event loop, so a shared client
        # would outlive its loop. Payment volume makes the extra handshake irrelevant.
        async with httpx.AsyncClient(
            base_url=settings.mercadopago_api_base, timeout=timeout, transport=self._transport
        ) as client:
            try:
                response = await client.request(
                    method, path, json=body, params=params, headers=headers
                )
            except httpx.TimeoutException as exc:
                raise ProviderError("Mercado Pago timed out", code="timeout") from exc
            except httpx.HTTPError as exc:
                raise ProviderError("Mercado Pago unreachable", code="network") from exc
        try:
            data = response.json()
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {"results": data}
        status = response.status_code
        if status >= 500 or status == 429:
            raise ProviderError(
                f"Mercado Pago answered {status}", http_status=status, code="unavailable"
            )
        if status >= 400:
            # O corpo e o unico lugar onde o provedor diz *por que* recusou. Sem ele sobra o
            # numero, e 401 nao conta se e token errado, conta sem o produto habilitado ou
            # credencial de teste em producao. Vai so a resposta deles: token nunca em log.
            logger.warning(
                "Mercado Pago recusou",
                extra={"mp_status": status, "mp_path": path, "mp_body": response.text[:500]},
            )
            raise ProviderError(
                str(
                    data.get("message")
                    or _orders_error(data, "message")
                    or f"Mercado Pago refused ({status})"
                )[:200],
                http_status=status,
                code=_error_code(data, status),
                definitive=True,
            )
        return status, data

    # ------------------------------------------------------------------- orders
    async def create_charge(self, creds: ProviderCredentials, req: ChargeRequest) -> ChargeResult:
        body = charge_body(req, datetime.now(UTC))
        status, data = await self._request(
            creds, "POST", "/v1/orders", body=body, idempotency=req.payment_id
        )
        return result_from(data, status)

    async def fetch_status(self, creds: ProviderCredentials, ref: ProviderRef) -> ChargeResult:
        if ref.provider_payment_id:
            order_id = _checked_id(ref.provider_payment_id)
            status, data = await self._request(creds, "GET", f"/v1/orders/{order_id}")
            return result_from(data, status)
        found = await self._by_reference(creds, ref.provider_reference)
        if found is None:
            # MP has nothing under our reference (yet): nothing to report.
            return ChargeResult(status=PaymentStatus.PENDING, provider_payment_id=None)
        return result_from(found)

    async def _by_reference(
        self, creds: ProviderCredentials, reference: str
    ) -> Mapping[str, Any] | None:
        """A order que carrega a nossa referência, quando perdemos o id dela.

        Caminho de exceção: o id da order é guardado na criação, então isto só roda se aquela
        gravação se perdeu. A busca da Orders API **não** está confirmada contra uma resposta
        real, e por isso qualquer recusa aqui vira "não achei" em vez de derrubar a
        conciliação — com o corpo no log, para a primeira chamada de verdade contar a forma.
        """
        if not reference:
            return None
        try:
            _, data = await self._request(
                creds, "GET", "/v1/orders/search", params={"external_reference": reference}
            )
        except ProviderError as exc:
            logger.warning(
                "Busca de order por referência não respondeu como esperado",
                extra={"mp_code": exc.code, "mp_status": exc.http_status},
            )
            return None
        results = data.get("results") or data.get("elements") or []
        if isinstance(results, list) and results and isinstance(results[0], dict):
            return results[0]
        return None

    async def cancel(self, creds: ProviderCredentials, ref: ProviderRef) -> ChargeResult | None:
        order_id = ref.provider_payment_id
        if not order_id:
            found = await self._by_reference(creds, ref.provider_reference)
            if found is None:
                return None  # never created there
            order_id = str(found.get("id"))
        order_id = _checked_id(order_id)
        try:
            status, data = await self._request(
                creds, "POST", f"/v1/orders/{order_id}/cancel", idempotency=f"cancel-{order_id}"
            )
        except ProviderError as exc:
            if exc.http_status not in (400, 409):
                raise
            # Not cancellable any more (approved meanwhile, already expired): report its state.
            status, data = await self._request(creds, "GET", f"/v1/orders/{order_id}")
        return result_from(data, status)

    async def refund(
        self, creds: ProviderCredentials, ref: ProviderRef, amount_cents: int, *, idempotency: str
    ) -> RefundResult:
        """`POST /v1/orders/{id}/refund`, com o nosso id de devolução como chave de idempotência.

        Total vai **sem corpo** — é o que a referência pede ("o body deve ser enviado vazio") e o
        que o `refundTotal.ts` do SDK oficial faz. `{"transactions": []}` parece a mesma coisa e
        não é: o validador deles exige ao menos um item e responde 400 `minimum_items` (o
        estorno do pedido #9 da SG Pipas morreu assim em 07/10/2026). Parcial identifica o
        pagamento de dentro da order e o valor; por isso lemos a order antes: o `PAY…` não é o
        id que guardamos, é o de dentro.

        Sem o valor do pagamento, ou sem o `PAY…` para um parcial, recusamos aqui: cair no total
        nesses casos devolveria mais do que foi pedido.
        """
        order_id = _checked_id(ref.provider_payment_id or "")
        _, order = await self._request(creds, "GET", f"/v1/orders/{order_id}")
        payment = first_payment(order)
        total = to_cents(payment.get("amount") or order.get("total_amount")) or 0
        body: dict[str, Any] | None
        if total > 0 and amount_cents == total:
            body = None
        elif 0 < amount_cents < total and payment.get("id"):
            body = {
                "transactions": [{"id": str(payment["id"]), "amount": to_amount_str(amount_cents)}]
            }
        else:
            raise ProviderError(
                "refund amount does not fit the Mercado Pago payment",
                code="refund_amount_mismatch",
                definitive=True,
            )
        _, data = await self._request(
            creds,
            "POST",
            f"/v1/orders/{order_id}/refund",
            body=body,
            idempotency=idempotency,
        )
        devolucoes = (data.get("transactions") or {}).get("refunds") or []
        primeira = devolucoes[0] if devolucoes and isinstance(devolucoes[0], dict) else {}
        refund_id = str(primeira.get("id")) if primeira.get("id") is not None else None
        status = str(primeira.get("status") or data.get("status") or "")
        if status in ("rejected", "cancelled", "failed"):
            return RefundResult(status="failed", provider_refund_id=refund_id, detail=status)
        if status in ("in_process", "pending"):
            return RefundResult(status="pending", provider_refund_id=refund_id, detail=status)
        return RefundResult(status="completed", provider_refund_id=refund_id, detail=status or None)

    # ------------------------------------------------------------------ webhooks
    def verify_webhook(self, creds: ProviderCredentials, inbound: InboundWebhook) -> WebhookVerdict:
        header = inbound.headers.get("x-signature")
        if not header:
            return WebhookVerdict.UNSUPPORTED  # a bare hint: processing fetches the payment
        secret = creds.secrets.get("webhook_secret")
        parts = _signature_parts(header)
        ts, sent = parts.get("ts"), parts.get("v1")
        if not secret or not ts or not sent:
            return WebhookVerdict.INVALID
        manifest = signature_manifest(
            inbound.query.get("data.id"), inbound.headers.get("x-request-id"), ts
        )
        expected = hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
        return (
            WebhookVerdict.VALID if hmac.compare_digest(expected, sent) else WebhookVerdict.INVALID
        )

    def parse_webhook(self, inbound: InboundWebhook) -> WebhookHint:
        body = json.loads(inbound.raw_body or b"{}")
        if not isinstance(body, dict):
            raise ValueError("webhook body is not an object")
        kind = str(
            body.get("type") or inbound.query.get("type") or inbound.query.get("topic") or ""
        )
        raw_data = body.get("data")
        data: dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
        resource = str(data.get("id") or inbound.query.get("data.id") or "") or None
        if resource is not None and not MP_ID.match(resource):
            resource = None
        # `merchant_order` é outro recurso, com id que não é o da nossa order: agir nele daria
        # 404 em toda notificação. Tópico de pagamento e de order passam; tópico vazio também,
        # porque o nome da notificação de order ainda não foi visto numa entrega real e inventá-lo
        # seria pior do que assumir que não se sabe. O id é conferido na consulta de todo jeito.
        if kind and kind not in ("payment", "order", "orders"):
            resource = None
        notification = str(body.get("id") or "")
        action = str(body.get("action") or "")
        dedupe = (
            f"{notification}:{action}:{resource}"
            if notification
            else hashlib.sha256(inbound.raw_body).hexdigest()
        )
        return WebhookHint(
            dedupe_key=dedupe,
            event_type=(action or kind or None),
            resource_id=resource,
            provider_payment_id=resource,
        )

    # ------------------------------------------------------------------ setup
    async def test_credentials(self, creds: ProviderCredentials) -> CredentialTest:
        if not creds.public_config.get("public_key"):
            return CredentialTest(ok=False, detail="public key ausente")
        if not creds.secrets.get("webhook_secret"):
            return CredentialTest(ok=False, detail="assinatura secreta dos webhooks ausente")
        # Sonda de leitura numa order que não existe: nada é criado na conta da loja. Cada
        # resposta diz uma coisa diferente, e o que não distingue token de permissão é relatado
        # como "não deu para testar" — reprovar sem ter testado manda procurar no lugar errado.
        try:
            await self._request(creds, "GET", "/v1/orders/ORD00000000000000000000000")
        except ProviderError as exc:
            if exc.http_status == 401:
                return CredentialTest(ok=False, detail="access token recusado pelo Mercado Pago")
            if exc.http_status == 403:
                return CredentialTest(
                    ok=False,
                    detail=(
                        "O Mercado Pago leu o token mas não liberou a API de pedidos para esta "
                        "conta. Confira se a aplicação é do tipo Checkout Transparente com a "
                        "Orders API habilitada."
                    ),
                )
            if exc.http_status == 404:
                return CredentialTest(ok=True, detail="access token aceito")
            return CredentialTest(ok=False, detail=f"Não deu para testar agora ({exc.code}).")
        return CredentialTest(ok=True, detail="access token aceito")


def _checked_id(value: str) -> str:
    if not MP_ID.match(value):
        raise ProviderError("invalid Mercado Pago payment id", code="bad_id", definitive=True)
    return value


def _orders_error(data: Mapping[str, Any], field: str) -> str | None:
    """A recusa da Orders API vem em `{"errors": [{"code", "message", "details"}]}`, não no
    `message`/`cause` da API de Pagamentos. A mensagem leva o código junto, porque "Minimum
    items" sozinho não diz qual regra foi."""
    errors = data.get("errors")
    first = errors[0] if isinstance(errors, list) and errors else None
    if not isinstance(first, Mapping) or not first.get("code"):
        return None
    if field == "code":
        return str(first["code"])
    message = first.get("message")
    return f"{first['code']}: {message}" if message else str(first["code"])


def _error_code(data: Mapping[str, Any], status: int) -> str:
    cause = data.get("cause")
    if isinstance(cause, list) and cause and isinstance(cause[0], dict) and cause[0].get("code"):
        return f"mp_{cause[0]['code']}"[:64]
    if status in (401, 403):
        return "credentials_refused"
    orders_code = _orders_error(data, "code")
    if orders_code:
        return f"mp_{orders_code}"[:64]
    return str(data.get("error") or f"http_{status}")[:64]
