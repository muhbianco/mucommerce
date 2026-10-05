"""CPF e CNPJ: só dígitos, com os dígitos verificadores conferidos.

A transportadora (Melhor Envio) recusa a etiqueta sem o documento de quem recebe; um número
digitado errado só apareceria lá, na hora de despachar. Conferir aqui pega o erro no checkout.
"""

from __future__ import annotations

import re

_NON_DIGIT = re.compile(r"\D")


def _cpf_ok(digits: str) -> bool:
    if len(set(digits)) == 1:  # 000.000.000-00 e afins passam na conta e não existem
        return False
    for size in (9, 10):
        total = sum(int(d) * w for d, w in zip(digits[:size], range(size + 1, 1, -1), strict=True))
        check = (total * 10) % 11 % 10
        if check != int(digits[size]):
            return False
    return True


def _cnpj_ok(digits: str) -> bool:
    if len(set(digits)) == 1:
        return False
    weights = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    for size in (12, 13):
        w = weights if size == 12 else [6, *weights]
        total = sum(int(d) * k for d, k in zip(digits[:size], w, strict=True))
        rest = total % 11
        check = 0 if rest < 2 else 11 - rest
        if check != int(digits[size]):
            return False
    return True


def normalize_document(raw: str) -> str:
    """`123.456.789-09` → `12345678909`. ValueError se não for CPF nem CNPJ válido."""
    digits = _NON_DIGIT.sub("", raw or "")
    if len(digits) == 11 and _cpf_ok(digits):
        return digits
    if len(digits) == 14 and _cnpj_ok(digits):
        return digits
    raise ValueError("CPF ou CNPJ inválido")


def mask_document(digits: str | None) -> str | None:
    """Para mostrar sem expor: `***.456.789-**` / `**.345.678/0001-**`."""
    if not digits:
        return None
    if len(digits) == 11:
        return f"***.{digits[3:6]}.{digits[6:9]}-**"
    if len(digits) == 14:
        return f"**.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-**"
    return "***"
