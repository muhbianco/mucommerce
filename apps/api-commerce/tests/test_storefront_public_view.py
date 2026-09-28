"""O que a vitrine pode anunciar antes de existir carrinho: parcelamento e escassez.

Duas perguntas guiam o arquivo: o que chega ao cliente (e é obrigação legal informar) e o que
**não** pode chegar — credencial, nome de provedor, número de estoque.
"""

from __future__ import annotations

import pytest

from app.catalog.models import Product, ProductVariant, StockPolicy
from app.catalog.storefront import LOW_STOCK_AT, variant_low_stock
from app.inventory.models import InventoryBalance
from app.payments.public_view import public_payments
from app.payments.registry import PaymentOption
from app.tenancy.context import TenantContext


def _tenant(settings: dict | None = None) -> TenantContext:
    return TenantContext(
        id="t1",
        slug="loja",
        public_key="pk",
        name="Loja",
        status="active",
        timezone="America/Sao_Paulo",
        locale="pt-BR",
        currency="BRL",
        host="loja.exemplo.com.br",
        settings=settings or {},
    )


def _option(*, installments_max: int = 1, methods: tuple[str, ...] = ("pix",)) -> PaymentOption:
    return PaymentOption(
        provider="fake",
        methods=methods,  # type: ignore[arg-type]
        mode="embedded",
        is_default=True,
        public_config={"public_key": "pk_visivel"},
        installments_max=installments_max,
    )


class TestParcelamentoPublico:
    def test_loja_sem_meio_configurado_nao_anuncia_parcela(self) -> None:
        # Sem provedor pronto, "em até 12x" seria promessa que o checkout não cumpre.
        publico = public_payments(_tenant(), [])
        assert publico["card_installments_max"] == 1
        assert publico["methods"] == []
        assert publico["surcharge"] is None

    def test_anuncia_o_teto_do_meio_mais_generoso(self) -> None:
        publico = public_payments(
            _tenant(),
            [_option(installments_max=3), _option(installments_max=10, methods=("card",))],
        )
        assert publico["card_installments_max"] == 10
        assert publico["methods"] == ["card", "pix"]

    def test_o_teto_nunca_passa_de_doze(self) -> None:
        # O schema do painel já limita, mas um dado velho no banco não pode virar "em até 99x".
        assert (
            public_payments(_tenant(), [_option(installments_max=99)])["card_installments_max"]
            == 12
        )

    def test_repasse_desligado_nao_manda_faixa_nenhuma(self) -> None:
        tenant = _tenant(
            {"payments": {"enabled": False, "surcharge": {"card": {"percent_bps": 300}}}}
        )
        assert public_payments(tenant, [_option()])["surcharge"] is None

    def test_repasse_ligado_manda_a_regra_e_nao_o_valor(self) -> None:
        # A lei manda informar antes da escolha, e o acréscimo é sobre o total do pedido —
        # então o que viaja é a regra; quem calcula é a tela, com o carrinho na mão.
        tenant = _tenant(
            {
                "payments": {
                    "enabled": True,
                    "surcharge": {"pix": {"percent_bps": 0, "fixed_cents": 0}},
                    "card_installments": [
                        {"up_to": 6, "percent_bps": 0, "fixed_cents": 0},
                        {"up_to": 12, "percent_bps": 450, "fixed_cents": 0},
                    ],
                }
            }
        )
        surcharge = public_payments(tenant, [_option(installments_max=12)])["surcharge"]
        assert surcharge is not None
        assert surcharge["by_method"]["pix"] == {"percent_bps": 0, "fixed_cents": 0}
        # Da menor para a maior: a tela lê a primeira faixa que cobre o parcelamento.
        assert [f["up_to"] for f in surcharge["card_installments"]] == [6, 12]
        assert surcharge["card_installments"][1]["percent_bps"] == 450

    def test_nada_de_credencial_nem_nome_de_provedor_sai_daqui(self) -> None:
        publico = public_payments(_tenant(), [_option()])
        texto = repr(publico)
        assert "pk_visivel" not in texto
        assert "fake" not in texto
        assert set(publico) == {"methods", "card_installments_max", "surcharge"}


class TestEstoqueBaixo:
    @staticmethod
    def _cenario(policy: StockPolicy, on_hand: int | None, reserved: int = 0):
        produto = Product(stock_policy=policy)
        variante = ProductVariant(id="v1", stock_policy=None)
        saldo = (
            None
            if on_hand is None
            else InventoryBalance(variant_id="v1", on_hand_milli=on_hand, reserved_milli=reserved)
        )
        return variante, produto, saldo

    def test_sobrando_pouco_esta_acabando(self) -> None:
        assert variant_low_stock(*self._cenario(StockPolicy.TRACKED, LOW_STOCK_AT * 1000))

    def test_sobrando_muito_nao_esta(self) -> None:
        assert not variant_low_stock(*self._cenario(StockPolicy.TRACKED, (LOW_STOCK_AT + 1) * 1000))

    def test_esgotado_nao_e_o_mesmo_que_acabando(self) -> None:
        # Zero já vira "esgotado" na disponibilidade; dizer "últimas unidades" seria mentira.
        assert not variant_low_stock(*self._cenario(StockPolicy.TRACKED, 0))

    def test_reservado_conta_contra_o_que_ainda_da_para_comprar(self) -> None:
        cheio = 100 * 1000
        assert variant_low_stock(*self._cenario(StockPolicy.TRACKED, cheio, cheio - 2000))

    @pytest.mark.parametrize("policy", [StockPolicy.MADE_TO_ORDER, StockPolicy.UNTRACKED])
    def test_quem_nao_controla_estoque_nunca_esta_acabando(self, policy: StockPolicy) -> None:
        assert not variant_low_stock(*self._cenario(policy, 1000))

    def test_sem_saldo_registrado_nao_inventa_escassez(self) -> None:
        assert not variant_low_stock(*self._cenario(StockPolicy.TRACKED, None))
