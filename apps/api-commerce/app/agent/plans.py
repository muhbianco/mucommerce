"""O contrato de confirmação das ações do assistente (etapa H).

A regra do dono: **leitura responde direto; todo o resto passa por confirmação de quem é dono da
loja**, com o resumo enumerado para ele conferir ou apontar o que ajustar.

Como isso é garantido aqui, sem guardar estado nenhum: a rota calcula o efeito exato da ação a
partir dos parâmetros **e do estado atual da loja**, devolve o resumo e uma assinatura desse
efeito (`plan_hash`). Aplicar exige a assinatura de volta. Se qualquer coisa mudou no meio — o
pedido andou, o estoque mexeu, outro alguém cancelou — o efeito recalculado é outro, a assinatura
não bate e a ação é recusada em vez de fazer o que ninguém conferiu.

É melhor do que uma tabela de "ações pendentes" com validade: não há estado para expirar, não há
varredura para escrever, e o que se compara é o efeito, não um id opaco.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

#: 32 hex são 128 bits de espaço: chutar uma assinatura é inviável, e cabe num argumento de tool.
HASH_CHARS = 32


def plan_hash(action: str, effect: Mapping[str, Any]) -> str:
    """Assina o efeito planejado de uma ação.

    Canônico de propósito (`sort_keys`, sem espaço): a mesma intenção sobre o mesmo estado dá
    sempre a mesma assinatura, independente da ordem em que o dicionário foi montado.
    """
    canonical = json.dumps(
        {"acao": action, "efeito": effect},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:HASH_CHARS]


def brl(cents: int) -> str:
    """`3000` → `R$ 30,00`. O resumo é lido por uma pessoa, não por um parser."""
    sinal = "-" if cents < 0 else ""
    inteiro, centavos = divmod(abs(int(cents)), 100)
    milhar = f"{inteiro:,}".replace(",", ".")
    return f"{sinal}R$ {milhar},{centavos:02d}"


def qty(milli: int) -> str:
    """Milésimos → o número que a pessoa reconhece (`2500` → `2,5`)."""
    texto = f"{milli / 1000:g}"
    return texto.replace(".", ",")
