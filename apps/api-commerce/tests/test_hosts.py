from __future__ import annotations

import pytest

from app.core.hosts import InvalidHostnameError, normalize_hostname
from app.tenancy.service import classify_kind


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Lunares.com.br", "lunares.com.br"),
        ("lunares.com.br:443", "lunares.com.br"),
        ("lunares.com.br.", "lunares.com.br"),
        ("  www.Lunares.COM.BR ", "www.lunares.com.br"),
        ("açaí.com.br", "xn--aa-4iaz.com.br"),
    ],
)
def test_normalize_hostname(raw: str, expected: str) -> None:
    assert normalize_hostname(raw) == expected


@pytest.mark.parametrize("raw", ["", "localhost", "[::1]", "bad_host.com", "-x.com", "a" * 300])
def test_normalize_hostname_rejects_garbage(raw: str) -> None:
    with pytest.raises(InvalidHostnameError):
        normalize_hostname(raw)


@pytest.mark.parametrize(
    ("host", "kind"),
    [
        ("lunares.com.br", "custom_apex"),
        ("lunares.com", "custom_apex"),
        ("www.lunares.com.br", "custom_subdomain"),
        ("loja.lunares.com.br", "custom_subdomain"),
        ("shop.lunares.com", "custom_subdomain"),
    ],
)
def test_classify_kind(host: str, kind: str) -> None:
    assert classify_kind(host) == kind
