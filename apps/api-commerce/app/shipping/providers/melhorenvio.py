"""Melhor Envio: Correios (PAC/SEDEX), Jadlog e Azul Cargo por uma API só (ADR 0015).

Fluxo deles, e é o que esta classe encapsula:
cotar (`/shipment/calculate`) → carrinho (`/cart`) → comprar (`/shipment/checkout`) →
gerar etiqueta (`/shipment/generate`) → imprimir (`/shipment/print`) → rastrear
(`/shipment/tracking`). Todos os caminhos e formatos foram conferidos na referência deles;
`cancel` ficou de fora de propósito, porque não confirmei o contrato dessa chamada.

Unidades: a API fala **centímetro, quilo e real**; o nosso domínio fala milímetro, grama e
centavo. A conversão mora aqui e em nenhum outro lugar.

Conferido no sandbox em 04/10/2026 (docs/13-frete-v2.md, "Fatos verificados"):

- seguro por volume só funciona em `volumes[].insurance`; `options.insurance_value` vai só
  para o 1º volume, e `volumes[].insurance_value` é ignorado;
- uma cotação com N volumes já traz o preço de cada transportadora para a remessa inteira —
  nos Correios, a soma por volume, com o preço de cada um em `packages[].price`;
- a API arredonda as medidas para inteiro (0,5 cm virou 0 nos Correios): mandamos centímetros
  inteiros **arredondados para cima**, para nunca cotar uma caixa menor que a real;
- `height`/`length` é a grafia certa (a do OpenAPI, `heigth`/`lenght`, é recusada).

Idempotência: a API não tem chave de idempotência. Quem impede etiqueta comprada duas vezes é a
linha de `order_shipments` com o pedido único — aqui só mandamos a nossa referência em `tags`,
para o operador achar o pedido do lado deles.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.shipping.provider import (
    CredentialTest,
    InsufficientBalanceError,
    Parcel,
    QuoteRequest,
    ShipmentRequest,
    ShipmentResult,
    ShippingCapabilities,
    ShippingCredentials,
    ShippingOption,
    ShippingParty,
    ShippingProviderError,
    TrackingEvent,
    TrackingResult,
    TrackingStatus,
)

logger = get_logger(__name__)

PRODUCTION_URL = "https://melhorenvio.com.br"
SANDBOX_URL = "https://sandbox.melhorenvio.com.br"

_CALCULATE = "/api/v2/me/shipment/calculate"
_CART = "/api/v2/me/cart"
_CHECKOUT = "/api/v2/me/shipment/checkout"
_GENERATE = "/api/v2/me/shipment/generate"
_PRINT = "/api/v2/me/shipment/print"
_TRACKING = "/api/v2/me/shipment/tracking"
_BALANCE = "/api/v2/me/balance"

#: Ciclo de vida deles → o nosso. O que não estiver aqui vira "unknown" em vez de chute.
_STATUS: dict[str, TrackingStatus] = {
    "pending": "created",
    "paid": "created",
    "generated": "created",
    "released": "created",
    "posted": "posted",
    "in_transit": "in_transit",
    "delivered": "delivered",
    "returned": "returned",
    "undelivered": "returned",
    "canceled": "cancelled",
    "cancelled": "cancelled",
}


#: Quem aceita vários volumes numa etiqueta só, na compra. Correios (1, 2, 17), J&T, Loggi,
#: .package Centralizado e Total Express exigem uma inserção por volume (docs de compra e
#: central de ajuda). Jadlog aceita a remessa com vários volumes; o teto de 5 é da central de
#: ajuda e se confirma na compra (F7). O que não estiver aqui conta como 1.
_MULTI_VOLUME = {"jadlog": 5}


#: Seguro mínimo na compra: com menos de R$ 1,00 a Jadlog recusa a inserção (sandbox).
MIN_INSURANCE_CENTS = 100


def _cm(mm: int) -> int:
    """Milímetros → centímetros inteiros, para cima (a API arredonda e pode cortar para baixo)."""
    return max(1, -(-mm // 10))


def _kg(grams: int) -> float:
    return round(max(grams, 1) / 1000, 3)


def _reais(cents: int) -> float:
    return round(cents / 100, 2)


def _cents(value: Any) -> int:
    try:
        return round(float(value) * 100)
    except (TypeError, ValueError):
        return 0


def volume(parcel: Parcel, *, insured: bool = True) -> dict[str, Any]:
    """Um volume nosso no formato deles (cm/kg), com o seguro **dele** (`insurance`)."""
    corpo: dict[str, Any] = {
        "height": _cm(parcel.height_mm),
        "width": _cm(parcel.width_mm),
        "length": _cm(parcel.depth_mm),
        "weight": _kg(parcel.weight_grams),
    }
    if insured:
        corpo["insurance"] = _reais(parcel.value_cents)
    return corpo


def party(who: ShippingParty) -> dict[str, Any]:
    documento = "".join(ch for ch in (who.document or "") if ch.isdigit())
    corpo: dict[str, Any] = {
        "name": who.name[:60],
        "email": who.email or "",
        "phone": "".join(ch for ch in (who.phone or "") if ch.isdigit()),
        "address": who.street[:60],
        "complement": who.complement or "",
        "number": who.number[:10],
        "district": who.district[:60],
        "city": who.city[:60],
        "postal_code": who.postal_code,
        "state_abbr": who.state.upper()[:2],
        "country_id": "BR",
    }
    # CPF tem 11 dígitos, CNPJ tem 14: o campo é outro, e mandar no errado o provedor recusa.
    if len(documento) == 14:
        corpo["company_document"] = documento
    elif documento:
        corpo["document"] = documento
    return corpo


def parse_options(payload: Any) -> tuple[ShippingOption, ...]:
    """Resposta da cotação → opções nossas.

    `custom_price` e `custom_delivery_time` são o que a loja configurou no painel do Melhor
    Envio; a referência deles manda usar esses, e não o preço de tabela.
    """
    if not isinstance(payload, list):
        return ()
    opcoes: list[ShippingOption] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        empresa = item.get("company") if isinstance(item.get("company"), dict) else {}
        erro = item.get("error")
        preco = item.get("custom_price") or item.get("price")
        prazo = item.get("custom_delivery_time") or item.get("delivery_time")
        faixa = item.get("custom_delivery_range") or item.get("delivery_range")
        faixa = faixa if isinstance(faixa, dict) else {}
        bruto_pacotes = item.get("packages")
        pacotes: list[Any] = bruto_pacotes if isinstance(bruto_pacotes, list) else []
        precos = [p.get("price") for p in pacotes if isinstance(p, dict)]
        transportadora = str((empresa or {}).get("name") or "")
        opcoes.append(
            ShippingOption(
                service_code=str(item.get("id") or ""),
                service_name=str(item.get("name") or ""),
                carrier=transportadora,
                price_cents=_cents(preco),
                delivery_days=_int(prazo),
                error=str(erro) if erro else None,
                delivery_min=_int(faixa.get("min")),
                delivery_max=_int(faixa.get("max")),
                # Só quando todo volume veio com preço (Correios); senão a cobrança é da remessa.
                parcel_prices_cents=tuple(_cents(p) for p in precos)
                if precos and all(p is not None for p in precos)
                else (),
                multi_volume_max=_MULTI_VOLUME.get(transportadora.casefold(), 1),
            )
        )
    return tuple(opcoes)


def _int(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float, str)) and str(value).isdigit() else None


class MelhorEnvioProvider:
    name = "melhorenvio"

    @property
    def capabilities(self) -> ShippingCapabilities:
        # Os códigos de serviço são os ids numéricos deles, descobertos na cotação: a loja não
        # digita nada, escolhe na tela o que voltou.
        return ShippingCapabilities(
            services=(),
            required_secrets=("access_token",),
            supports_label=True,
            supports_tracking=True,
            supports_cancel=False,
        )

    def base_url(self, credentials: ShippingCredentials) -> str:
        return SANDBOX_URL if credentials.sandbox else PRODUCTION_URL

    async def quote(
        self, credentials: ShippingCredentials, request: QuoteRequest
    ) -> tuple[ShippingOption, ...]:
        corpo: dict[str, Any] = {
            "from": {"postal_code": request.origin_postal_code},
            "to": {"postal_code": request.destination_postal_code},
            # Seguro dentro de cada volume: em `options` ele ia todo para o 1º (sandbox).
            "volumes": [volume(p) for p in request.parcels],
            "options": {"receipt": False, "own_hand": False},
        }
        if request.services:
            corpo["services"] = ",".join(request.services)
        dados = await self._post(credentials, _CALCULATE, corpo)
        return parse_options(dados)

    async def ship(
        self, credentials: ShippingCredentials, request: ShipmentRequest
    ) -> ShipmentResult:
        """Carrinho → compra → etiqueta → link de impressão, nessa ordem.

        Cada passo depende do anterior; se a compra passa e a etiqueta falha, a remessa existe
        e foi paga — por isso quem chama guarda o id do provedor antes de seguir.
        """
        carrinho = await self._post(credentials, _CART, self._cart_body(request))
        if not isinstance(carrinho, dict) or not carrinho.get("id"):
            raise ShippingProviderError("carrinho sem id", definitive=True)
        shipment_id = str(carrinho["id"])
        custo = _cents(carrinho.get("price") or carrinho.get("quote") or 0)
        await self._checkout(credentials, shipment_id)
        await self._post(credentials, _GENERATE, {"orders": [shipment_id]})
        etiqueta = await self._label_url(credentials, shipment_id)
        rastreio = await self._tracking_code(credentials, shipment_id)
        return ShipmentResult(
            provider_shipment_id=shipment_id,
            tracking_code=rastreio,
            carrier=str(carrinho.get("company_name") or ""),
            service_code=str(carrinho.get("service_id") or request.service_code),
            cost_cents=custo,
            label_url=etiqueta,
            raw={"protocol": carrinho.get("protocol"), "status": carrinho.get("status")},
        )

    async def track(
        self, credentials: ShippingCredentials, provider_shipment_id: str
    ) -> TrackingResult:
        dados = await self._post(credentials, _TRACKING, {"orders": [provider_shipment_id]})
        item = dados.get(provider_shipment_id) if isinstance(dados, dict) else None
        if not isinstance(item, dict):
            raise ShippingProviderError("remessa sem rastreio", definitive=True)
        bruto = str(item.get("status") or "").lower()
        status = _STATUS.get(bruto, "unknown")
        if status == "unknown" and bruto:
            logger.info("Status de rastreio desconhecido", extra={"status": bruto})
        entregue = _quando(item.get("delivered_at"))
        eventos = tuple(
            TrackingEvent(at=quando, status=estado, description=rotulo)
            for rotulo, estado, quando in _MARCOS(item, entregue)
            if quando is not None
        )
        return TrackingResult(
            status=status,
            tracking_code=str(item.get("tracking") or item.get("melhorenvio_tracking") or ""),
            events=eventos,
            delivered_at=entregue,
        )

    async def cancel(self, credentials: ShippingCredentials, provider_shipment_id: str) -> bool:
        # Não confirmei o contrato de cancelamento na referência deles; prefiro não chutar uma
        # chamada que mexe em dinheiro. Quem precisar cancela no painel do Melhor Envio.
        raise ShippingProviderError("cancelamento não implementado", definitive=True)

    async def test_credentials(self, credentials: ShippingCredentials) -> CredentialTest:
        try:
            dados = await self._request(credentials, "GET", _BALANCE, None)
        except ShippingProviderError as exc:
            return CredentialTest(ok=False, detail=str(exc)[:200])
        saldo = dados.get("balance") if isinstance(dados, dict) else None
        return CredentialTest(ok=True, detail=f"saldo {saldo}" if saldo is not None else "ok")

    # ------------------------------------------------------------------ interno

    def _cart_body(self, request: ShipmentRequest) -> dict[str, Any]:
        corpo: dict[str, Any] = {
            "service": int(request.service_code)
            if request.service_code.isdigit()
            else request.service_code,
            "from": party(request.sender),
            "to": party(request.recipient),
            "products": [
                {
                    "name": f"Pedido {request.order_number}" if request.order_number else "Pedido",
                    "quantity": "1",
                    "unitary_value": _reais(sum(p.value_cents for p in request.parcels)),
                }
            ],
            # No carrinho é o contrário da cotação (sandbox, 04/10/2026): `volumes[].insurance`
            # é ignorado e o seguro vale em `options`; a Jadlog recusa abaixo de R$ 1,00.
            "volumes": [volume(p, insured=False) for p in request.parcels],
            "options": {
                "insurance_value": _reais(max(request.insurance_cents, MIN_INSURANCE_CENTS)),
                "receipt": False,
                "own_hand": False,
                "reverse": False,
                "non_commercial": True,
                "platform": "MuhBianco",
                "tags": [{"tag": request.reference}],
            },
        }
        if request.notes:
            corpo["options"]["reminder"] = request.notes[:100]
        return corpo

    async def _checkout(self, credentials: ShippingCredentials, shipment_id: str) -> None:
        try:
            await self._post(credentials, _CHECKOUT, {"orders": [shipment_id]})
        except ShippingProviderError as exc:
            if exc.http_status in {402, 422} and "saldo" in str(exc).lower():
                raise InsufficientBalanceError() from exc
            raise

    async def _label_url(self, credentials: ShippingCredentials, shipment_id: str) -> str | None:
        dados = await self._post(credentials, _PRINT, {"orders": [shipment_id], "mode": "private"})
        url = dados.get("url") if isinstance(dados, dict) else None
        return str(url) if url else None

    async def _tracking_code(self, credentials: ShippingCredentials, shipment_id: str) -> str:
        try:
            return (await self.track(credentials, shipment_id)).tracking_code
        except ShippingProviderError:
            # Etiqueta comprada vale mais do que o código: ele chega no primeiro rastreio.
            logger.warning("Remessa criada sem código de rastreio ainda")
            return ""

    async def _post(self, credentials: ShippingCredentials, path: str, body: dict[str, Any]) -> Any:
        return await self._request(credentials, "POST", path, body)

    async def _request(
        self,
        credentials: ShippingCredentials,
        method: str,
        path: str,
        body: dict[str, Any] | None,
    ) -> Any:
        token = credentials.secrets.get("access_token", "")
        if not token:
            raise ShippingProviderError("sem token do Melhor Envio", definitive=True)
        url = self.base_url(credentials) + path
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            # A API deles exige identificação com contato; sem isso responde 401/403.
            "User-Agent": settings.shipping_user_agent,
        }
        timeout = httpx.Timeout(settings.shipping_timeout_seconds, connect=5.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.request(method, url, json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise ShippingProviderError(f"rede: {type(exc).__name__}") from exc
        if response.status_code >= 400:
            detalhe = response.text[:300]
            logger.warning(
                "Melhor Envio recusou",
                extra={"path": path, "status": response.status_code, "body": detalhe},
            )
            raise ShippingProviderError(
                detalhe or "recusado",
                http_status=response.status_code,
                definitive=400 <= response.status_code < 500 and response.status_code != 429,
            )
        return response.json() if response.content else {}


def _MARCOS(
    item: dict[str, Any], entregue: datetime | None
) -> tuple[tuple[str, TrackingStatus, datetime | None], ...]:
    """Os dois carimbos que a resposta deles traz viram eventos da linha do tempo."""
    return (
        ("postado", "posted", _quando(item.get("posted_at"))),
        ("entregue", "delivered", entregue),
    )


def _quando(value: Any) -> datetime | None:
    if not value:
        return None
    texto = str(value).replace("Z", "+00:00")
    for formato in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S%z"):
        try:
            quando = datetime.strptime(texto, formato)
        except ValueError:
            continue
        return quando if quando.tzinfo else quando.replace(tzinfo=UTC)
    return None
