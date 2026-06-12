from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import and_, delete, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.auth import (
    Organization,
    PlatformMaintenanceAnnouncement,
    PlatformMaintenanceAnnouncementTarget,
    PlatformMaintenanceStatus,
)

UTC = timezone.utc


def _as_utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class PlatformMaintenanceRepository:
    @staticmethod
    def derive_ends_at(starts_at: datetime, estimated_duration_minutes: int) -> datetime:
        return _as_utc_datetime(starts_at) + timedelta(minutes=estimated_duration_minutes)

    async def list_overlapping_announcements(
        self,
        db: AsyncSession,
        *,
        starts_at: datetime,
        estimated_duration_minutes: int,
        broadcast_all_tenants: bool,
        target_tenant_ids: list[str],
        exclude_announcement_id: str | None = None,
    ) -> list[PlatformMaintenanceAnnouncement]:
        window_start = _as_utc_datetime(starts_at)
        window_end = self.derive_ends_at(starts_at, estimated_duration_minutes)
        statement = (
            select(PlatformMaintenanceAnnouncement)
            .options(selectinload(PlatformMaintenanceAnnouncement.targets))
            .where(
                PlatformMaintenanceAnnouncement.status.in_(
                    [
                        PlatformMaintenanceStatus.SCHEDULED,
                        PlatformMaintenanceStatus.ACTIVE,
                    ]
                ),
                PlatformMaintenanceAnnouncement.starts_at < window_end,
                PlatformMaintenanceAnnouncement.ends_at > window_start,
            )
        )
        if exclude_announcement_id:
            statement = statement.where(PlatformMaintenanceAnnouncement.id != exclude_announcement_id)
        result = await db.execute(statement)
        candidates = list(result.scalars().all())
        requested_targets = set(target_tenant_ids)
        overlapping: list[PlatformMaintenanceAnnouncement] = []
        for candidate in candidates:
            candidate_targets = {target.tenant_id for target in candidate.targets}
            if broadcast_all_tenants or candidate.broadcast_all_tenants or requested_targets.intersection(candidate_targets):
                overlapping.append(candidate)
        return overlapping

    async def list_all(self, db: AsyncSession) -> list[PlatformMaintenanceAnnouncement]:
        result = await db.execute(
            select(PlatformMaintenanceAnnouncement)
            .options(selectinload(PlatformMaintenanceAnnouncement.targets))
            .order_by(
                PlatformMaintenanceAnnouncement.starts_at.desc(),
                PlatformMaintenanceAnnouncement.created_at.desc(),
            )
        )
        return list(result.scalars().all())

    async def get_by_id(self, db: AsyncSession, announcement_id: str) -> PlatformMaintenanceAnnouncement | None:
        result = await db.execute(
            select(PlatformMaintenanceAnnouncement)
            .options(selectinload(PlatformMaintenanceAnnouncement.targets))
            .where(PlatformMaintenanceAnnouncement.id == announcement_id)
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        db: AsyncSession,
        *,
        title: str,
        severity,
        message: str,
        starts_at: datetime,
        estimated_duration_minutes: int,
        status,
        broadcast_all_tenants: bool,
        target_tenant_ids: list[str],
        created_by_user_id: str,
    ) -> PlatformMaintenanceAnnouncement:
        now = datetime.now(UTC)
        announcement = PlatformMaintenanceAnnouncement(
            id=str(uuid4()),
            title=title,
            severity=severity,
            message=message,
            starts_at=_as_utc_datetime(starts_at),
            estimated_duration_minutes=estimated_duration_minutes,
            ends_at=self.derive_ends_at(starts_at, estimated_duration_minutes),
            status=status,
            broadcast_all_tenants=broadcast_all_tenants,
            created_by_user_id=created_by_user_id,
            updated_by_user_id=created_by_user_id,
            created_at=now,
            updated_at=now,
        )
        announcement.targets = [
            PlatformMaintenanceAnnouncementTarget(
                id=str(uuid4()),
                tenant_id=tenant_id,
                created_at=now,
            )
            for tenant_id in target_tenant_ids
        ]
        db.add(announcement)
        await db.flush()
        await db.refresh(announcement, attribute_names=["targets"])
        return announcement

    async def update(
        self,
        db: AsyncSession,
        announcement: PlatformMaintenanceAnnouncement,
        *,
        title: str,
        severity,
        message: str,
        starts_at: datetime,
        estimated_duration_minutes: int,
        status,
        broadcast_all_tenants: bool,
        target_tenant_ids: list[str],
        updated_by_user_id: str,
    ) -> PlatformMaintenanceAnnouncement:
        announcement.title = title
        announcement.severity = severity
        announcement.message = message
        announcement.starts_at = _as_utc_datetime(starts_at)
        announcement.estimated_duration_minutes = estimated_duration_minutes
        announcement.ends_at = self.derive_ends_at(starts_at, estimated_duration_minutes)
        announcement.status = status
        announcement.broadcast_all_tenants = broadcast_all_tenants
        announcement.updated_by_user_id = updated_by_user_id
        announcement.updated_at = datetime.now(UTC)

        await db.execute(
            delete(PlatformMaintenanceAnnouncementTarget).where(
                PlatformMaintenanceAnnouncementTarget.announcement_id == announcement.id
            )
        )
        announcement.targets = [
            PlatformMaintenanceAnnouncementTarget(
                id=str(uuid4()),
                announcement_id=announcement.id,
                tenant_id=tenant_id,
            )
            for tenant_id in target_tenant_ids
        ]
        await db.flush()
        await db.refresh(announcement, attribute_names=["targets"])
        return announcement

    async def delete(
        self,
        db: AsyncSession,
        announcement: PlatformMaintenanceAnnouncement,
    ) -> None:
        await db.delete(announcement)
        await db.flush()

    async def list_current_for_tenant(
        self,
        db: AsyncSession,
        tenant_id: str,
        *,
        now: datetime | None = None,
    ) -> list[PlatformMaintenanceAnnouncement]:
        resolved_now = _as_utc_datetime(now or datetime.now(UTC))
        target_exists = exists(
            select(1).where(
                and_(
                    PlatformMaintenanceAnnouncementTarget.announcement_id == PlatformMaintenanceAnnouncement.id,
                    PlatformMaintenanceAnnouncementTarget.tenant_id == tenant_id,
                )
            )
        )
        result = await db.execute(
            select(PlatformMaintenanceAnnouncement)
            .options(selectinload(PlatformMaintenanceAnnouncement.targets))
            .where(
                exists(
                    select(1).where(
                        Organization.id == tenant_id,
                        Organization.is_active.is_(True),
                    )
                ),
                PlatformMaintenanceAnnouncement.status.in_(
                    [
                        PlatformMaintenanceStatus.SCHEDULED,
                        PlatformMaintenanceStatus.ACTIVE,
                    ]
                ),
                PlatformMaintenanceAnnouncement.ends_at >= resolved_now,
                or_(
                    PlatformMaintenanceAnnouncement.broadcast_all_tenants.is_(True),
                    target_exists,
                ),
            )
            .order_by(
                PlatformMaintenanceAnnouncement.starts_at.asc(),
                PlatformMaintenanceAnnouncement.created_at.desc(),
            )
        )
        return list(result.scalars().all())
