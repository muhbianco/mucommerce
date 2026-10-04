"""O contrato que toda transportadora implementa (ADR 0015).

O provedor traduz entre a nossa remessa e a dele; nunca toca no banco. O domínio só conhece
`ShippingOption` (o que dá para oferecer) e `ShipmentResult` (o que saiu de fato).

A diferença de temperatura entre os dois lados é de propósito: **cotar é leitura** e pode
falhar sem drama — o checkout avisa que o frete está indisponível e oferece retirada.
**Despachar é dinheiro** — compra etiqueta e debita a carteira da loja —, então falha é
barulhenta e a chamada carrega uma referência própria para o provedor deduplicar.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol

# Ciclo de vida normalizado: cada provedor fala o seu dialeto, o pedido entende só estes.
TrackingStatus = Literal[
    "created",
    "posted",
    "in_transit",
    "out_for_delivery",
    "delivered",
    "returned",
    "cancelled",
    "unknown",
]


class ShippingProviderError(Exception):
    """Chamada que falhou.

    `definitive` = o provedor recusou o pedido (endereço inválido, volume fora do limite):
    repetir igual não ajuda. O resto (rede, timeout, 5xx) deixa o resultado desconhecido — em
    cotação isso vira "frete indisponível", em despacho vira erro para o operador tentar de novo.
    """

    def __init__(
        self,
        message: str,
        *,
        http_status: int | None = None,
        code: str | None = None,
        definitive: bool = False,
    ) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.code = code
        self.definitive = definitive


class InsufficientBalanceError(ShippingProviderError):
    """Carteira do agregador sem saldo para comprar a etiqueta.

    É operação da loja, não defeito nosso: o pedido não muda de estado e o recado diz o que
    fazer em vez de "erro interno".
    """

    def __init__(self, message: str = "Saldo insuficiente na carteira do frete.") -> None:
        super().__init__(message, code="insufficient_balance", definitive=True)


@dataclass(frozen=True, slots=True)
class ShippingCredentials:
    """Decifradas na borda do serviço; nunca vão para log nem para a API."""

    secrets: Mapping[str, str]
    public_config: Mapping[str, Any]
    sandbox: bool


@dataclass(frozen=True, slots=True)
class ShippingCapabilities:
    #: Códigos de serviço que este provedor sabe oferecer ("correios_pac", "jadlog_package"…).
    services: tuple[str, ...]
    required_secrets: tuple[str, ...] = ()
    required_public: tuple[str, ...] = ()
    supports_label: bool = True
    supports_tracking: bool = True
    supports_cancel: bool = True


@dataclass(frozen=True, slots=True)
class Parcel:
    """Um volume pronto para cotar: medidas e valor declarado.

    Milímetros e gramas aqui dentro (é como o catálogo guarda); cada provedor converte para a
    unidade dele. Valor declarado é o do conteúdo, para seguro.
    """

    weight_grams: int
    width_mm: int
    height_mm: int
    depth_mm: int
    value_cents: int = 0

    @property
    def weight_kg(self) -> float:
        return round(self.weight_grams / 1000, 3)


@dataclass(frozen=True, slots=True)
class ShippingParty:
    """Quem envia e quem recebe. A etiqueta precisa de mais do que a cotação."""

    name: str
    postal_code: str
    street: str
    number: str
    district: str
    city: str
    state: str
    complement: str | None = None
    document: str | None = None
    email: str | None = None
    phone: str | None = None


@dataclass(frozen=True, slots=True)
class QuoteRequest:
    origin_postal_code: str
    destination_postal_code: str
    parcels: tuple[Parcel, ...]
    #: Allowlist da loja; vazio = o que o provedor oferecer.
    services: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ShippingOption:
    """Um serviço cotado. `error` preenchido = veio na resposta mas não dá para usar."""

    service_code: str
    service_name: str
    carrier: str
    price_cents: int
    delivery_days: int | None = None
    error: str | None = None
    #: Faixa de prazo em dias úteis (o Melhor Envio devolve `delivery_range`); `delivery_days`
    #: é o máximo dela.
    delivery_min: int | None = None
    delivery_max: int | None = None
    #: Preço de cada volume, quando a transportadora cobra por volume (Correios). Vazio quando
    #: ela cobra a remessa inteira (Jadlog).
    parcel_prices_cents: tuple[int, ...] = ()
    #: Quantos volumes cabem numa etiqueta só na compra. 1 = uma etiqueta por volume (Correios,
    #: J&T, Loggi, Total — docs de compra do Melhor Envio). Desconhecido conta como 1: é sempre
    #: válido e nunca promete uma compra que a transportadora recusaria.
    multi_volume_max: int = 1

    @property
    def usable(self) -> bool:
        return self.error is None and self.price_cents > 0


@dataclass(frozen=True, slots=True)
class DeclaredItem:
    """Um bem da declaração de conteúdo (DC-e): o que a etiqueta diz que vai no pacote."""

    name: str
    quantity: int
    unit_value_cents: int


@dataclass(frozen=True, slots=True)
class ShipmentRequest:
    #: Nossa referência (id da remessa). Vai para o provedor como chave de deduplicação.
    reference: str
    service_code: str
    sender: ShippingParty
    recipient: ShippingParty
    parcels: tuple[Parcel, ...]
    order_number: int | None = None
    insurance_cents: int = 0
    notes: str | None = None
    #: Os bens desta etiqueta, item a item: viram a lista da DC-e (fato 8). Com uma etiqueta
    #: por volume, só os daquele volume.
    items: tuple[DeclaredItem, ...] = ()


@dataclass(frozen=True, slots=True)
class ShipmentResult:
    provider_shipment_id: str
    tracking_code: str
    carrier: str
    service_code: str
    cost_cents: int
    label_url: str | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TrackingEvent:
    at: datetime
    status: TrackingStatus
    description: str
    location: str | None = None


@dataclass(frozen=True, slots=True)
class TrackingResult:
    status: TrackingStatus
    tracking_code: str
    events: tuple[TrackingEvent, ...] = ()
    delivered_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class CredentialTest:
    ok: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ServiceInfo:
    """Um serviço que a conta da loja pode oferecer, como a transportadora o descreve."""

    code: str
    name: str
    carrier: str
    #: Como a transportadora classifica: "normal", "express" ou "economic".
    kind: str
    available: bool
    #: Junta vários volumes numa etiqueta (Jadlog); senão, uma etiqueta por volume (Correios).
    grouped_volumes: bool = False
    #: A compra da etiqueta exige nota fiscal (Jadlog, no Melhor Envio).
    requires_invoice: bool = False
    #: Teto do valor declarado (seguro); acima disso o volume vai segurado só até aqui.
    max_insurance_cents: int | None = None
    #: Peso máximo por volume, em gramas.
    max_weight_grams: int | None = None


class ShippingProvider(Protocol):
    name: str

    @property
    def capabilities(self) -> ShippingCapabilities: ...

    async def quote(
        self, credentials: ShippingCredentials, request: QuoteRequest
    ) -> tuple[ShippingOption, ...]:
        """Preços e prazos por serviço. Serviço indisponível volta com `error`, não somindo."""
        ...

    async def ship(
        self, credentials: ShippingCredentials, request: ShipmentRequest
    ) -> ShipmentResult:
        """Compra o frete e gera a etiqueta. Idempotente por `request.reference`."""
        ...

    async def track(
        self, credentials: ShippingCredentials, provider_shipment_id: str
    ) -> TrackingResult: ...

    async def cancel(self, credentials: ShippingCredentials, provider_shipment_id: str) -> bool: ...

    async def test_credentials(self, credentials: ShippingCredentials) -> CredentialTest: ...

    async def list_services(self, credentials: ShippingCredentials) -> tuple[ServiceInfo, ...]:
        """Os serviços que a conta da loja pode oferecer (tela "Serviços oferecidos")."""
        ...
