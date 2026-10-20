"""Frenet: cotação por volumes, etiqueta e rastreio pela API whitelabel (docs/14 §4).

O que está aqui foi medido pela sonda `scripts/frenet_probe.py` (14 e 20/10/2026), não lido de
segunda mão:

- a API de etiquetas (`whitelabel.frenet.com.br/v1`) exige **dois** cabeçalhos: `token` (o da
  conta do lojista, que ele cola na tela de Envio) e `x-partner-token` (o da plataforma, que fica
  só na configuração do servidor). Só com o do lojista, tudo dá 401 — até ler o saldo;
- as respostas vêm em camelCase, apesar de a documentação escrever PascalCase (os pedidos
  aceitam as duas);
- `POST /quotes` cota **volumes** (kg e cm) e devolve preço em número;
- `POST /shipments` exige `Order.Created` como `2026-10-20T18:40:23Z` (sem fração; o
  `isoformat()` foi recusado) e o e-mail do remetente (erro 2004). Cada envio leva **um** volume:
  pedido com vários volumes vira uma etiqueta por volume (`multi_volume_max = 1`);
- o envio criado fica em `shipmentStatus` 1 (criado) até o pagamento; `DELETE` responde 204 e o
  deixa em 9 (excluído). A etiqueta só tem link depois de pago.

**Não medido** (precisa de saldo na conta): a resposta do `POST /shipments/checkout` e o link da
etiqueta depois do pagamento. Por isso o pagamento confere a situação do envio antes de dizer
"pago", e nada aqui levanta erro depois de um pagamento confirmado — erro depois de pagar faria o
operador tentar de novo e comprar duas vezes.

A Frenet não devolve código de rastreio, só um link (`trackingUrl`); e a situação do envio vai
até "postado" — "entregue ao cliente" não existe nessa API.
"""

from __future__ import annotations

import asyncio
import json
import math
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
    ServiceInfo,
    ShipmentRequest,
    ShipmentResult,
    ShippingCapabilities,
    ShippingCredentials,
    ShippingOption,
    ShippingParty,
    ShippingProviderError,
    TrackingResult,
    TrackingStatus,
)

logger = get_logger(__name__)

#: `shipmentStatus` (referência `getShipmentAsync`): 1 criado, 2 pendente de pagamento, 3 falha
#: no pagamento, 4 pago, 5 postado, 6 cancelamento agendado, 7 cancelado, 9 excluído, 18 entregue
#: no ponto de postagem.
PAID = frozenset({4, 5, 18})
_STATUS: dict[int, TrackingStatus] = {
    1: "created",
    2: "created",
    3: "created",
    4: "created",
    5: "posted",
    18: "posted",
    6: "cancelled",
    7: "cancelled",
    9: "cancelled",
}
#: Saldo insuficiente (tabela de erros da Frenet).
INSUFFICIENT_BALANCE = 3000
#: O link da etiqueta pode demorar um instante depois do pagamento.
LABEL_WAITS = (1.0, 2.0, 3.0)


def _pega(dados: Any, chave: str) -> Any:
    """Lê `Chave` ou `chave`: a Frenet documenta PascalCase e responde camelCase."""
    if not isinstance(dados, dict):
        return None
    if chave in dados:
        return dados[chave]
    return dados.get(chave[:1].lower() + chave[1:])


def _cm(mm: int) -> int:
    return max(1, math.ceil(mm / 10))


def _reais(cents: int) -> float:
    return round(max(cents, 0) / 100, 2)


def _cents(valor: Any) -> int:
    try:
        return round(float(valor) * 100)
    except (TypeError, ValueError):
        return 0


def _volume(p: Parcel) -> dict[str, Any]:
    return {
        "Weight": p.weight_kg,
        "Length": _cm(p.depth_mm),
        "Height": _cm(p.height_mm),
        "Width": _cm(p.width_mm),
    }


def _party(parte: ShippingParty) -> dict[str, Any]:
    endereco = {
        "ZipCode": "".join(ch for ch in parte.postal_code if ch.isdigit()),
        "Street": parte.street,
        "AddressNumber": parte.number,
        "AddressComplement": parte.complement or "",
        "AddressQuarter": parte.district,
        "City": parte.city,
        "AddressState": parte.state,
        "Country": "BR",
    }
    corpo: dict[str, Any] = {"Name": parte.name, "Address": endereco}
    if parte.email:
        corpo["Email"] = parte.email
    if parte.phone:
        corpo["Phone"] = "".join(ch for ch in parte.phone if ch.isdigit())
    if parte.document:
        corpo["Document"] = "".join(ch for ch in parte.document if ch.isdigit())
    return corpo


def _codes(dados: Any) -> list[tuple[int | None, str]]:
    """Os erros de qualquer forma de resposta da Frenet: `details[]`, `errors{}` (validação do
    ASP.NET) ou `items[].errors[]` (lote)."""
    saida: list[tuple[int | None, str]] = []
    if not isinstance(dados, dict):
        return saida
    for detalhe in _pega(dados, "Details") or []:
        if isinstance(detalhe, dict):
            codigo = _pega(detalhe, "Code")
            saida.append(
                (codigo if isinstance(codigo, int) else None, str(_pega(detalhe, "Message") or ""))
            )
    erros = _pega(dados, "Errors")
    if isinstance(erros, dict):
        for mensagens in erros.values():
            for m in mensagens if isinstance(mensagens, list) else [mensagens]:
                saida.append((None, str(m)))
    for item in _pega(dados, "Items") or []:
        for erro in _pega(item, "Errors") or []:
            if isinstance(erro, dict):
                codigo = _pega(erro, "Code")
                saida.append(
                    (codigo if isinstance(codigo, int) else None, str(_pega(erro, "Message") or ""))
                )
    mensagem = _pega(dados, "Message")
    if isinstance(mensagem, str) and mensagem.strip() and not saida:
        saida.append((None, mensagem))
    return saida


def _motivo(dados: Any) -> str | None:
    frases = [m.strip() for _, m in _codes(dados) if m and m.strip()]
    return " ".join(dict.fromkeys(frases))[:200] or None


class FrenetProvider:
    name = "frenet"

    @property
    def available(self) -> bool:
        """Sem o token de parceiro, a API de etiquetas recusa tudo: a Frenet nem aparece."""
        return bool(settings.frenet_partner_token.get_secret_value())

    @property
    def capabilities(self) -> ShippingCapabilities:
        return ShippingCapabilities(
            services=(),
            required_secrets=("access_token",),
            supports_label=True,
            supports_tracking=True,
            supports_cancel=True,
        )

    # ------------------------------------------------------------------ cotar
    async def quote(
        self, credentials: ShippingCredentials, request: QuoteRequest
    ) -> tuple[ShippingOption, ...]:
        dados = await self._quote_raw(credentials, request.origin_postal_code, request)
        opcoes: list[ShippingOption] = []
        for o in _pega(dados, "Quotations") or []:
            codigo = str(_pega(o, "ShippingServiceCode") or "")
            if not codigo or (request.services and codigo not in request.services):
                continue
            erro = bool(_pega(o, "Error"))
            prazo = _pega(o, "DeliveryTime")
            opcoes.append(
                ShippingOption(
                    service_code=codigo,
                    service_name=str(_pega(o, "ShippingServiceName") or codigo),
                    carrier=str(_pega(o, "Carrier") or ""),
                    price_cents=0 if erro else _cents(_pega(o, "ShippingPrice")),
                    delivery_days=int(prazo) if isinstance(prazo, int | float) else None,
                    error=(str(_pega(o, "Msg") or "indisponível") if erro else None),
                    # Um volume por envio (medido): cada volume é uma etiqueta.
                    multi_volume_max=1,
                )
            )
        return tuple(opcoes)

    async def _quote_raw(
        self, credentials: ShippingCredentials, origem: str, request: QuoteRequest
    ) -> Any:
        valor = sum(p.value_cents for p in request.parcels)
        corpo = {
            "SenderZipCode": "".join(ch for ch in origem if ch.isdigit()),
            "RecipientZipCode": "".join(
                ch for ch in request.destination_postal_code if ch.isdigit()
            ),
            "RecipientCountry": "BR",
            "ShipmentItemValue": _reais(valor),
            "Services": {
                "DeclaredValue": valor > 0,
                "ReceiptNotification": False,
                "OwnHand": False,
            },
            "Volumes": [_volume(p) for p in request.parcels],
        }
        try:
            return await self._request(credentials, "POST", "/quotes", corpo)
        except ShippingProviderError as exc:
            # CEP sem atendimento: 404 com o código 1005 ("a cotação não retornou dados").
            if exc.http_status == 404:
                return {"quotations": []}
            raise

    # ------------------------------------------------------------------ etiqueta
    async def ship(
        self, credentials: ShippingCredentials, request: ShipmentRequest
    ) -> ShipmentResult:
        """Cria o envio, paga com o saldo da conta do lojista e lê a etiqueta.

        Uma etiqueta por chamada, de um volume só. Paga e confirmada, a etiqueta não volta como
        erro, mesmo que o link ainda não esteja pronto: erro depois de pagar faria o operador
        comprar de novo.
        """
        if len(request.parcels) != 1:
            raise ShippingProviderError(
                "a Frenet emite uma etiqueta por volume",
                definitive=True,
                reason="A Frenet emite uma etiqueta por volume.",
            )
        if not request.sender.email:
            raise ShippingProviderError(
                "remetente sem e-mail",
                definitive=True,
                reason="Informe o e-mail do remetente na origem do envio: a Frenet exige.",
            )
        volume = request.parcels[0]
        cotacao = await self._chosen_quote(credentials, request, volume)
        shipment_id = await self._create(credentials, request, volume, cotacao)
        await self._pay(credentials, shipment_id)
        etiqueta, rastreio = await self._label(credentials, shipment_id)
        return ShipmentResult(
            provider_shipment_id=str(shipment_id),
            # A Frenet não devolve código de rastreio: só o link (`raw.tracking_url`).
            tracking_code="",
            carrier=str(_pega(cotacao, "Carrier") or _pega(cotacao, "CarrierCode") or ""),
            service_code=request.service_code,
            cost_cents=_cents(_pega(cotacao, "ShippingPrice")),
            label_url=etiqueta,
            raw={"tracking_url": rastreio, "frenet_status": "pago"},
        )

    async def _chosen_quote(
        self, credentials: ShippingCredentials, request: ShipmentRequest, volume: Parcel
    ) -> dict[str, Any]:
        """Cota de novo o volume e pega o serviço escolhido: a criação leva a cotação junto
        (foi assim que a sonda criou envio com sucesso)."""
        dados = await self._quote_raw(
            credentials,
            request.sender.postal_code,
            QuoteRequest(
                origin_postal_code=request.sender.postal_code,
                destination_postal_code=request.recipient.postal_code,
                parcels=(volume,),
            ),
        )
        for o in _pega(dados, "Quotations") or []:
            if str(_pega(o, "ShippingServiceCode") or "") == request.service_code:
                if _pega(o, "Error"):
                    raise ShippingProviderError(
                        "serviço indisponível",
                        definitive=True,
                        reason=str(_pega(o, "Msg") or "Serviço indisponível para este envio."),
                    )
                return dict(o)
        raise ShippingProviderError(
            "serviço não cotado",
            definitive=True,
            reason="A Frenet não cotou este serviço para este volume agora.",
        )

    async def _create(
        self,
        credentials: ShippingCredentials,
        request: ShipmentRequest,
        volume: Parcel,
        cotacao: dict[str, Any],
    ) -> int:
        valor = max(volume.value_cents, request.insurance_cents)
        itens = [
            {
                "ItemId": str(n),
                "ProductName": item.name[:120],
                "Quantity": max(item.quantity, 1),
                "Price": _reais(item.unit_value_cents),
            }
            for n, item in enumerate(request.items, start=1)
        ] or [
            {
                "ItemId": "1",
                "ProductName": f"Pedido {request.order_number or ''}".strip(),
                "Quantity": 1,
                "Price": _reais(valor),
            }
        ]
        envio = [
            {
                "Order": {
                    "Id": request.reference[:60],
                    "Value": _reais(valor),
                    # Medido: sem fração e com Z. `isoformat()` foi recusado.
                    "Created": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "Items": itens,
                    "From": _party(request.sender),
                    "To": _party(request.recipient),
                },
                "Volumes": dict(
                    _volume(volume), VolumeId=1, Price=_reais(valor), DeclaredValue=_reais(valor)
                ),
                "Quotation": {
                    k: _pega(cotacao, k)
                    for k in (
                        "ShippingServiceCode",
                        "ShippingServiceName",
                        "Carrier",
                        "CarrierCode",
                        "ShippingPrice",
                        "PlatformShippingPrice",
                        "DeliveryTime",
                    )
                },
            }
        ]
        dados = await self._request(credentials, "POST", "/shipments", envio)
        itens_resposta = _pega(dados, "Items") or []
        primeiro = itens_resposta[0] if itens_resposta else {}
        shipment_id = _pega(primeiro, "ShipmentId")
        if not isinstance(shipment_id, int):
            raise ShippingProviderError(
                f"envio não criado: {json.dumps(dados, ensure_ascii=False)[:200]}",
                definitive=True,
                reason=_motivo(dados) or "A Frenet não criou o envio.",
            )
        return shipment_id

    async def _pay(self, credentials: ShippingCredentials, shipment_id: int) -> None:
        """Paga com o saldo e **confirma** pela situação do envio. Sem confirmação de pagamento,
        o envio criado é apagado: nada fica pendente na conta do lojista por nossa causa."""
        erro: ShippingProviderError | None = None
        try:
            resposta = await self._request(
                credentials, "POST", "/shipments/checkout", [shipment_id]
            )
        except ShippingProviderError as exc:
            erro, resposta = exc, None
        codigos = {c for c, _ in _codes(resposta)} | (
            {c for c, _ in _codes(erro.body)} if erro else set()
        )
        if INSUFFICIENT_BALANCE in codigos:
            await self._discard(credentials, shipment_id)
            raise InsufficientBalanceError()
        situacao = await self._status(credentials, shipment_id)
        if situacao in PAID:
            return
        if situacao is None:
            # Não deu para confirmar: o envio pode ter sido pago. Não apaga e não tenta de novo
            # sozinho — o id fica no log para conferir no painel da Frenet.
            logger.error("Frenet: pagamento sem confirmação", extra={"shipment_id": shipment_id})
            raise ShippingProviderError(
                f"pagamento sem confirmação (envio {shipment_id})",
                definitive=True,
                reason=f"Confira o envio {shipment_id} no painel da Frenet antes de repetir.",
            )
        await self._discard(credentials, shipment_id)
        if situacao == 2:
            raise InsufficientBalanceError()
        motivo = _motivo(resposta) or (erro.reason if erro else None)
        raise ShippingProviderError(
            f"pagamento não concluído (situação {situacao})",
            definitive=True,
            reason=motivo or "A Frenet não concluiu o pagamento da etiqueta.",
        )

    async def _status(self, credentials: ShippingCredentials, shipment_id: int) -> int | None:
        for espera in (0.0, 1.0, 2.0):
            if espera:
                await asyncio.sleep(espera)
            try:
                dados = await self._request(credentials, "GET", f"/shipments/{shipment_id}", None)
            except ShippingProviderError:
                continue
            situacao = _pega(dados, "ShipmentStatus")
            if isinstance(situacao, int):
                return situacao
        return None

    async def _discard(self, credentials: ShippingCredentials, shipment_id: int) -> None:
        try:
            await self._request(credentials, "DELETE", f"/shipments/{shipment_id}", None)
        except ShippingProviderError:
            logger.warning(
                "Frenet: envio não pago ficou sem apagar", extra={"shipment_id": shipment_id}
            )

    async def _label(
        self, credentials: ShippingCredentials, shipment_id: int
    ) -> tuple[str | None, str | None]:
        """Link da etiqueta e do rastreio. Nunca levanta: a etiqueta já está paga."""
        for espera in LABEL_WAITS:
            try:
                dados = await self._request(
                    credentials, "GET", f"/shipments/{shipment_id}/label", None
                )
            except ShippingProviderError:
                dados = None
            etiqueta = str(_pega(dados, "LabelUrl") or "") or None
            rastreio = str(_pega(dados, "TrackingUrl") or "") or None
            if etiqueta:
                return etiqueta, rastreio
            await asyncio.sleep(espera)
        logger.warning("Frenet: etiqueta paga ainda sem link", extra={"shipment_id": shipment_id})
        return None, None

    # ------------------------------------------------------------------ depois
    async def track(
        self, credentials: ShippingCredentials, provider_shipment_id: str
    ) -> TrackingResult:
        dados = await self._request(credentials, "GET", f"/shipments/{provider_shipment_id}", None)
        situacao = _pega(dados, "ShipmentStatus")
        status = _STATUS.get(situacao, "unknown") if isinstance(situacao, int) else "unknown"
        return TrackingResult(status=status, tracking_code="")

    async def cancel(self, credentials: ShippingCredentials, provider_shipment_id: str) -> bool:
        await self._request(credentials, "POST", f"/shipments/{provider_shipment_id}/cancel", None)
        return True

    async def test_credentials(self, credentials: ShippingCredentials) -> CredentialTest:
        try:
            dados = await self._request(credentials, "GET", "/wallet", None)
        except ShippingProviderError as exc:
            if exc.http_status in (401, 403):
                return CredentialTest(
                    ok=False, detail="a Frenet recusou o token da conta", refused=True
                )
            return CredentialTest(ok=False, detail=str(exc)[:200], refused=exc.definitive)
        saldo = _pega(dados, "Balance")
        limite = _pega(dados, "LabelLimit")
        partes = []
        if isinstance(saldo, int | float):
            partes.append(f"saldo R$ {saldo:.2f}".replace(".", ","))
        if isinstance(limite, int):
            partes.append(f"limite de {limite} etiquetas")
        return CredentialTest(ok=True, detail=", ".join(partes) or "ok")

    async def list_services(self, credentials: ShippingCredentials) -> tuple[ServiceInfo, ...]:
        dados = await self._request(
            credentials, "GET", "/carriers/services", None, base=settings.frenet_config_url
        )
        servicos: list[ServiceInfo] = []
        for s in _pega(dados, "ShippingServices") or []:
            codigo = str(_pega(s, "ServiceCode") or "")
            if not codigo:
                continue
            nome = str(_pega(s, "ServiceDescription") or codigo)
            servicos.append(
                ServiceInfo(
                    code=codigo,
                    name=nome,
                    carrier=str(_pega(s, "Carrier") or ""),
                    kind="express" if "sedex" in nome.lower() else "normal",
                    # Desligado na conta do lojista: cota, mas a etiqueta não sai.
                    available=bool(_pega(s, "Enabled")),
                    grouped_volumes=False,
                )
            )
        return tuple(servicos)

    # ------------------------------------------------------------------ HTTP
    async def _request(
        self,
        credentials: ShippingCredentials,
        method: str,
        path: str,
        body: Any,
        *,
        base: str | None = None,
    ) -> Any:
        token = credentials.secrets.get("access_token", "")
        parceiro = settings.frenet_partner_token.get_secret_value()
        if not token or not parceiro:
            raise ShippingProviderError("Frenet sem token", definitive=True)
        url = (base or settings.frenet_whitelabel_url).rstrip("/") + path
        headers = {
            "token": token,
            "x-partner-token": parceiro,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        timeout = httpx.Timeout(settings.shipping_timeout_seconds, connect=5.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.request(method, url, json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise ShippingProviderError(f"rede: {type(exc).__name__}") from exc
        dados: Any = None
        if response.content:
            try:
                dados = response.json()
            except ValueError:
                dados = {"message": response.text[:300]}
        if response.status_code >= 400:
            logger.warning(
                "Frenet recusou",
                extra={"path": path.split("/")[1], "status": response.status_code},
            )
            raise ShippingProviderError(
                json.dumps(dados, ensure_ascii=False)[:300] if dados else "recusado",
                http_status=response.status_code,
                definitive=400 <= response.status_code < 500 and response.status_code != 429,
                reason=_motivo(dados),
                body=dados,
            )
        return dados if dados is not None else {}
