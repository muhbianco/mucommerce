from __future__ import annotations

import re
import unicodedata

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def slugify(text: str, *, max_length: int = 160) -> str:
    """ASCII, lowercase, hyphen-separated: "Pão de Mel 🍯" → "pao-de-mel".

    Returns "" when nothing alphanumeric survives; callers pick a fallback.
    """
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = _NON_ALNUM.sub("-", ascii_text.lower()).strip("-")
    return slug[:max_length].rstrip("-")


def next_free_slug(base: str, taken: set[str], *, max_length: int = 160) -> str | None:
    """`base`, else `base-2`, `base-3`… (trimmed to fit), else None when all 2..999 are taken."""
    if base not in taken:
        return base
    for n in range(2, 1000):
        suffix = f"-{n}"
        candidate = base[: max_length - len(suffix)].rstrip("-") + suffix
        if candidate not in taken:
            return candidate
    return None
