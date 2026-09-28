"""PagBank (PagSeguro) pelo Checkout: a pessoa paga na página do PagBank, na conta da loja.

Contrato conferido na referência do PagBank em 28/09/2026:
- `POST /checkouts` {reference_id, items[{name, quantity, unit_amount (centavos)}], customer,
  payment_methods[{type}], redirect_url, notification_urls, payment_notification_urls} →
  {id: "CHEC_…", status, links[{rel: "PAY"|"SELF", href}]}. O link `PAY` é para onde o cliente vai.
- `GET /charges/{charge_id}` → {id, reference_id, status, amount{value, summary{total, paid,
  refunded}}, payment_method, paid_at}. Status: AUTHORIZED, PAID, IN_ANALYSIS, DECLINED, CANCELED.
- Webhook em `payment_notification_urls` com o mesmo corpo da resposta síncrona, assinado no
  header `x-authenticity-token` = SHA256("{token}-{corpo}").

Duas coisas que moldaram o código:

*A assinatura do webhook não é confiável em sandbox* — a própria comunidade do PagBank relata o
header ausente lá. Por isso ausência vira `UNSUPPORTED` (o serviço trata como aviso não
autenticado) em vez de rejeição: o aviso é só uma dica, e quem decide é o `GET /charges`.

*O checkout não sabe se foi pago.* `GET /checkouts/{id}` devolve o estado do **checkout**
(ativo/inativo), não do dinheiro. Quem tem a verdade é a cobrança, e o id dela chega pelo aviso.
Sem aviso ainda, `fetch_status` responde "esperando" em vez de inventar.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from typing import Any

import httpx

from app.core.config import settings
from app.payments.models import PaymentStatus
from app.payments.provider import (
    ChargeRequest,
    ChargeResult,
    CredentialTest,
    InboundWebhook,
    ProviderCapabilities,
    ProviderCredentials,
    ProviderError,
    ProviderRef,
    RefundResult,
    WebhookHint,
    WebhookVerdict,
)

CHARGE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
#: O que a página do PagBank oferece. A loja pode restringir em `public_config.payment_methods`.
ALL_METHODS = ("CREDIT_CARD", "DEBIT_CARD", "PIX", "BOLETO")
_STATUS = {
    "PAID": PaymentStatus.APPROVED,
    "AVAILABLE": PaymentStatus.APPROVED,
    "AUTHORIZED": PaymentStatus.REQUIRES_ACTION,  # pré-autorizado: ainda não é dinheiro
    "IN_ANALYSIS": PaymentStatus.REQUIRES_ACTION,
    "WAITING": PaymentStatus.REQUIRES_ACTION,
    "DECLINED": PaymentStatus.REJECTED,
    "CANCELED": PaymentStatus.CANCELLED,
}


def _token(creds: ProviderCredentials) -> str:
    token = str(creds.secrets.get("access_token") or "").strip()
    if not token:
        raise ProviderError("PagBank token ausente", code="token_missing", definitive=True)
    return token


def _base(creds: ProviderCredentials) -> str:
    return settings.pagbank_sandbox_api_base if creds.sandbox else settings.pagbank_api_base


def _methods(creds: ProviderCredentials) -> list[dict[str, str]]:
    escolhidos = creds.public_config.get("payment_methods")
    if isinstance(escolhidos, list) and escolhidos:
        validos = [str(m).upper() for m in escolhidos if str(m).upper() in ALL_METHODS]
        if validos:
            return [{"type": m} for m in validos]
    return [{"type": m} for m in ALL_METHODS]


def checkout_body(req: ChargeRequest, methods: list[dict[str, str]]) -> dict[str, Any]:
    """Uma linha só: a loja já somou produtos, frete e desconto no total do pedido.

    Mandar o carrinho detalhado faria o PagBank recalcular e discordar do nosso total por um
    centavo de arredondamento — e aí o pagamento não bate com o pedido.
    """
    corpo: dict[str, Any] = {
        "reference_id": req.provider_reference[:64],
        "customer_modifiable": False,
        "items": [
            {
                "reference_id": str(req.order_number)[:64],
                "name": req.description[:100],
                "quantity": 1,
                "unit_amount": req.amount_cents,
            }
        ],
        "payment_methods": methods,
    }
    cliente = {k: v for k, v in (("name", req.payer_name), ("email", req.payer_email)) if v}
    if cliente:
        corpo["customer"] = cliente
    if req.return_url:
        corpo["redirect_url"] = req.return_url[:1000]
    if req.notification_url:
        corpo["notification_urls"] = [req.notification_url[:1000]]
        corpo["payment_notification_urls"] = [req.notification_url[:1000]]
    if req.expires_at is not None:
        corpo["expiration_date"] = req.expires_at.isoformat()
    return corpo


def pay_link(data: Mapping[str, Any]) -> str | None:
    for link in data.get("links") or []:
        if isinstance(link, dict) and str(link.get("rel") or "").upper() == "PAY":
            href = str(link.get("href") or "")
            if href.startswith("https://"):
                return href[:1000]
    return None


def first_charge(body: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """A cobrança dentro do aviso: ora vem em `charges`, ora o corpo já é a própria cobrança."""
    charges = body.get("charges")
    if isinstance(charges, list):
        for item in charges:
            if isinstance(item, dict) and item.get("id"):
                return item
    if body.get("id") and body.get("status") and "amount" in body:
        return body
    return None


def _cents(amount: Any) -> int | None:
    if not isinstance(amount, dict):
        return None
    resumo = amount.get("summary")
    bruto = resumo.get("paid") if isinstance(resumo, dict) else None
    if bruto is None:
        bruto = amount.get("value")
    if not isinstance(bruto, (int, float, str)):
        return None
    try:
        return int(bruto)
    except (TypeError, ValueError):
        return None


class PagBankProvider:
    name = "pagbank"
    capabilities = ProviderCapabilities(
        mode="redirect",
        methods=("link",),
        cancel=False,
        refunds=False,
        signed_webhooks=True,
        required_secrets=("access_token",),
        required_public=(),
    )

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    async def _request(
        self,
        creds: ProviderCredentials,
        method: str,
        path: str,
        body: Mapping[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        timeout = httpx.Timeout(settings.payments_http_timeout_seconds, connect=5.0)
        headers = {
            "Authorization": f"Bearer {_token(creds)}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(
            base_url=_base(creds), timeout=timeout, transport=self._transport
        ) as client:
            try:
                response = await client.request(method, path, json=body, headers=headers)
            except httpx.TimeoutException as exc:
                raise ProviderError("PagBank timed out", code="timeout") from exc
            except httpx.HTTPError as exc:
                raise ProviderError("PagBank unreachable", code="network") from exc
        try:
            data = response.json()
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        status = response.status_code
        if status >= 500 or status == 429:
            raise ProviderError(
                f"PagBank answered {status}", http_status=status, code="unavailable"
            )
        if status >= 400:
            raise ProviderError(
                _error_message(data) or f"PagBank refused ({status})",
                http_status=status,
                code=f"http_{status}",
                definitive=True,
            )
        return status, data

    async def create_charge(self, creds: ProviderCredentials, req: ChargeRequest) -> ChargeResult:
        if req.method != "link":
            raise ProviderError("method not offered", code="method_not_offered", definitive=True)
        status, data = await self._request(
            creds, "POST", "/checkouts", checkout_body(req, _methods(creds))
        )
        url = pay_link(data)
        if not url:
            raise ProviderError("PagBank returned no link", http_status=status, code="no_link")
        return ChargeResult(
            status=PaymentStatus.REQUIRES_ACTION,
            provider_payment_id=str(data.get("id") or "") or None,
            provider_status=str(data.get("status") or "") or None,
            provider_reference=req.provider_reference,
            checkout_url=url,
            raw_summary={"checkout_id": data.get("id")},
            http_status=status,
        )

    async def fetch_status(self, creds: ProviderCredentials, ref: ProviderRef) -> ChargeResult:
        charge = str(ref.hints.get("charge_id") or "")
        if not CHARGE_ID.match(charge):
            # Ainda não houve aviso: o checkout existe, o dinheiro não. Segue esperando.
            return ChargeResult(status=PaymentStatus.REQUIRES_ACTION, provider_payment_id=None)
        status, data = await self._request(creds, "GET", f"/charges/{charge}")
        bruto = str(data.get("status") or "").upper()
        destino = _STATUS.get(bruto, PaymentStatus.PENDING)
        pago = _cents(data.get("amount")) if destino == PaymentStatus.APPROVED else None
        metodo = data.get("payment_method")
        tipo = str(metodo.get("type") or "") if isinstance(metodo, dict) else ""
        return ChargeResult(
            status=destino,
            provider_payment_id=charge,
            provider_status=bruto or None,
            provider_status_detail=tipo or None,
            provider_reference=str(data.get("reference_id") or "") or None,
            paid_amount_cents=pago,
            payer={"method": tipo} if tipo else None,
            raw_summary={"charge_id": charge, "status": bruto},
            http_status=status,
        )

    async def cancel(self, creds: ProviderCredentials, ref: ProviderRef) -> ChargeResult | None:
        # Checkout não pago simplesmente expira; não há o que cancelar do nosso lado.
        return None

    async def refund(
        self, creds: ProviderCredentials, ref: ProviderRef, amount_cents: int, *, idempotency: str
    ) -> RefundResult:
        # `capabilities.refunds` é False: devolução sai pelo app do PagBank, com evidência,
        # como já acontece na InfinitePay. Automatizar isso é outra conversa.
        raise ProviderError("PagBank refund not wired", code="refund_unsupported", definitive=True)

    def verify_webhook(self, creds: ProviderCredentials, inbound: InboundWebhook) -> WebhookVerdict:
        enviado = inbound.headers.get("x-authenticity-token") or inbound.headers.get(
            "X-Authenticity-Token"
        )
        if not enviado:
            # Sandbox não manda o header. Não rejeitamos por isso: o aviso é dica, e a verdade
            # vem do GET /charges com o token da loja.
            return WebhookVerdict.UNSUPPORTED
        esperado = hashlib.sha256(
            f"{_token(creds)}-".encode() + (inbound.raw_body or b"")
        ).hexdigest()
        return (
            WebhookVerdict.VALID
            if hmac.compare_digest(esperado, str(enviado).strip())
            else WebhookVerdict.INVALID
        )

    def parse_webhook(self, inbound: InboundWebhook) -> WebhookHint:
        body = json.loads(inbound.raw_body or b"{}")
        if not isinstance(body, dict):
            raise ValueError("webhook body is not an object")
        cobranca = first_charge(body)
        charge_id = str((cobranca or {}).get("id") or "")
        referencia = str(body.get("reference_id") or (cobranca or {}).get("reference_id") or "")
        digest = hashlib.sha256(inbound.raw_body).hexdigest()
        hints = {"charge_id": charge_id} if CHARGE_ID.match(charge_id) else {}
        return WebhookHint(
            dedupe_key=f"{charge_id or 'none'}:{digest[:32]}",
            event_type=str((cobranca or {}).get("status") or "notification"),
            resource_id=charge_id or None,
            provider_payment_id=None,  # o id do checkout é outro; quem paga é a cobrança
            hints=hints,
            provider_reference=referencia or None,
        )

    async def test_credentials(self, creds: ProviderCredentials) -> CredentialTest:
        """Sonda de leitura: pergunta por uma cobrança que não existe.

        401/403 = token errado. 404 = token aceito (a conta respondeu "não achei"). Nada é
        criado na conta da loja, que é o ponto — testar credencial não pode gerar cobrança.
        """
        try:
            await self._request(creds, "GET", "/charges/CHAR_TESTE_MUHBIANCO")
        except ProviderError as exc:
            if exc.http_status in (401, 403):
                return CredentialTest(ok=False, detail="Token recusado pelo PagBank")
            if exc.http_status == 404:
                return CredentialTest(ok=True, detail="Token aceito")
            return CredentialTest(ok=False, detail=str(exc)[:200])
        return CredentialTest(ok=True, detail="Token aceito")


def _error_message(data: Mapping[str, Any]) -> str:
    erros = data.get("error_messages")
    if isinstance(erros, list) and erros:
        primeiro = erros[0]
        if isinstance(primeiro, dict):
            descricao = str(primeiro.get("description") or primeiro.get("message") or "")
            parametro = str(primeiro.get("parameter_name") or "")
            return f"{descricao} ({parametro})"[:200] if parametro else descricao[:200]
    return str(data.get("message") or "")[:200]
