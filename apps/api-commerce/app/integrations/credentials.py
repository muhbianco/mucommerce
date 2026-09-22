"""A store's secrets for third parties (payment tokens, webhook secrets), encrypted at rest.

AES-GCM with the associated data `tenant:provider:key_name`: a value copied to another row or
store does not decrypt. The plaintext only leaves through `get`, inside the service that calls
the provider; the API and the audit log only ever see the masked form (last four characters).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import CredentialCipher, EncryptedValue, get_cipher
from app.models.base import utcnow
from app.tenancy.models import TenantIntegrationCredential


def mask(value: str) -> str:
    """Never reveals the length: "****" plus the last four characters."""
    return "****" + value[-4:] if len(value) > 8 else "****"


class CredentialStore:
    def __init__(self, session: AsyncSession, tenant_id: str) -> None:
        self.session = session
        self.tenant_id = tenant_id

    async def put(self, provider: str, key_name: str, value: str) -> str:
        """Store (or replace) a secret; returns its masked form."""
        cipher = get_cipher()
        encrypted = cipher.encrypt(value, CredentialCipher.aad(self.tenant_id, provider, key_name))
        row = await self._row(provider, key_name)
        if row is None:
            row = TenantIntegrationCredential(provider=provider, key_name=key_name)
            self.session.add(row)
        else:
            row.rotated_at = utcnow()
        row.ciphertext = encrypted.ciphertext
        row.nonce = encrypted.nonce
        row.key_version = encrypted.key_version
        row.masked = mask(value)
        await self.session.flush()
        return row.masked

    async def get(self, provider: str, key_name: str) -> str | None:
        row = await self._row(provider, key_name)
        if row is None:
            return None
        return get_cipher().decrypt(
            EncryptedValue(row.ciphertext, row.nonce, row.key_version),
            CredentialCipher.aad(self.tenant_id, provider, key_name),
        )

    async def status(self, provider: str) -> dict[str, tuple[str, datetime]]:
        """key_name → (masked, last changed), without decrypting anything."""
        stmt = select(TenantIntegrationCredential).where(
            TenantIntegrationCredential.provider == provider
        )
        return {
            row.key_name: (row.masked, row.rotated_at or row.created_at)
            for row in (await self.session.execute(stmt)).scalars()
        }

    async def delete(self, provider: str, key_name: str) -> None:
        await self.session.execute(
            delete(TenantIntegrationCredential)
            .where(TenantIntegrationCredential.provider == provider)
            .where(TenantIntegrationCredential.key_name == key_name)
            .execution_options(synchronize_session=False)
        )

    async def _row(self, provider: str, key_name: str) -> TenantIntegrationCredential | None:
        stmt = (
            select(TenantIntegrationCredential)
            .where(TenantIntegrationCredential.provider == provider)
            .where(TenantIntegrationCredential.key_name == key_name)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()
