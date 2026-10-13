"""Uber Direct com a conta da própria loja (docs/15-entrega-uber-direct.md).

O que está aqui foi medido em 13/10/2026 pela sonda `scripts/uber_direct_probe.py` (produção e
sandbox), não lido de segunda mão:

- token: `POST auth.uber.com/oauth/v2/token`, `client_credentials`, escopo `eats.deliveries`,
  vale 30 dias, e a Uber limita a 100 pedidos de token por hora — por isso o token fica guardado
  no cofre da loja e só é renovado perto do fim (ou quando a Uber o recusa);
- o Customer ID entra no caminho (`/v1/customers/{id}/...`), e um Customer ID que não é destas
  chaves só aparece quando se usa: a listagem de entregas é o jeito barato de conferir;
- o `live_mode` da resposta **não** diz se as chaves são de teste (veio `false` em produção):
  quem diz é a loja, ao colar as chaves. Em teste, toda criação leva o entregador simulado; com
  chave de produção a Uber recusa isso com 400 **sem criar nada**;
- a entrega criada a partir da cotação `dqt_X` tem o id `del_X`, e a mesma `idempotency_key` de
  novo dá 409 `duplicate_delivery` sem devolver a entrega: é pelo id derivado da cotação que se
  acha a corrida cuja resposta se perdeu.

Este módulo só fala com a Uber. Quem decide quando chamar é `app.courier.service`.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.integrations.credentials import CredentialStore
from app.models.base import utcnow

logger = get_logger(__name__)

#: Nome do provedor no cofre da loja.
PROVIDER = "uber_direct"
SECRET_KEYS = ("customer_id", "client_id", "client_secret")
#: O token guardado e quando ele vence (ISO). Não são chaves da loja: a tela não os mostra.
TOKEN_KEY = "access_token"  # noqa: S105 (nome da linha no cofre, não segredo)
TOKEN_EXPIRES_KEY = "access_token_expires_at"  # noqa: S105 (idem)
SCOPE = "eats.deliveries"
#: Renova antes do fim: um token que vence no meio de uma chamada vira erro para a loja.
TOKEN_MARGIN = timedelta(days=1)
#: Itens mandados no manifesto. A Uber só precisa de uma ideia do pacote.
MAX_MANIFEST_ITEMS = 20


@dataclass(frozen=True, slots=True)
class UberCredentials:
    customer_id: str
    client_id: str
    client_secret: str


@dataclass(frozen=True, slots=True)
class UberCheck:
    """Resultado de conferir as chaves. `refused` diz qual chave a Uber recusou; nulo = ela não
    recusou nada (deu certo, ou não respondeu)."""

    ok: bool
    refused: Literal["client", "customer"] | None
    detail: str


class CourierError(Exception):
    """A Uber disse não, ou não respondeu.

    `definitive`: repetir igual não adianta (endereço fora do raio, chave recusada). Sem isso, é
    rede ou a Uber fora do ar, e o worker tenta de novo.
    """

    def __init__(
        self, message: str, *, definitive: bool, code: str = "", status: int | None = None
    ) -> None:
        super().__init__(message)
        self.definitive = definitive
        self.code = code
        self.status = status


@dataclass(frozen=True, slots=True)
class Address:
    street: str
    number: str
    district: str
    city: str
    state: str
    postal_code: str
    complement: str | None = None

    def as_uber(self) -> str:
        """O endereço estruturado, em JSON dentro de texto, como a Uber pede (e a sonda mediu)."""
        cep = "".join(ch for ch in self.postal_code if ch.isdigit())
        segunda = " - ".join(p for p in (self.complement, self.district) if p)
        linhas = [f"{self.street}, {self.number}"] + ([segunda] if segunda else [])
        return json.dumps(
            {
                "street_address": linhas,
                "city": self.city,
                "state": self.state,
                "zip_code": f"{cep[:5]}-{cep[5:]}" if len(cep) == 8 else self.postal_code,
                "country": "BR",
            },
            ensure_ascii=False,
        )


@dataclass(frozen=True, slots=True)
class ManifestItem:
    name: str
    quantity: int
    price_cents: int


@dataclass(frozen=True, slots=True)
class DeliveryRequest:
    pickup_name: str
    pickup_address: Address
    pickup_phone: str
    pickup_notes: str | None
    dropoff_name: str
    dropoff_address: Address
    dropoff_phone: str
    dropoff_notes: str | None
    items: tuple[ManifestItem, ...]
    total_value_cents: int
    #: O que a loja e o entregador leem ("Pedido #12").
    reference: str
    #: A nossa linha (`courier_deliveries.id`): referência externa e chave de idempotência.
    external_id: str
    store_ref: str
    #: Entregador simulado. Só com as chaves de teste; com as de produção a Uber recusa.
    simulated: bool


@dataclass(frozen=True, slots=True)
class Quote:
    id: str
    fee_cents: int
    currency: str
    dropoff_eta: datetime | None


@dataclass(frozen=True, slots=True)
class DeliveryInfo:
    id: str
    status: str
    fee_cents: int | None
    tracking_url: str | None
    courier_name: str | None
    courier_vehicle: str | None
    pickup_eta: datetime | None
    dropoff_eta: datetime | None
    complete: bool
    external_id: str | None
    undeliverable_reason: str | None


def delivery_id_for_quote(quote_id: str) -> str | None:
    """`dqt_X` → `del_X` (medido no sandbox e na F0)."""
    return "del_" + quote_id[4:] if quote_id.startswith("dqt_") and len(quote_id) > 4 else None


# ------------------------------------------------------------------ cofre


async def load_credentials(session: AsyncSession, tenant_id: str) -> UberCredentials | None:
    """As chaves da loja, ou None se falta alguma. Só quem chama a Uber abre o cofre."""
    store = CredentialStore(session, tenant_id)
    valores: dict[str, str] = {}
    for chave in SECRET_KEYS:
        valor = await store.get(PROVIDER, chave)
        if not valor:
            return None
        valores[chave] = valor
    return UberCredentials(**valores)


async def save_credentials(
    session: AsyncSession, tenant_id: str, credentials: UberCredentials
) -> dict[str, str]:
    """Guarda as três chaves cifradas; devolve as formas mascaradas (para tela e auditoria).
    O token guardado era das chaves antigas: sai junto."""
    store = CredentialStore(session, tenant_id)
    mascaradas = {
        chave: await store.put(PROVIDER, chave, getattr(credentials, chave))
        for chave in SECRET_KEYS
    }
    await forget_token(session, tenant_id)
    return mascaradas


async def forget_credentials(session: AsyncSession, tenant_id: str) -> None:
    store = CredentialStore(session, tenant_id)
    for chave in SECRET_KEYS:
        await store.delete(PROVIDER, chave)
    await forget_token(session, tenant_id)


async def stored_token(session: AsyncSession, tenant_id: str) -> str | None:
    """O token guardado, se ainda vale com folga."""
    store = CredentialStore(session, tenant_id)
    vence = await store.get(PROVIDER, TOKEN_EXPIRES_KEY)
    if not vence:
        return None
    try:
        quando = datetime.fromisoformat(vence)
    except ValueError:
        return None
    if quando - TOKEN_MARGIN <= utcnow():
        return None
    return await store.get(PROVIDER, TOKEN_KEY)


async def save_token(session: AsyncSession, tenant_id: str, token: str, expires_in: int) -> None:
    store = CredentialStore(session, tenant_id)
    await store.put(PROVIDER, TOKEN_KEY, token)
    vence = utcnow() + timedelta(seconds=max(int(expires_in), 0))
    await store.put(PROVIDER, TOKEN_EXPIRES_KEY, vence.isoformat())


async def forget_token(session: AsyncSession, tenant_id: str) -> None:
    store = CredentialStore(session, tenant_id)
    for chave in (TOKEN_KEY, TOKEN_EXPIRES_KEY):
        await store.delete(PROVIDER, chave)


# ------------------------------------------------------------------ HTTP


def _timeout() -> httpx.Timeout:
    return httpx.Timeout(settings.uber_direct_timeout_seconds, connect=5.0)


def _base(customer_id: str) -> str:
    return f"{settings.uber_direct_api_url.rstrip('/')}/v1/customers/{customer_id}"


def _json(resposta: httpx.Response) -> dict[str, Any]:
    try:
        dados = resposta.json()
    except ValueError as exc:
        raise CourierError(
            "a Uber respondeu algo que não é JSON", definitive=False, status=resposta.status_code
        ) from exc
    return dados if isinstance(dados, dict) else {}


async def request_token(credentials: UberCredentials) -> tuple[str, int]:
    """Um token novo: (token, segundos até vencer). Recusa das chaves é definitiva."""
    try:
        async with httpx.AsyncClient(timeout=_timeout()) as client:
            resposta = await client.post(
                settings.uber_direct_auth_url,
                data={
                    "client_id": credentials.client_id,
                    "client_secret": credentials.client_secret,
                    "grant_type": "client_credentials",
                    "scope": SCOPE,
                },
            )
    except httpx.HTTPError as exc:
        raise CourierError("a Uber não respondeu ao token", definitive=False) from exc
    if resposta.status_code in (400, 401, 403):
        raise CourierError(
            "a Uber recusou as chaves da loja", definitive=True, status=resposta.status_code
        )
    if resposta.status_code != 200:
        raise CourierError(
            f"a Uber respondeu {resposta.status_code} ao token",
            definitive=False,
            status=resposta.status_code,
        )
    dados = _json(resposta)
    token = str(dados.get("access_token") or "")
    if not token:
        raise CourierError("a Uber não devolveu o token", definitive=False)
    return token, int(dados.get("expires_in") or 0)


#: Os recusos que a loja entende sem traduzir código.
_MOTIVOS = {
    "address_undeliverable": "o endereço do cliente está fora do alcance da Uber (cerca de 5 km)",
    "duplicate_delivery": "a Uber já tem uma corrida para este pedido",
    "invalid_params": "a Uber recusou os dados da corrida",
    "customer_not_found": "o Customer ID não é destas chaves",
    "delivery_not_found": "a Uber não achou a corrida",
    "noncancelable_delivery": "a corrida já não pode ser cancelada na Uber",
}


def _error(resposta: httpx.Response) -> CourierError:
    dados: dict[str, Any] = {}
    try:
        corpo = resposta.json()
        dados = corpo if isinstance(corpo, dict) else {}
    except ValueError:
        dados = {}
    code = str(dados.get("code") or "")[:60]
    mensagem = str(dados.get("message") or "")[:200]
    status = resposta.status_code
    # 401 só chega aqui depois de o token ter sido renovado (UberDirect._send): é a chave.
    definitive = status in (400, 401, 403, 404, 409, 422)
    if code in _MOTIVOS:
        texto = _MOTIVOS[code]
    elif definitive:
        texto = f"a Uber recusou ({code or status}){f': {mensagem}' if mensagem else ''}"
    else:
        texto = f"a Uber respondeu {status}"
    return CourierError(texto[:300], definitive=definitive, code=code, status=status)


def _instant(valor: Any) -> datetime | None:
    if not isinstance(valor, str) or not valor:
        return None
    try:
        quando = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        return None
    return None if quando.year < 2000 else quando  # o cancelamento volta 1970-01-01


def _delivery(dados: dict[str, Any]) -> DeliveryInfo:
    entregador = dados.get("courier")
    nome: str | None = None
    veiculo: str | None = None
    if isinstance(entregador, dict):
        nome = str(entregador.get("name") or "").strip()[:80] or None
        partes = [
            str(entregador.get(k) or "").strip()
            for k in ("vehicle_make", "vehicle_model", "vehicle_color")
        ]
        veiculo = " ".join(p for p in partes if p)[:80] or None
    fee = dados.get("fee")
    return DeliveryInfo(
        id=str(dados.get("id") or "")[:64],
        status=str(dados.get("status") or "")[:32],
        fee_cents=fee if isinstance(fee, int) else None,
        tracking_url=str(dados.get("tracking_url") or "")[:500] or None,
        courier_name=nome,
        courier_vehicle=veiculo,
        pickup_eta=_instant(dados.get("pickup_eta")),
        dropoff_eta=_instant(dados.get("dropoff_eta")),
        complete=bool(dados.get("complete")),
        external_id=str(dados.get("external_id") or "") or None,
        undeliverable_reason=str(dados.get("undeliverable_reason") or "")[:60] or None,
    )


class UberDirect:
    """Uma conta Uber Direct. O token vem de fora (o serviço guarda e renova): quando a Uber o
    recusa, `refresh` traz outro uma vez e a chamada é repetida."""

    def __init__(
        self,
        credentials: UberCredentials,
        token: str,
        refresh: Callable[[], Awaitable[str]],
    ) -> None:
        self.credentials = credentials
        self.token = token
        self._refresh = refresh

    async def _send(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> httpx.Response:
        url = _base(self.credentials.customer_id) + path
        resposta: httpx.Response | None = None
        for tentativa in (1, 2):
            try:
                async with httpx.AsyncClient(timeout=_timeout()) as client:
                    resposta = await client.request(
                        method,
                        url,
                        json=body,
                        headers={"Authorization": f"Bearer {self.token}"},
                    )
            except httpx.HTTPError as exc:
                raise CourierError(
                    f"a Uber não respondeu ({type(exc).__name__})", definitive=False
                ) from exc
            if resposta.status_code != 401 or tentativa == 2:
                break
            self.token = await self._refresh()
        assert resposta is not None
        return resposta

    async def quote(self, request: DeliveryRequest) -> Quote:
        resposta = await self._send(
            "POST",
            "/delivery_quotes",
            {
                "pickup_address": request.pickup_address.as_uber(),
                "dropoff_address": request.dropoff_address.as_uber(),
                "pickup_phone_number": request.pickup_phone,
                "dropoff_phone_number": request.dropoff_phone,
                "manifest_total_value": request.total_value_cents,
                "external_store_id": request.store_ref,
            },
        )
        if resposta.status_code != 200:
            raise _error(resposta)
        dados = _json(resposta)
        fee = dados.get("fee")
        if not dados.get("id") or not isinstance(fee, int):
            raise CourierError("a cotação da Uber veio sem preço", definitive=False)
        return Quote(
            id=str(dados["id"])[:64],
            fee_cents=fee,
            currency=str(dados.get("currency") or "brl"),
            dropoff_eta=_instant(dados.get("dropoff_eta")),
        )

    async def create(self, request: DeliveryRequest, quote_id: str) -> DeliveryInfo:
        corpo: dict[str, Any] = {
            "quote_id": quote_id,
            "pickup_name": request.pickup_name[:80],
            "pickup_business_name": request.pickup_name[:80],
            "pickup_address": request.pickup_address.as_uber(),
            "pickup_phone_number": request.pickup_phone,
            "dropoff_name": request.dropoff_name[:80],
            "dropoff_address": request.dropoff_address.as_uber(),
            "dropoff_phone_number": request.dropoff_phone,
            "manifest_items": [
                {
                    "name": item.name[:100],
                    "quantity": max(item.quantity, 1),
                    "size": "small",
                    "price": max(item.price_cents, 0),
                }
                for item in request.items[:MAX_MANIFEST_ITEMS]
            ],
            "manifest_reference": request.reference[:60],
            "manifest_total_value": request.total_value_cents,
            "external_id": request.external_id,
            "external_store_id": request.store_ref,
            "idempotency_key": request.external_id,
        }
        if request.pickup_notes:
            corpo["pickup_notes"] = request.pickup_notes[:280]
        if request.dropoff_notes:
            corpo["dropoff_notes"] = request.dropoff_notes[:280]
        if request.simulated:
            corpo["test_specifications"] = {"robo_courier_specification": {"mode": "auto"}}
        resposta = await self._send("POST", "/deliveries", corpo)
        if resposta.status_code not in (200, 201):
            raise _error(resposta)
        return _delivery(_json(resposta))

    async def get(self, delivery_id: str) -> DeliveryInfo | None:
        """A corrida, ou None se a Uber não a conhece."""
        resposta = await self._send("GET", f"/deliveries/{delivery_id}")
        if resposta.status_code == 404:
            return None
        if resposta.status_code != 200:
            raise _error(resposta)
        return _delivery(_json(resposta))

    async def cancel_delivery(self, delivery_id: str) -> DeliveryInfo:
        resposta = await self._send("POST", f"/deliveries/{delivery_id}/cancel", {})
        if resposta.status_code != 200:
            raise _error(resposta)
        return _delivery(_json(resposta))


async def check_credentials(credentials: UberCredentials) -> UberCheck:
    """Confere as chaves de verdade: pede um token e lista uma entrega com o Customer ID.

    Duas perguntas porque são dois erros diferentes: Client ID/Secret errados a Uber recusa no
    token; Customer ID de outra conta só aparece quando o token tenta usá-lo.
    """
    try:
        async with httpx.AsyncClient(timeout=_timeout()) as client:
            token = await client.post(
                settings.uber_direct_auth_url,
                data={
                    "client_id": credentials.client_id,
                    "client_secret": credentials.client_secret,
                    "grant_type": "client_credentials",
                    "scope": SCOPE,
                },
            )
            if token.status_code in (400, 401, 403):
                return UberCheck(False, "client", "a Uber recusou o Client ID ou o Client Secret")
            if token.status_code != 200:
                return UberCheck(False, None, f"a Uber respondeu {token.status_code} ao token")
            acesso = str((token.json() or {}).get("access_token") or "")
            if not acesso:
                return UberCheck(False, None, "a Uber não devolveu o token")
            lista = await client.get(
                f"{_base(credentials.customer_id)}/deliveries",
                params={"limit": 1},
                headers={"Authorization": f"Bearer {acesso}"},
            )
    except httpx.HTTPError as exc:
        logger.warning("uber: sem resposta ao conferir chaves", extra={"error": type(exc).__name__})
        return UberCheck(False, None, "a Uber não respondeu agora")
    except ValueError:
        return UberCheck(False, None, "a Uber respondeu algo que não é JSON")
    if lista.status_code == 200:
        return UberCheck(True, None, "chaves conferidas")
    if lista.status_code in (401, 403, 404):
        return UberCheck(False, "customer", "o Customer ID não é destas chaves")
    return UberCheck(False, None, f"a Uber respondeu {lista.status_code} à conferência")
