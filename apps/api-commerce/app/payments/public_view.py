"""O que a vitrine pode contar sobre pagamento, antes de existir carrinho.

A loja anuncia "em até 12x" na página do produto, e quem lê ainda não entrou, não escolheu
nada e não tem pedido. Então o que sai daqui é a **regra**, não um valor pronto: o acréscimo
é percentual sobre o total do pedido (frete incluído — a maquininha cobra em cima do que
passa), e não dá para pré-calcular por produto.

Nada de segredo, nada de credencial, nada de nome de provedor: só o número máximo de parcelas
e as faixas de acréscimo, que a Lei 13.455/2017 obriga a informar antes da escolha de qualquer
forma. Quem decide o que é público é este arquivo, no mesmo molde de
`app.fulfillment.service.public_fulfillment`.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.credentials import CredentialStore
from app.payments import registry
from app.payments.models import TenantPaymentConfig
from app.payments.registry import PaymentOption
from app.tenancy.context import TenantContext
from app.tenancy.settings_schemas import PaymentsV1, payments_settings


def public_payments(tenant: TenantContext, options: list[PaymentOption]) -> dict[str, Any]:
    """Meios aceitos, teto de parcelamento e as faixas de acréscimo do cartão."""
    if not options:
        return {"methods": [], "card_installments_max": 1, "surcharge": None}

    methods = sorted({method for option in options for method in option.methods})
    # O teto que a vitrine anuncia é o do meio mais generoso que a loja aceita hoje.
    installments_max = max((option.installments_max for option in options), default=1)
    cfg: PaymentsV1 = payments_settings(tenant.settings)

    surcharge: dict[str, Any] | None = None
    if cfg.enabled:
        surcharge = {
            # Por meio, para a tela dizer "no Pix sai por X".
            "by_method": {
                method: {"percent_bps": rule.percent_bps, "fixed_cents": rule.fixed_cents}
                for method, rule in cfg.surcharge.items()
            },
            # Faixas do cartão, da menor para a maior: a primeira que cobre o parcelamento vence.
            "card_installments": [
                {"up_to": f.up_to, "percent_bps": f.percent_bps, "fixed_cents": f.fixed_cents}
                for f in sorted(cfg.card_installments, key=lambda f: f.up_to)
            ],
        }

    return {
        "methods": methods,
        "card_installments_max": max(1, min(installments_max, 12)),
        "surcharge": surcharge,
    }


async def load_public_payments(session: AsyncSession, tenant: TenantContext) -> dict[str, Any]:
    """Lê o que a vitrine anuncia. Sem `Actor`: é leitura pública, não muda nada e não audita."""
    stmt = select(TenantPaymentConfig).order_by(TenantPaymentConfig.provider).limit(10)
    configs = list((await session.execute(stmt)).scalars())
    if not configs:
        return public_payments(tenant, [])
    store = CredentialStore(session, tenant.id)
    secrets = {c.provider: set(await store.status(c.provider)) for c in configs}
    return public_payments(tenant, registry.options_for(tenant, configs, secrets))
