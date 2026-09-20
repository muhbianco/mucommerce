from __future__ import annotations

import base64
import os

import pytest

from app.core.crypto import CredentialCipher, CredentialCipherError, mask_secret


def _key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


def test_roundtrip_and_aad_binding() -> None:
    cipher = CredentialCipher(_key(), key_version=1)
    aad = CredentialCipher.aad("tenant-a", "mercadopago", "access_token")
    encrypted = cipher.encrypt("APP_USR-secret-1234", aad)
    assert encrypted.key_version == 1
    assert cipher.decrypt(encrypted, aad) == "APP_USR-secret-1234"

    other_aad = CredentialCipher.aad("tenant-b", "mercadopago", "access_token")
    with pytest.raises(CredentialCipherError):
        cipher.decrypt(encrypted, other_aad)


def test_key_rotation_keeps_old_versions_readable() -> None:
    old_key, new_key = _key(), _key()
    old_cipher = CredentialCipher(old_key, key_version=1)
    aad = CredentialCipher.aad("t", "p", "k")
    encrypted = old_cipher.encrypt("value", aad)

    rotated = CredentialCipher(new_key, key_version=2, previous_keys={1: old_key})
    assert rotated.decrypt(encrypted, aad) == "value"
    assert rotated.encrypt("value", aad).key_version == 2

    without_history = CredentialCipher(new_key, key_version=2)
    with pytest.raises(CredentialCipherError):
        without_history.decrypt(encrypted, aad)


def test_invalid_master_key_rejected() -> None:
    with pytest.raises(CredentialCipherError):
        CredentialCipher("not-base64-32-bytes", key_version=1)
    with pytest.raises(CredentialCipherError):
        CredentialCipher("", key_version=1)


def test_mask_secret() -> None:
    assert mask_secret("APP_USR-1234567890") == "**************7890"
    assert mask_secret("abc") == "***"
