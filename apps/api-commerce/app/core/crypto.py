from __future__ import annotations

import base64
import binascii
import os
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import settings


class CredentialCipherError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class EncryptedValue:
    ciphertext: bytes
    nonce: bytes
    key_version: int


def _decode_key(raw: str) -> bytes:
    padded = raw.strip()
    for decoder in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            key = decoder(padded + "=" * (-len(padded) % 4))
        except (binascii.Error, ValueError):
            continue
        if len(key) == 32:
            return key
    raise CredentialCipherError("CREDENTIALS_MASTER_KEY must be base64 of exactly 32 bytes")


class CredentialCipher:
    """AES-256-GCM envelope for tenant credentials at rest.

    AAD binds the ciphertext to `tenant_id:provider:key_name`, so a value copied
    between rows (or tenants) fails to decrypt. Key rotation: keep old keys in
    `previous_keys` by version until a re-encrypt job has migrated every row.
    """

    def __init__(
        self, master_key: str, key_version: int, previous_keys: dict[int, str] | None = None
    ) -> None:
        if not master_key:
            raise CredentialCipherError("CREDENTIALS_MASTER_KEY is not configured")
        self._keys: dict[int, AESGCM] = {key_version: AESGCM(_decode_key(master_key))}
        for version, raw in (previous_keys or {}).items():
            self._keys[version] = AESGCM(_decode_key(raw))
        self.key_version = key_version

    @staticmethod
    def aad(tenant_id: str, provider: str, key_name: str) -> bytes:
        return f"{tenant_id}:{provider}:{key_name}".encode()

    def encrypt(self, plaintext: str, aad: bytes) -> EncryptedValue:
        nonce = os.urandom(12)
        ciphertext = self._keys[self.key_version].encrypt(nonce, plaintext.encode("utf-8"), aad)
        return EncryptedValue(ciphertext=ciphertext, nonce=nonce, key_version=self.key_version)

    def decrypt(self, value: EncryptedValue, aad: bytes) -> str:
        cipher = self._keys.get(value.key_version)
        if cipher is None:
            raise CredentialCipherError(f"No key for version {value.key_version}")
        try:
            return cipher.decrypt(value.nonce, value.ciphertext, aad).decode("utf-8")
        except InvalidTag as exc:
            raise CredentialCipherError("Ciphertext does not match key or AAD") from exc


def mask_secret(value: str, visible: int = 4) -> str:
    if len(value) <= visible:
        return "*" * len(value)
    return "*" * (len(value) - visible) + value[-visible:]


_cipher: CredentialCipher | None = None


def get_cipher() -> CredentialCipher:
    global _cipher
    if _cipher is None:
        _cipher = CredentialCipher(
            settings.credentials_master_key.get_secret_value(), settings.credentials_key_version
        )
    return _cipher
