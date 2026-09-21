"""Brazilian phone numbers in E.164 (digits only, `55…`), same rules as api-agents
(`app/core/phone.py` there): the WhatsApp id of a mobile sometimes comes without the 9th digit,
so comparisons accept both forms."""

from __future__ import annotations

import re

_DIGITS = re.compile(r"\D+")


def normalize_br_phone(raw: str | None) -> str | None:
    """`+55 11 99999-8888`, `(11) 99999-8888`, `5511999998888`… → `5511999998888`; else None."""
    digits = _DIGITS.sub("", str(raw or ""))
    if digits.startswith("55") and len(digits) in {12, 13}:
        national = digits[2:]
    elif len(digits) in {10, 11}:
        national = digits
    else:
        return None
    ddd = national[:2]
    if not ddd.isdigit() or not 11 <= int(ddd) <= 99:
        return None
    if len(national) == 11 and national[2] != "9":
        return None
    return f"55{national}"


def br_phone_variants(raw: str | None) -> set[str]:
    """The same mobile with and without the 9th digit."""
    normalized = normalize_br_phone(raw)
    if normalized is None:
        return set()
    variants = {normalized}
    national = normalized[2:]
    if len(national) == 11 and national[2] == "9":
        variants.add(f"55{national[:2]}{national[3:]}")
    elif len(national) == 10 and national[2] in "6789":
        variants.add(f"55{national[:2]}9{national[2:]}")
    return variants


def same_br_phone(a: str | None, b: str | None) -> bool:
    return bool(br_phone_variants(a) & br_phone_variants(b))


def mask_phone(phone: str | None) -> str | None:
    if not phone or len(phone) < 8:
        return None
    return f"+{phone[:4]} •••• {phone[-4:]}"
