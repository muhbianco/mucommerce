"""Envio por transportadora (etapa J, ADR 0015): empacotamento, assinatura da cotação e a
avaliação do modo `shipping` no fulfillment.

Tudo aqui é puro — sem rede e sem banco. O que fala com o provedor tem teste próprio.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.fulfillment.service import FulfillmentChoice, evaluate, offered_modes
from app.shipping import signing
from app.shipping.packing import (
    Box,
    MissingDimensions,
    PackItem,
    item_from_variant,
    pack,
)
from app.shipping.selection import ShippingSelection
from app.tenancy.context import TenantContext
from app.tenancy.settings_normalizers import normalize_fulfillment
from app.tenancy.settings_schemas import FulfillmentV2

AGORA = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
CAIXA = Box(width_mm=300, height_mm=200, depth_mm=200, max_weight_grams=10_000)
ORIGEM = {
    "name": "Loja",
    "postal_code": "01001000",
    "address": "Praça da Sé",
    "number": "1",
    "district": "Sé",
    "city": "São Paulo",
    "state": "SP",
}


# ------------------------------------------------------------------------- empacotamento


def item(peso: int = 500, lado: int = 100, valor: int = 1000) -> PackItem:
    return PackItem(
        weight_grams=peso, width_mm=lado, height_mm=lado, depth_mm=lado, value_cents=valor
    )


def test_tudo_que_cabe_vai_numa_caixa_so() -> None:
    volumes = pack([item(), item(), item()], CAIXA)
    assert len(volumes) == 1
    assert volumes[0].weight_grams == 1500
    assert (volumes[0].width_mm, volumes[0].height_mm) == (300, 200)


def test_peso_estoura_e_abre_caixa_nova() -> None:
    volumes = pack([item(peso=6000), item(peso=6000)], CAIXA)
    assert len(volumes) == 2
    assert all(v.weight_grams == 6000 for v in volumes)


def test_tara_da_caixa_entra_no_peso() -> None:
    caixa = Box(
        width_mm=300, height_mm=200, depth_mm=200, max_weight_grams=10_000, empty_weight_grams=200
    )
    assert pack([item(peso=1000)], caixa)[0].weight_grams == 1200


def test_peca_grande_viaja_sozinha_com_as_medidas_dela() -> None:
    gigante = PackItem(weight_grams=4000, width_mm=900, height_mm=400, depth_mm=300)
    volumes = pack([gigante, item()], CAIXA)
    assert len(volumes) == 2
    proprio = next(v for v in volumes if v.width_mm == 900)
    assert (proprio.height_mm, proprio.depth_mm, proprio.weight_grams) == (400, 300, 4000)


def test_peca_cabe_girada() -> None:
    """Caixa 300x200x200 engole uma peça 200x300x150: o que importa são as medidas ordenadas."""
    deitada = PackItem(weight_grams=100, width_mm=200, height_mm=300, depth_mm=150)
    assert pack([deitada], CAIXA)[0].width_mm == 300


def test_carrinho_vazio_nao_gera_volume() -> None:
    assert pack([], CAIXA) == ()


def test_variante_sem_medida_recusa_em_vez_de_chutar() -> None:
    with pytest.raises(MissingDimensions):
        item_from_variant(
            weight_grams=500, width_mm=None, height_mm=80, depth_mm=60, value_cents=100, quantity=1
        )


def test_valor_declarado_nao_perde_centavo_na_divisao() -> None:
    itens = item_from_variant(
        weight_grams=100, width_mm=50, height_mm=50, depth_mm=50, value_cents=5000, quantity=3
    )
    assert sum(i.value_cents for i in itens) == 5000
    assert pack(itens, CAIXA)[0].value_cents == 5000


# ---------------------------------------------------------------------------- assinatura


def test_assinatura_do_carrinho_ignora_a_ordem() -> None:
    a = signing.cart_signature(
        tenant_id="t", destination_postal_code="20000-000", lines=[("v1", 2), ("v2", 1)]
    )
    b = signing.cart_signature(
        tenant_id="t", destination_postal_code="20000000", lines=[("v2", 1), ("v1", 2)]
    )
    assert a == b


def test_assinatura_muda_com_item_e_com_destino() -> None:
    base = signing.cart_signature(
        tenant_id="t", destination_postal_code="20000000", lines=[("v1", 1)]
    )
    assert base != signing.cart_signature(
        tenant_id="t", destination_postal_code="20000000", lines=[("v1", 2)]
    )
    assert base != signing.cart_signature(
        tenant_id="t", destination_postal_code="30000000", lines=[("v1", 1)]
    )
    assert base != signing.cart_signature(
        tenant_id="outra", destination_postal_code="20000000", lines=[("v1", 1)]
    )


def test_preco_adulterado_nao_confere() -> None:
    dados: dict[str, Any] = {
        "tenant_id": "t",
        "cart": "c",
        "provider": "fake",
        "service_code": "1",
        "quoted_at": AGORA,
    }
    assinatura = signing.sign(price_cents=2500, **dados)
    assert signing.verify(signature=assinatura, price_cents=2500, **dados) is True
    assert signing.verify(signature=assinatura, price_cents=500, **dados) is False


def test_cotacao_vence() -> None:
    assert signing.expired(AGORA, AGORA + timedelta(minutes=5), ttl_minutes=30) is False
    assert signing.expired(AGORA, AGORA + timedelta(minutes=31), ttl_minutes=30) is True


# ------------------------------------------------------------------------------ evaluate


@dataclass
class Address:
    postal_code: str = "20000000"
    city: str = "Rio de Janeiro"
    state: str = "RJ"
    district: str = "Centro"


def store(flags: dict[str, bool], fulfillment: dict[str, Any]) -> TenantContext:
    value = FulfillmentV2.model_validate(normalize_fulfillment(None, fulfillment)).model_dump()
    return TenantContext(
        id="t",
        slug="t",
        name="T",
        public_key="k",
        status="active",
        timezone="America/Sao_Paulo",
        locale="pt-BR",
        currency="BRL",
        features=flags,
        settings={"fulfillment": value},
    )


def loja(**shipping: Any) -> TenantContext:
    config = {"enabled": True, "provider": "fake", "origin": ORIGEM} | shipping
    return store({"shipping.fake": True}, {"shipping": config})


def escolha(
    *,
    tenant_id: str = "t",
    cart: str = "carrinho",
    price_cents: int = 2500,
    quoted_at: datetime = AGORA,
    signature: str | None = None,
) -> ShippingSelection:
    return ShippingSelection(
        provider="fake",
        service_code="1",
        service_name="Fake Econômico",
        carrier="Fake",
        price_cents=price_cents,
        quoted_at=quoted_at,
        cart=cart,
        delivery_days=8,
        signature=signature
        if signature is not None
        else signing.sign(
            tenant_id=tenant_id,
            cart=cart,
            provider="fake",
            service_code="1",
            price_cents=price_cents,
            quoted_at=quoted_at,
        ),
    )


def cotar(tenant: TenantContext, selection: ShippingSelection | None, **kwargs: Any):
    return evaluate(
        tenant,
        FulfillmentChoice(type="shipping", shipping=selection),
        subtotal_cents=kwargs.pop("subtotal_cents", 10_000),
        address=kwargs.pop("address", Address()),
        now=kwargs.pop("now", AGORA),
        cart_signature=kwargs.pop("cart_signature", "carrinho"),
    )


def test_modo_precisa_de_configuracao_e_origem() -> None:
    assert "shipping" in offered_modes(loja())
    # Sem endereço de origem não há de onde cotar.
    assert "shipping" not in offered_modes(
        store({}, {"shipping": {"enabled": True, "provider": "fake"}})
    )
    # Desligado no painel não aparece, mesmo com tudo configurado.
    assert "shipping" not in offered_modes(
        store({}, {"shipping": {"enabled": False, "provider": "fake", "origin": ORIGEM}})
    )


def test_transportadora_de_verdade_ainda_depende_da_flag() -> None:
    """O `fake` é exceção (só existe onde o ambiente permite); o resto precisa da flag."""
    config = {"enabled": True, "provider": "melhorenvio", "origin": ORIGEM}
    assert "shipping" not in offered_modes(store({}, {"shipping": config}))
    assert "shipping" in offered_modes(store({"shipping.melhorenvio": True}, {"shipping": config}))


def test_cotacao_valida_vira_frete_do_pedido() -> None:
    quote = cotar(loja(), escolha())
    assert quote.problems == ()
    assert quote.fee_cents == 2500
    assert quote.snapshot["carrier"] == "Fake"
    assert quote.snapshot["service_name"] == "Fake Econômico"
    assert quote.snapshot["fee_cents"] == 2500


def test_sem_endereco_e_sem_cotacao_reclama_o_que_falta() -> None:
    assert cotar(loja(), escolha(), address=None).problems == ("address_required",)
    assert cotar(loja(), None).problems == ("quote_required",)


def test_cotacao_vencida_ou_de_outro_carrinho_obriga_recotar() -> None:
    assert cotar(loja(), escolha(), now=AGORA + timedelta(hours=2)).problems == ("quote_expired",)
    assert cotar(loja(), escolha(cart="outro"), cart_signature="carrinho").problems == (
        "quote_expired",
    )


def test_preco_mexido_no_navegador_e_recusado() -> None:
    adulterada = ShippingSelection(
        provider="fake",
        service_code="1",
        service_name="Fake Econômico",
        carrier="Fake",
        price_cents=1,  # o cliente editou
        quoted_at=AGORA,
        cart="carrinho",
        signature=escolha().signature,
    )
    assert cotar(loja(), adulterada).problems == ("quote_invalid",)


def test_frete_gratis_zera_para_o_cliente() -> None:
    quote = cotar(loja(free_above_cents=9_000), escolha(), subtotal_cents=10_000)
    assert quote.fee_cents == 0
    # O preço cotado continua no snapshot: a loja paga a etiqueta e precisa ver quanto foi.
    assert quote.snapshot["price_cents"] == 2500


def test_modo_desligado_nao_aceita_cotacao() -> None:
    desligada = store({"shipping.fake": True}, {"shipping": {"enabled": False, "provider": "fake"}})
    assert cotar(desligada, escolha()).problems == ("mode_unavailable",)


def test_o_motivo_da_recusa_sobrevive_ao_filtro() -> None:
    """A loja do Silvio: produto cadastrado com 5 m de lado, todas as transportadoras recusam.

    A tela dizia "nenhuma transportadora atende esse endereço", mandando conferir o CEP, quando
    o Melhor Envio tinha respondido 200 e explicado serviço por serviço que o volume não cabe.
    Nós descartávamos a explicação junto com a opção.
    """
    from app.shipping.provider import ShippingOption
    from app.shipping.service import _refusals

    recusadas = (
        ShippingOption("1", "PAC", "Correios", 0, None, error="As dimensões excedem o limite."),
        ShippingOption("2", "SEDEX", "Correios", 0, None, error="As dimensões excedem o limite."),
        ShippingOption("3", ".Package", "Jadlog", 0, None, error="Peso acima do permitido"),
    )
    # Mesma frase não repete, e o que volta é o texto deles.
    assert _refusals(recusadas) == ("As dimensões excedem o limite.", "Peso acima do permitido")

    # Quebra de linha e espaço sobrando viram uma frase só; no máximo três motivos.
    muitas = tuple(
        ShippingOption(str(i), "S", "C", 0, None, error=f"motivo\n  {i}") for i in range(10)
    )
    assert _refusals(muitas) == ("motivo 0", "motivo 1", "motivo 2")

    # Sem erro nenhum (cotação boa) não há o que dizer.
    assert _refusals((ShippingOption("1", "PAC", "Correios", 1500, 5),)) == ()
