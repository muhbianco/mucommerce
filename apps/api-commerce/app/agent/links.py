"""O vínculo entre um agente e uma loja, e a credencial que nasce dele.

Até aqui o assistente falava com a loja usando a **conta** do dono: `X-Account-Id`, associação,
papel dele. Serve enquanto quem fala é o assistente do próprio lojista, mas não serve para o
modo expor — ali o agente atende clientes finais, e carregar o papel do dono significa que uma
conversa torta pode pausar produto, mexer em estoque ou cancelar pedido.

O vínculo resolve isso dando ao agente uma credencial **própria**, com um conjunto fixo de
permissões e revogável sozinha, sem mexer na conta de ninguém.

Como se estabelece: o lojista gera um código no painel e passa para quem está configurando o
agente. O código é curto porque alguém vai ditá-lo por mensagem; é de uso único e morre em
pouco tempo porque código curto que vive muito é código que se adivinha. Quem o troca por uma
credencial é a api-agents, com o token interno — então nem o código sozinho basta.

Da credencial guardamos só o hash. Ela aparece uma vez, no resgate; depois disso nem nós
sabemos qual é, e um vazamento do banco não dá acesso a loja nenhuma.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Literal

from app.core.scopes import Scope

#: Sem I, O, 0 e 1: o código é ditado por voz ou mensagem, e esses quatro viram erro de digitação.
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8
#: Curto de propósito: o lojista gera, dita e o outro resgata na hora. Não é para guardar.
CODE_TTL = timedelta(minutes=30)

#: O que cada tipo de vínculo pode fazer. Fixo no código, não no banco: permissão que se edita
#: por UPDATE é permissão que ninguém revisa.
LinkKind = Literal["sales", "operator"]

SCOPES: dict[str, frozenset[Scope]] = {
    # Modo expor: atende e vende. Não pausa produto, não mexe em estoque, não cancela pedido —
    # o agente conversa com clientes finais, e nenhuma conversa deve chegar nessas alavancas.
    "sales": frozenset({Scope.CATALOG_READ, Scope.ORDERS_READ, Scope.ORDERS_WRITE}),
    # Assistente do próprio lojista: o que ele já faz hoje falando pela conta do dono. Inclui
    # cancelar, que devolve dinheiro — não por ser inofensivo, mas porque trocar a conta pela
    # credencial não é hora de tirar função que funciona. Quem segura o cancelamento é o
    # bilhete de confirmação do outro lado, que exige um turno da pessoa, e isso é mais forte
    # do que um escopo: escopo não sabe se alguém leu o resumo.
    "operator": frozenset(
        {
            Scope.CATALOG_READ,
            Scope.CATALOG_WRITE,
            Scope.INVENTORY_READ,
            Scope.INVENTORY_ADJUST,
            Scope.MANUFACTURING_READ,
            Scope.ORDERS_READ,
            Scope.ORDERS_WRITE,
            Scope.ORDERS_TRANSITION,
            Scope.ORDERS_CANCEL,
            Scope.CUSTOMERS_READ,
        }
    ),
}


def new_code() -> str:
    """Um código novo, em letras que ninguém confunde ao ditar."""
    return "".join(secrets.choice(_ALPHABET) for _ in range(CODE_LENGTH))


def normalize_code(raw: str) -> str:
    """Aceita o que a pessoa digitou: minúsculas, espaços e hífen no meio."""
    return "".join(ch for ch in (raw or "").upper() if ch in _ALPHABET)


def hash_code(code: str) -> str:
    """Determinístico porque a busca é pelo código; o que o protege é o prazo e o uso único."""
    return hashlib.sha256(normalize_code(code).encode()).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def scopes_for(kind: str) -> frozenset[str]:
    """As permissões deste tipo de vínculo, como strings — o formato que o resto compara."""
    return frozenset(str(scope) for scope in SCOPES.get(kind, frozenset()))


def expired(issued_at: datetime, now: datetime) -> bool:
    return now - issued_at > CODE_TTL
