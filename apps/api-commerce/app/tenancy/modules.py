"""Catálogo de módulos da loja: o que cada flag liga, quem manda nela e com que valor ela nasce.

Antes disto, `DEFAULT_FEATURE_FLAGS` era uma lista de chaves sem significado nenhum fora da
cabeça de quem escreveu. A tela precisava de rótulo, o servidor precisava saber quem pode mexer
em quê, e as duas coisas viviam separadas — que é como nasce o bug de deixar o cliente ligar um
módulo pago sem cobrar.

Cada módulo tem **um dono**, e só ele escreve (ADR 0019):

- `store` — o lojista, no painel da loja. Incluído na mensalidade.
- `subscription` — a compra no catálogo de serviços do site: ligar é comprar, e quem liga e
  desliga é o provisionamento chamado pelo api-agents (carteira, termos e renovação vivem lá).
- `platform` — a MuhBianco, no admin do site.

Os valores com que uma loja nasce (`DEFAULT_FEATURE_FLAGS`) saem daqui também.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Owner = Literal["store", "subscription", "platform"]


@dataclass(frozen=True, slots=True)
class Module:
    key: str
    label: str
    #: O que o lojista ganha ao ligar, na língua dele.
    summary: str
    owner: Owner
    #: Valor na loja recém-criada.
    default_on: bool = False
    #: Onde ele configura depois de ligar (rota do painel), quando há onde.
    where: str | None = None
    #: Ligado para sempre: aparece no painel, mas não desliga.
    always_on: bool = False
    #: Por que o lojista não mexe (aparece na tela em vez de um cadeado sem explicação).
    locked_reason: str | None = None
    #: Módulos que precisam estar ligados antes deste.
    requires: tuple[str, ...] = field(default_factory=tuple)

    @property
    def self_service(self) -> bool:
        return self.owner == "store"


MODULES: tuple[Module, ...] = (
    # --- do lojista: liga e desliga no painel ------------------------------------------
    Module(
        key="catalog",
        label="Catálogo",
        summary="Produtos, categorias e a vitrine com preço.",
        owner="store",
        default_on=True,
        where="produtos",
    ),
    Module(
        key="inventory",
        label="Estoque",
        summary="Saldo por variante, entradas e saídas com histórico.",
        owner="store",
        default_on=True,
        where="estoque",
        requires=("catalog",),
    ),
    Module(
        key="events",
        label="Eventos e ingressos",
        summary="Produtos com data, lotes e controle de ingressos.",
        owner="store",
        where="produtos",
        requires=("catalog",),
    ),
    Module(
        key="checkout",
        label="Carrinho e checkout",
        summary="O cliente monta o carrinho, escolhe entrega e paga.",
        owner="store",
        default_on=True,
        where="entrega",
        # O carrinho é de uma pessoa: sem login não há a quem pertencer. Sem esta dependência
        # dá para deixar a loja num estado em que a vitrine abre e ninguém consegue comprar.
        requires=("catalog", "customer_login"),
    ),
    Module(
        key="pickup",
        label="Retirada no local",
        summary="O cliente busca no balcão, sem frete.",
        owner="store",
        where="entrega",
        requires=("checkout",),
    ),
    Module(
        key="delivery",
        label="Entrega própria",
        summary="Você entrega, com taxa por faixa de CEP ou bairro.",
        owner="store",
        where="entrega",
        requires=("checkout",),
    ),
    Module(
        key="shipping.melhorenvio",
        label="Envio por transportadora",
        summary="Correios, Jadlog e Azul Cargo com preço pelo CEP, etiqueta e rastreio.",
        owner="store",
        default_on=True,
        where="envio",
        requires=("checkout",),
    ),
    Module(
        key="coupons",
        label="Cupons",
        summary="Desconto por código, com limite de uso e validade.",
        owner="store",
        default_on=True,
        where="cupons",
        requires=("checkout",),
    ),
    Module(
        key="payments.mercadopago",
        label="Mercado Pago",
        summary="Pix e cartão pelo Mercado Pago, com a conta da loja.",
        owner="store",
        where="pagamentos",
        requires=("checkout",),
    ),
    Module(
        key="payments.pagbank",
        label="PagBank",
        summary="Pix, cartão e boleto na página do PagBank, com a conta da loja.",
        owner="store",
        where="pagamentos",
        requires=("checkout",),
    ),
    Module(
        key="payments.infinitepay",
        label="InfinitePay",
        summary="Link de pagamento da InfinitePay, com a conta da loja.",
        owner="store",
        where="pagamentos",
        requires=("checkout",),
    ),
    Module(
        key="customer_login",
        label="Login dos clientes",
        summary="Quem compra entra com a conta Google dele. É o que dá dono ao carrinho.",
        owner="store",
        default_on=True,
        # Sem login não há carrinho, nem cliente aprovado, nem histórico de pedido: desligar
        # não deixa a loja mais simples, só quebrada.
        always_on=True,
    ),
    Module(
        key="customer_phone_otp",
        label="Confirmação de WhatsApp",
        summary=(
            "O cliente confirma o número antes de comprar, mandando uma mensagem pronta para o "
            "WhatsApp da MuhBianco."
        ),
        owner="store",
        requires=("customer_login",),
    ),
    Module(
        key="manufacturing",
        label="Produção e insumos",
        summary="Fichas técnicas, insumos e custo por produto.",
        owner="store",
        default_on=True,
    ),
    # --- da assinatura: liga com a compra no catálogo de serviços ----------------------
    Module(
        key="chatwoot",
        label="Atendimento omnichannel (Chatwoot)",
        summary="Mesa de atendimento com as conversas da loja num lugar só.",
        owner="subscription",
        locked_reason="É um serviço à parte: contrate em muhbianco.com.br/conta.",
    ),
    Module(
        key="sales_agent",
        label="Assistente de vendas",
        summary=(
            "Um agente que atende e vende pelo WhatsApp da sua empresa, com o catálogo e os "
            "pedidos da loja."
        ),
        owner="subscription",
        locked_reason="Em breve, como serviço à parte na sua conta MuhBianco.",
    ),
    # --- da MuhBianco: liga pelo admin do site -----------------------------------------
    Module(
        key="landing_ai",
        label="Montagem da vitrine com IA",
        summary=(
            "Você conta do seu negócio e a gente monta uma proposta de página inicial para "
            "você aprovar."
        ),
        owner="platform",
        where="vitrine",
        # Toda loja já pode pedir algumas propostas por mês sem contratar nada; o módulo abre
        # o teto maior. Por isso ele não é o portão de usar — é o portão de usar mais.
        locked_reason="Precisa de mais propostas que as do mês? Fale com a MuhBianco.",
    ),
)

BY_KEY: dict[str, Module] = {module.key: module for module in MODULES}
#: O que o lojista liga sozinho pelo painel.
SELF_SERVICE: frozenset[str] = frozenset(m.key for m in MODULES if m.owner == "store")
#: O que a MuhBianco liga pelo admin do site.
PLATFORM: frozenset[str] = frozenset(m.key for m in MODULES if m.owner == "platform")
#: O que não desliga, nem pelo painel.
ALWAYS_ON: frozenset[str] = frozenset(m.key for m in MODULES if m.always_on)
#: Como a loja nasce: `TenantService.create` grava uma linha por chave.
DEFAULTS: dict[str, bool] = {m.key: m.default_on for m in MODULES}


def dependents(key: str) -> list[str]:
    """Quem deixa de funcionar se `key` for desligado."""
    return [m.key for m in MODULES if key in m.requires]


def missing_requirements(key: str, enabled: dict[str, bool]) -> list[str]:
    """O que precisa estar ligado antes de `key`."""
    module = BY_KEY.get(key)
    if module is None:
        return []
    return [req for req in module.requires if not enabled.get(req, False)]
