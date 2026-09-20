from __future__ import annotations

import re

_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")


class InvalidHostnameError(ValueError):
    pass


def normalize_hostname(raw: str | None) -> str:
    """Lowercase, strip port and trailing dot, IDNA-encode. Raises on garbage.

    The result is what we store in `tenant_domains.hostname` and what we compare
    the incoming `Host` header against. Never build URLs from the raw header.
    """
    if not raw:
        raise InvalidHostnameError("hostname vazio")
    host = raw.strip().lower()
    if host.startswith("["):  # IPv6 literal: never a tenant host
        raise InvalidHostnameError("hostname IPv6 não suportado")
    host = host.split(":", 1)[0].rstrip(".")
    if not host or len(host) > 253:
        raise InvalidHostnameError("hostname com tamanho inválido")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise InvalidHostnameError("hostname inválido") from exc
    labels = host.split(".")
    if len(labels) < 2 or any(not _LABEL.match(label) for label in labels):
        raise InvalidHostnameError("hostname inválido")
    return host


def is_subdomain_of(host: str, base: str) -> bool:
    return host == base or host.endswith("." + base)
