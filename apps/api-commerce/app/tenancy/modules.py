"""Catálogo de módulos da loja: o que cada flag liga, quem pode ligar e o que ela exige.

Antes disto, `DEFAULT_FEATURE_FLAGS` era uma lista de chaves sem significado nenhum fora da
cabeça de quem escreveu. A tela precisava de rótulo, o servidor precisava saber o que o lojista
pode mexer sozinho, e as duas coisas viviam separadas — que é como nasce o bug de deixar o
cliente ligar um módulo pago sem cobrar.

Regra que dá nome ao arquivo: **ligar módulo pago é compra**, e compra acontece na loja de
serviços do site, onde existe carteira, termos e renovação. O painel liga o que não cobra.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Module:
    key: str
    label: str
    #: O que o lojista ganha ao ligar, na língua dele.
    summary: str
    #: Onde ele configura depois de ligar (rota do painel), quando há onde.
    where: str | None = None
    #: O próprio lojista liga? `False` = cobra, ou é perigoso, ou é da plataforma.
    self_service: bool = False
    #: Por que não é self-service (aparece na tela em vez de um cadeado sem explicação).
    locked_reason: str | None = None
    #: Módulos que precisam estar ligados antes deste.
    requires: tuple[str, ...] = field(default_factory=tuple)


MODULES: tuple[Module, ...] = (
    Module(
        key="catalog",
        label="Catálogo",
        summary="Produtos, categorias e a vitrine com preço.",
        where="produtos",
        self_service=True,
    ),
    Module(
        key="inventory",
        label="Estoque",
        summary="Saldo por variante, entradas e saídas com histórico.",
        where="estoque",
        self_service=True,
        requires=("catalog",),
    ),
    Module(
        key="events",
        label="Eventos e ingressos",
        summary="Produtos com data, lotes e controle de ingressos.",
        where="produtos",
        self_service=True,
        requires=("catalog",),
    ),
    Module(
        key="checkout",
        label="Carrinho e checkout",
        summary="O cliente monta o carrinho, escolhe entrega e paga.",
        where="entrega",
        self_service=True,
        requires=("catalog",),
    ),
    Module(
        key="pickup",
        label="Retirada no local",
        summary="O cliente busca no balcão, sem frete.",
        where="entrega",
        self_service=True,
        requires=("checkout",),
    ),
    Module(
        key="delivery",
        label="Entrega própria",
        summary="Você entrega, com taxa por faixa de CEP ou bairro.",
        where="entrega",
        self_service=True,
        requires=("checkout",),
    ),
    Module(
        key="shipping.melhorenvio",
        label="Envio por transportadora",
        summary="Correios, Jadlog e Azul Cargo com preço pelo CEP, etiqueta e rastreio.",
        where="envio",
        self_service=True,
        requires=("checkout",),
    ),
    Module(
        key="coupons",
        label="Cupons",
        summary="Desconto por código, com limite de uso e validade.",
        where="cupons",
        self_service=True,
        requires=("checkout",),
    ),
    Module(
        key="payments.mercadopago",
        label="Mercado Pago",
        summary="Pix e cartão pelo Mercado Pago, com a conta da loja.",
        where="pagamentos",
        self_service=True,
        requires=("checkout",),
    ),
    Module(
        key="payments.infinitepay",
        label="InfinitePay",
        summary="Link de pagamento da InfinitePay, com a conta da loja.",
        where="pagamentos",
        self_service=True,
        requires=("checkout",),
    ),
    # --- daqui para baixo, quem liga é a MuhBianco -------------------------------------
    Module(
        key="storefront",
        label="Vitrine no ar",
        summary="A loja responde no endereço dela.",
        locked_reason="É a chave geral da loja; quem liga e desliga é a MuhBianco.",
    ),
    Module(
        key="customer_login",
        label="Login dos clientes",
        summary="Quem compra entra com a conta Google dele.",
        locked_reason="Depende de configuração da plataforma. Peça para a MuhBianco.",
    ),
    Module(
        key="customer_phone_otp",
        label="Confirmação de WhatsApp",
        summary="O cliente confirma o número antes de comprar.",
        locked_reason="Depende de configuração da plataforma. Peça para a MuhBianco.",
    ),
    Module(
        key="chatwoot",
        label="Atendimento (Chatwoot)",
        summary="Mesa de atendimento com as conversas da loja.",
        locked_reason="É um serviço à parte: contrate em muhbianco.com.br/conta.",
    ),
    Module(
        key="sales_agent",
        label="Assistente de vendas",
        summary="Um agente que atende e vende pelo WhatsApp.",
        locked_reason="É um serviço à parte: contrate em muhbianco.com.br/conta.",
    ),
    Module(
        key="whatsapp_owned",
        label="WhatsApp próprio",
        summary="O atendimento sai do número da sua empresa.",
        locked_reason="É um serviço à parte: contrate em muhbianco.com.br/conta.",
    ),
    Module(
        key="manufacturing",
        label="Produção e insumos",
        summary="Fichas técnicas, insumos e custo por produto.",
        locked_reason="Ainda não está disponível.",
    ),
)

BY_KEY: dict[str, Module] = {module.key: module for module in MODULES}
#: O que o lojista liga sozinho pelo painel. Tudo que não estiver aqui é da plataforma.
SELF_SERVICE: frozenset[str] = frozenset(m.key for m in MODULES if m.self_service)


def dependents(key: str) -> list[str]:
    """Quem deixa de funcionar se `key` for desligado."""
    return [m.key for m in MODULES if key in m.requires]


def missing_requirements(key: str, enabled: dict[str, bool]) -> list[str]:
    """O que precisa estar ligado antes de `key`."""
    module = BY_KEY.get(key)
    if module is None:
        return []
    return [req for req in module.requires if not enabled.get(req, False)]
