"""Melhor Envio no formato que o sandbox confirmou (portão F2.5, docs/13-frete-v2.md).

A resposta usada aqui é a da sonda de 04/10/2026 (2 volumes de 30 x 20 x 15 cm, 1 kg, seguros
de 50 e 150), sem nada que identifique a conta.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx

from app.shipping.provider import (
    Parcel,
    QuoteRequest,
    ShipmentRequest,
    ShippingCredentials,
    ShippingParty,
    ShippingProviderError,
)
from app.shipping.providers.melhorenvio import SANDBOX_URL, MelhorEnvioProvider, parse_options

CREDENCIAIS = ShippingCredentials(
    secrets={"access_token": "token-de-teste"}, public_config={}, sandbox=True
)

#: Resposta do sandbox para 2 volumes com `insurance` 50 e 150 (teste "1-seguro-insurance").
RESPOSTA_SANDBOX: list[dict[str, Any]] = [
    {
        "id": 1,
        "name": "PAC",
        "price": "54.04",
        "custom_price": "54.04",
        "discount": "0.00",
        "currency": "R$",
        "delivery_time": 6,
        "delivery_range": {"min": 5, "max": 6},
        "custom_delivery_time": 6,
        "custom_delivery_range": {"min": 5, "max": 6},
        "packages": [
            {
                "price": "26.52",
                "format": "box",
                "dimensions": {"height": 15, "width": 20, "length": 30},
                "weight": "1.00",
                "insurance_value": "50.00",
            },
            {
                "price": "27.52",
                "format": "box",
                "dimensions": {"height": 15, "width": 20, "length": 30},
                "weight": "1.00",
                "insurance_value": "150.00",
            },
        ],
        "company": {"id": 1, "name": "Correios"},
    },
    {
        "id": 3,
        "name": ".Package",
        "price": "30.70",
        "custom_price": "30.70",
        "delivery_time": 4,
        "delivery_range": {"min": 3, "max": 4},
        "custom_delivery_time": 4,
        "custom_delivery_range": {"min": 3, "max": 4},
        "packages": [
            {
                "format": "box",
                "dimensions": {"height": 15, "width": 20, "length": 30},
                "weight": "1.00",
                "insurance_value": "50.00",
            },
            {
                "format": "box",
                "dimensions": {"height": 15, "width": 20, "length": 30},
                "weight": "1.00",
                "insurance_value": "150.00",
            },
        ],
        "company": {"id": 2, "name": "Jadlog"},
    },
    {
        "id": 17,
        "name": "Mini Envios",
        "error": "Dimensões do objeto ultrapassam o limite da transportadora.",
        "company": {"id": 1, "name": "Correios"},
    },
]


def test_parse_da_resposta_real_do_sandbox() -> None:
    pac, package, mini = parse_options(RESPOSTA_SANDBOX)
    assert (pac.price_cents, pac.delivery_days, pac.delivery_min, pac.delivery_max) == (
        5404,
        6,
        5,
        6,
    )
    assert pac.parcel_prices_cents == (2652, 2752), "Correios cobram por volume"
    assert pac.multi_volume_max == 1, "Correios: uma etiqueta por volume"
    assert package.parcel_prices_cents == (), "Jadlog cobra a remessa"
    assert package.multi_volume_max == 5
    assert mini.error and not mini.usable


@respx.mock
async def test_cotacao_manda_seguro_por_volume_e_centimetros_inteiros_para_cima() -> None:
    rota = respx.post(f"{SANDBOX_URL}/api/v2/me/shipment/calculate").mock(
        return_value=httpx.Response(200, json=RESPOSTA_SANDBOX)
    )
    pedido = QuoteRequest(
        origin_postal_code="01001000",
        destination_postal_code="20040020",
        parcels=(
            Parcel(weight_grams=1000, width_mm=200, height_mm=150, depth_mm=300, value_cents=5000),
            # 15,8 cm não pode virar 15: a API arredonda, e para baixo seria caixa menor que a real.
            Parcel(weight_grams=680, width_mm=158, height_mm=108, depth_mm=208, value_cents=15000),
        ),
        services=("1", "3"),
    )
    opcoes = await MelhorEnvioProvider().quote(CREDENCIAIS, pedido)
    assert [o.service_code for o in opcoes] == ["1", "3", "17"]
    corpo = json.loads(rota.calls.last.request.content)
    assert corpo["volumes"] == [
        {"height": 15, "width": 20, "length": 30, "weight": 1.0, "insurance": 50.0},
        {"height": 11, "width": 16, "length": 21, "weight": 0.68, "insurance": 150.0},
    ]
    assert "insurance_value" not in corpo["options"], "em options o seguro ia só para o 1º volume"
    assert corpo["services"] == "1,3"


@pytest.mark.parametrize(
    ("seguro_cents", "esperado"),
    [(6000, 60.0), (50, 1.0), (0, 1.0)],
    ids=["valor-do-volume", "abaixo-do-minimo", "sem-valor-declarado"],
)
@respx.mock
async def test_carrinho_manda_o_seguro_em_options_com_minimo_de_um_real(
    seguro_cents: int, esperado: float
) -> None:
    """Sandbox, 04/10/2026: no `/cart` o `volumes[].insurance` é ignorado, vale o
    `options.insurance_value`, e abaixo de R$ 1,00 a Jadlog recusa a inserção."""
    rota = respx.post(f"{SANDBOX_URL}/api/v2/me/cart").mock(
        return_value=httpx.Response(422, json={"message": "recusado no teste"})
    )
    parte = ShippingParty(
        name="Loja",
        postal_code="01001000",
        street="Praça da Sé",
        number="1",
        district="Sé",
        city="São Paulo",
        state="SP",
        document="46867029000176",
    )
    pedido = ShipmentRequest(
        reference="remessa-1",
        service_code="1",
        sender=parte,
        recipient=parte,
        parcels=(
            Parcel(
                weight_grams=680,
                width_mm=158,
                height_mm=108,
                depth_mm=208,
                value_cents=seguro_cents,
            ),
        ),
        insurance_cents=seguro_cents,
    )
    with pytest.raises(ShippingProviderError):  # o carrinho recusa; interessa o corpo enviado
        await MelhorEnvioProvider().ship(CREDENCIAIS, pedido)
    corpo = json.loads(rota.calls.last.request.content)
    assert corpo["volumes"] == [{"height": 11, "width": 16, "length": 21, "weight": 0.68}]
    assert corpo["options"]["insurance_value"] == esperado
