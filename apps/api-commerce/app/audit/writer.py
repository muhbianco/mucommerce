from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditLog
from app.core.logging import request_id_var


async def audit(
    session: AsyncSession,
    *,
    actor: str,
    action: str,
    entity_type: str,
    entity_id: str | None,
    tenant_id: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> AuditLog:
    """Append one audit row in the caller's transaction. Never commits by itself."""
    entry = AuditLog(
        tenant_id=tenant_id,
        actor=actor,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before_json=before,
        after_json=after,
        ip=ip,
        user_agent=user_agent[:300] if user_agent else None,
        request_id=request_id_var.get() if request_id_var.get() != "-" else None,
    )
    session.add(entry)
    return entry
