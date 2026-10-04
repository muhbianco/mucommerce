"""Transportadora de mentira, para teste e E2E (nunca em produção: o ambiente não a permite).

Coteja preço por peso e distância fingida (diferença entre os CEPs), para os testes poderem
afirmar coisas sem depender de rede. Despacho guarda a remessa em memória e o rastreio avança
por comando (`advance`), do jeito que o `FakeProvider` de pagamentos faz com `settle`.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.ids import new_id
from app.shipping.provider import (
    CredentialTest,
    InsufficientBalanceError,
    QuoteRequest,
    ServiceInfo,
    ShipmentRequest,
    ShipmentResult,
    ShippingCapabilities,
    ShippingCredentials,
    ShippingOption,
    ShippingProviderError,
    TrackingEvent,
    TrackingResult,
    TrackingStatus,
)

SERVICES = ("fake_economico", "fake_expresso")
_SHIPMENTS: dict[str, ShipmentResult] = {}
_BY_REFERENCE: dict[str, str] = {}
_STATUS: dict[str, TrackingStatus] = {}
#: Cotações recebidas, na ordem (os testes contam chamadas: deduplicação e cache).
CALLS: list[QuoteRequest] = []
#: Referências de compra recebidas, na ordem (os testes conferem que nada é comprado duas vezes).
SHIP_CALLS: list[str] = []
#: Os pedidos de etiqueta inteiros (os testes conferem a declaração de conteúdo de cada um).
SHIP_REQUESTS: list[ShipmentRequest] = []
#: Finais de referência cuja compra falha (".2" = o 2º volume): simula a transportadora
#: recusando um volume no meio da compra por volume.
FAIL_SUFFIXES: set[str] = set()
#: Interruptores de teste: CEP de destino que força um comportamento.
CEP_SEM_SALDO = "99999999"
CEP_FORA_DE_AREA = "88888888"
CEP_INSTAVEL = "77777777"


def reset() -> None:
    CALLS.clear()
    SHIP_CALLS.clear()
    SHIP_REQUESTS.clear()
    FAIL_SUFFIXES.clear()
    _SHIPMENTS.clear()
    _BY_REFERENCE.clear()
    _STATUS.clear()


def advance(provider_shipment_id: str, status: TrackingStatus) -> None:
    """Move a remessa no rastreio, como o carteiro faria."""
    _STATUS[provider_shipment_id] = status


class FakeShippingProvider:
    name = "fake"

    @property
    def capabilities(self) -> ShippingCapabilities:
        return ShippingCapabilities(services=SERVICES, required_secrets=("token",))

    async def quote(
        self, credentials: ShippingCredentials, request: QuoteRequest
    ) -> tuple[ShippingOption, ...]:
        CALLS.append(request)
        if request.destination_postal_code == CEP_INSTAVEL:
            raise ShippingProviderError("provedor instável", http_status=503)
        distancia = abs(
            int(request.destination_postal_code[:5]) - int(request.origin_postal_code[:5])
        )
        # Econômico cobra por volume e não junta volumes numa etiqueta (como os Correios);
        # Expresso cobra a remessa e aceita até 5 volumes por etiqueta (como a Jadlog). Com um
        # volume só, os preços são os de sempre.
        por_volume = tuple(1500 + p.weight_grams * 2 + distancia // 10 for p in request.parcels)
        peso = sum(p.weight_grams for p in request.parcels)
        remessa = 1500 + peso * 2 + distancia // 10
        fora = request.destination_postal_code == CEP_FORA_DE_AREA
        opcoes: tuple[ShippingOption, ...] = (
            ShippingOption(
                service_code="fake_economico",
                service_name="Fake Econômico",
                carrier="Fake",
                price_cents=sum(por_volume),
                delivery_days=8,
                error="fora da área de entrega" if fora else None,
                delivery_min=6,
                delivery_max=8,
                parcel_prices_cents=por_volume,
                multi_volume_max=1,
            ),
            ShippingOption(
                service_code="fake_expresso",
                service_name="Fake Expresso",
                carrier="Fake",
                price_cents=remessa * 2,
                delivery_days=2,
                error="fora da área de entrega" if fora else None,
                delivery_min=1,
                delivery_max=2,
                multi_volume_max=5,
            ),
        )
        if request.services:
            opcoes = tuple(o for o in opcoes if o.service_code in request.services)
        return opcoes

    async def ship(
        self, credentials: ShippingCredentials, request: ShipmentRequest
    ) -> ShipmentResult:
        SHIP_CALLS.append(request.reference)
        SHIP_REQUESTS.append(request)
        if any(request.reference.endswith(s) for s in FAIL_SUFFIXES):
            raise ShippingProviderError("volume recusado no teste", http_status=422)
        existente = _BY_REFERENCE.get(request.reference)
        if existente is not None:
            return _SHIPMENTS[existente]  # idempotência: a mesma remessa, não outra etiqueta
        if request.recipient.postal_code == CEP_SEM_SALDO:
            raise InsufficientBalanceError()
        shipment_id = new_id()
        peso = sum(p.weight_grams for p in request.parcels)
        resultado = ShipmentResult(
            provider_shipment_id=shipment_id,
            tracking_code=f"FK{shipment_id[:10].upper()}BR",
            carrier="Fake",
            service_code=request.service_code,
            cost_cents=1500 + peso * 2,
            label_url=f"https://fake.local/etiquetas/{shipment_id}.pdf",
        )
        _SHIPMENTS[shipment_id] = resultado
        _BY_REFERENCE[request.reference] = shipment_id
        _STATUS[shipment_id] = "posted"
        return resultado

    async def track(
        self, credentials: ShippingCredentials, provider_shipment_id: str
    ) -> TrackingResult:
        resultado = _SHIPMENTS.get(provider_shipment_id)
        if resultado is None:
            raise ShippingProviderError("remessa desconhecida", definitive=True)
        status = _STATUS.get(provider_shipment_id, "unknown")
        agora = datetime.now(UTC)
        return TrackingResult(
            status=status,
            tracking_code=resultado.tracking_code,
            events=(TrackingEvent(at=agora, status=status, description=status),),
            delivered_at=agora if status == "delivered" else None,
        )

    async def cancel(self, credentials: ShippingCredentials, provider_shipment_id: str) -> bool:
        if provider_shipment_id not in _SHIPMENTS:
            return False
        _STATUS[provider_shipment_id] = "cancelled"
        return True

    async def test_credentials(self, credentials: ShippingCredentials) -> CredentialTest:
        return CredentialTest(ok=bool(credentials.secrets.get("token")), detail="fake")

    async def list_services(self, credentials: ShippingCredentials) -> tuple[ServiceInfo, ...]:
        # Os mesmos dois da cotação: o Econômico como os Correios (uma etiqueta por volume), o
        # Expresso como a Jadlog (junta volumes, exige nota fiscal).
        return (
            ServiceInfo(
                code="fake_economico",
                name="Fake Econômico",
                carrier="Fake",
                kind="normal",
                available=True,
                max_insurance_cents=300_000,
                max_weight_grams=30_000,
            ),
            ServiceInfo(
                code="fake_expresso",
                name="Fake Expresso",
                carrier="Fake",
                kind="express",
                available=True,
                grouped_volumes=True,
                requires_invoice=True,
                max_insurance_cents=2_990_000,
                max_weight_grams=120_000,
            ),
        )
