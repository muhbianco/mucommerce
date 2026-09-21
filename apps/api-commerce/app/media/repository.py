from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Sequence
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.media.models import MediaAsset, MediaStatus
from app.tenancy.context import CROSS_TENANT_OPTION

MAX_MEDIA_PER_OWNER = 12
MAINTENANCE_BATCH = 100


class MediaRepository:
    """Tenant-scoped queries (the ORM filter adds the tenant), plus the cross-tenant ones used
    by the maintenance job, which say so explicitly."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, media_id: str) -> MediaAsset | None:
        stmt = select(MediaAsset).where(MediaAsset.id == media_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def for_owner(self, owner_type: str, owner_id: str | None) -> Sequence[MediaAsset]:
        stmt = (
            select(MediaAsset)
            .where(MediaAsset.owner_type == owner_type)
            .where(
                MediaAsset.owner_id == owner_id
                if owner_id is not None
                else MediaAsset.owner_id.is_(None)
            )
            .order_by(MediaAsset.position, MediaAsset.id)
            .limit(MAX_MEDIA_PER_OWNER * 4)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def count_for_owner(self, owner_type: str, owner_id: str | None) -> int:
        """Slots in use: everything except failed uploads."""
        stmt = (
            select(func.count())
            .select_from(MediaAsset)
            .where(MediaAsset.owner_type == owner_type)
            .where(MediaAsset.status != MediaStatus.FAILED)
            .where(
                MediaAsset.owner_id == owner_id
                if owner_id is not None
                else MediaAsset.owner_id.is_(None)
            )
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def ready_for_owners(
        self, owner_type: str, owner_ids: Collection[str]
    ) -> dict[str, list[MediaAsset]]:
        """Ready media per owner, in display order (batch: one query for a page of products)."""
        if not owner_ids:
            return {}
        stmt = (
            select(MediaAsset)
            .where(MediaAsset.owner_type == owner_type)
            .where(MediaAsset.owner_id.in_(list(owner_ids)))
            .where(MediaAsset.status == MediaStatus.READY)
            .order_by(MediaAsset.owner_id, MediaAsset.position, MediaAsset.id)
        )
        grouped: dict[str, list[MediaAsset]] = defaultdict(list)
        for media in (await self.session.execute(stmt)).scalars():
            if media.owner_id is not None:
                grouped[media.owner_id].append(media)
        return dict(grouped)

    # --- maintenance (cross-tenant, explicit) ----------------------------------------
    async def stale(self, status: str, updated_before: datetime) -> Sequence[MediaAsset]:
        stmt = (
            select(MediaAsset)
            .where(MediaAsset.status == status)
            .where(MediaAsset.updated_at < updated_before)
            .order_by(MediaAsset.updated_at)
            .limit(MAINTENANCE_BATCH)
            .execution_options(**{CROSS_TENANT_OPTION: True})
        )
        return (await self.session.execute(stmt)).scalars().all()
