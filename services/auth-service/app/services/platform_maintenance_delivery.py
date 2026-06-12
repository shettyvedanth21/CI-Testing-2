from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import Select, and_, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import AsyncSessionFactory
from app.models.auth import (
    Organization,
    PlatformMaintenanceAnnouncement,
    PlatformMaintenanceEmailDelivery,
    PlatformMaintenanceEmailDeliveryStatus,
    PlatformMaintenanceStatus,
    User,
    UserRole,
)
from app.repositories.platform_maintenance_repository import PlatformMaintenanceRepository
from app.services.mailer_service import mailer_svc
from app.services.platform_maintenance_status import (
    compute_platform_maintenance_effective_status,
)

logger = logging.getLogger("auth-service.platform-maintenance")
UTC = timezone.utc


@dataclass(frozen=True)
class EligibleMaintenanceRecipient:
    tenant_id: str
    user_id: str
    role: str
    email: str
    full_name: str | None


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _as_utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _is_reachable_status(status: PlatformMaintenanceStatus) -> bool:
    return status in {PlatformMaintenanceStatus.SCHEDULED, PlatformMaintenanceStatus.ACTIVE}


def _delivery_terminal(status: PlatformMaintenanceEmailDeliveryStatus) -> bool:
    return status in {
        PlatformMaintenanceEmailDeliveryStatus.SENT,
        PlatformMaintenanceEmailDeliveryStatus.SKIPPED,
        PlatformMaintenanceEmailDeliveryStatus.CANCELLED,
    }


def _normalize_email(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized or "@" not in normalized:
        return None
    return normalized


class PlatformMaintenanceDeliveryService:
    def __init__(self) -> None:
        self._repo = PlatformMaintenanceRepository()

    async def list_announcements_requiring_sync(
        self,
        session: AsyncSession,
    ) -> list[PlatformMaintenanceAnnouncement]:
        now = _utcnow()
        open_delivery_exists = exists(
            select(1).where(
                PlatformMaintenanceEmailDelivery.announcement_id == PlatformMaintenanceAnnouncement.id,
                PlatformMaintenanceEmailDelivery.status.in_([
                    PlatformMaintenanceEmailDeliveryStatus.PENDING,
                    PlatformMaintenanceEmailDeliveryStatus.PROCESSING,
                    PlatformMaintenanceEmailDeliveryStatus.FAILED,
                ]),
            )
        )
        result = await session.execute(
            select(PlatformMaintenanceAnnouncement)
            .options(selectinload(PlatformMaintenanceAnnouncement.targets))
            .where(
                or_(
                    and_(
                        PlatformMaintenanceAnnouncement.status.in_([
                            PlatformMaintenanceStatus.SCHEDULED,
                            PlatformMaintenanceStatus.ACTIVE,
                        ]),
                        PlatformMaintenanceAnnouncement.ends_at >= now,
                    ),
                    open_delivery_exists,
                )
            )
            .order_by(PlatformMaintenanceAnnouncement.created_at.desc())
        )
        return list(result.scalars().all())

    async def list_eligible_recipients(
        self,
        session: AsyncSession,
        announcement: PlatformMaintenanceAnnouncement,
    ) -> list[EligibleMaintenanceRecipient]:
        stmt: Select[tuple[User]] = (
            select(User)
            .join(Organization, Organization.id == User.tenant_id)
            .where(
                User.is_active.is_(True),
                Organization.is_active.is_(True),
                User.role.in_([
                    UserRole.ORG_ADMIN,
                    UserRole.PLANT_MANAGER,
                    UserRole.OPERATOR,
                    UserRole.VIEWER,
                ]),
            )
            .order_by(User.tenant_id.asc(), User.email.asc())
        )
        if announcement.broadcast_all_tenants:
            pass
        else:
            tenant_ids = [target.tenant_id for target in announcement.targets]
            if not tenant_ids:
                return []
            stmt = stmt.where(User.tenant_id.in_(tenant_ids))

        result = await session.execute(stmt)
        recipients: list[EligibleMaintenanceRecipient] = []
        for user in result.scalars().all():
            email = _normalize_email(user.email)
            if not email or user.tenant_id is None:
                continue
            recipients.append(
                EligibleMaintenanceRecipient(
                    tenant_id=user.tenant_id,
                    user_id=user.id,
                    role=user.role.value,
                    email=email,
                    full_name=user.full_name,
                )
            )
        return recipients

    async def sync_announcement(
        self,
        session: AsyncSession,
        announcement: PlatformMaintenanceAnnouncement,
    ) -> None:
        now = _utcnow()
        existing_result = await session.execute(
            select(PlatformMaintenanceEmailDelivery).where(
                PlatformMaintenanceEmailDelivery.announcement_id == announcement.id
            )
        )
        existing_rows = list(existing_result.scalars().all())
        existing_by_user_id = {row.user_id: row for row in existing_rows}

        if not _is_reachable_status(announcement.status):
            for row in existing_rows:
                if row.status in {
                    PlatformMaintenanceEmailDeliveryStatus.PENDING,
                    PlatformMaintenanceEmailDeliveryStatus.PROCESSING,
                    PlatformMaintenanceEmailDeliveryStatus.FAILED,
                }:
                    row.status = PlatformMaintenanceEmailDeliveryStatus.CANCELLED
                    row.cancelled_at = now
                    row.failure_code = "ANNOUNCEMENT_NOT_REACHABLE"
                    row.failure_message = "Announcement is no longer scheduled or active."
                    row.updated_at = now
            await session.flush()
            return

        recipients = await self.list_eligible_recipients(session, announcement)
        eligible_by_user_id = {recipient.user_id: recipient for recipient in recipients}

        for recipient in recipients:
            existing = existing_by_user_id.get(recipient.user_id)
            if existing is None:
                session.add(
                    PlatformMaintenanceEmailDelivery(
                        announcement_id=announcement.id,
                        tenant_id=recipient.tenant_id,
                        user_id=recipient.user_id,
                        user_role=recipient.role,
                        recipient_email=recipient.email,
                        recipient_full_name=recipient.full_name,
                        status=PlatformMaintenanceEmailDeliveryStatus.PENDING,
                        retry_count=0,
                        next_attempt_at=now,
                        created_at=now,
                        updated_at=now,
                    )
                )
                continue

            existing.tenant_id = recipient.tenant_id
            existing.user_role = recipient.role
            existing.recipient_email = recipient.email
            existing.recipient_full_name = recipient.full_name
            existing.updated_at = now
            if existing.status == PlatformMaintenanceEmailDeliveryStatus.CANCELLED:
                existing.status = PlatformMaintenanceEmailDeliveryStatus.PENDING
                existing.cancelled_at = None
                existing.next_attempt_at = now
                existing.failure_code = None
                existing.failure_message = None
            if existing.status == PlatformMaintenanceEmailDeliveryStatus.FAILED:
                existing.next_attempt_at = min(existing.next_attempt_at, now) if existing.next_attempt_at else now

        for row in existing_rows:
            if row.user_id in eligible_by_user_id:
                continue
            if _delivery_terminal(row.status):
                continue
            row.status = PlatformMaintenanceEmailDeliveryStatus.CANCELLED
            row.cancelled_at = now
            row.failure_code = "AUDIENCE_CHANGED"
            row.failure_message = "Recipient is no longer part of the targeted audience."
            row.updated_at = now

        await session.flush()

    async def sync_reachable_announcements(self, session: AsyncSession) -> int:
        announcements = await self.list_announcements_requiring_sync(session)
        for announcement in announcements:
            await self.sync_announcement(session, announcement)
        return len(announcements)

    async def _requeue_stale_processing_deliveries(self, session: AsyncSession, now: datetime) -> None:
        timeout_cutoff = now - timedelta(
            seconds=settings.PLATFORM_MAINTENANCE_EMAIL_PROCESSING_TIMEOUT_SECONDS
        )
        result = await session.execute(
            select(PlatformMaintenanceEmailDelivery).where(
                PlatformMaintenanceEmailDelivery.status == PlatformMaintenanceEmailDeliveryStatus.PROCESSING,
                PlatformMaintenanceEmailDelivery.processing_started_at <= timeout_cutoff,
            )
        )
        stale_rows = list(result.scalars().all())
        if not stale_rows:
            return

        for row in stale_rows:
            row.retry_count = int(row.retry_count or 0) + 1
            row.last_attempted_at = now
            row.updated_at = now
            row.processing_started_at = None
            row.failure_code = "PROCESSING_TIMEOUT"
            row.failure_message = "Delivery was reclaimed after exceeding the processing timeout."
            row.status = PlatformMaintenanceEmailDeliveryStatus.FAILED
            if row.retry_count >= settings.PLATFORM_MAINTENANCE_EMAIL_MAX_RETRIES:
                row.next_attempt_at = now
            else:
                backoff_seconds = min(
                    settings.PLATFORM_MAINTENANCE_EMAIL_BACKOFF_BASE_SECONDS
                    * (2 ** max(row.retry_count - 1, 0)),
                    settings.PLATFORM_MAINTENANCE_EMAIL_BACKOFF_MAX_SECONDS,
                )
                row.next_attempt_at = now + timedelta(seconds=backoff_seconds)
        await session.flush()

    async def claim_due_deliveries(
        self,
        session: AsyncSession,
        *,
        limit: int,
    ) -> list[PlatformMaintenanceEmailDelivery]:
        now = _utcnow()
        await self._requeue_stale_processing_deliveries(session, now)
        result = await session.execute(
            select(PlatformMaintenanceEmailDelivery)
            .options(selectinload(PlatformMaintenanceEmailDelivery.announcement))
            .where(
                PlatformMaintenanceEmailDelivery.status.in_([
                    PlatformMaintenanceEmailDeliveryStatus.PENDING,
                    PlatformMaintenanceEmailDeliveryStatus.FAILED,
                ]),
                PlatformMaintenanceEmailDelivery.retry_count < settings.PLATFORM_MAINTENANCE_EMAIL_MAX_RETRIES,
                PlatformMaintenanceEmailDelivery.next_attempt_at <= now,
            )
            .order_by(
                PlatformMaintenanceEmailDelivery.next_attempt_at.asc(),
                PlatformMaintenanceEmailDelivery.created_at.asc(),
            )
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        rows = list(result.scalars().all())
        for row in rows:
            row.status = PlatformMaintenanceEmailDeliveryStatus.PROCESSING
            row.processing_started_at = now
            row.last_attempted_at = now
            row.updated_at = now
        await session.flush()
        return rows

    async def send_delivery(
        self,
        session: AsyncSession,
        delivery: PlatformMaintenanceEmailDelivery,
    ) -> None:
        announcement = delivery.announcement
        if announcement is None:
            delivery.status = PlatformMaintenanceEmailDeliveryStatus.CANCELLED
            delivery.failure_code = "ANNOUNCEMENT_NOT_FOUND"
            delivery.failure_message = "Announcement was not available during delivery."
            delivery.cancelled_at = _utcnow()
            await session.flush()
            return

        if not _is_reachable_status(announcement.status):
            delivery.status = PlatformMaintenanceEmailDeliveryStatus.CANCELLED
            delivery.failure_code = "ANNOUNCEMENT_NOT_REACHABLE"
            delivery.failure_message = "Announcement is no longer scheduled or active."
            delivery.cancelled_at = _utcnow()
            await session.flush()
            return

        if _as_utc_datetime(announcement.ends_at) < _utcnow():
            delivery.status = PlatformMaintenanceEmailDeliveryStatus.CANCELLED
            delivery.failure_code = "ANNOUNCEMENT_WINDOW_ENDED"
            delivery.failure_message = "Announcement window has already ended."
            delivery.cancelled_at = _utcnow()
            await session.flush()
            return

        eligibility_result = await session.execute(
            select(User, Organization)
            .join(Organization, Organization.id == User.tenant_id)
            .where(User.id == delivery.user_id)
        )
        eligible_row = eligibility_result.first()
        if eligible_row is None:
            delivery.status = PlatformMaintenanceEmailDeliveryStatus.CANCELLED
            delivery.failure_code = "RECIPIENT_NOT_FOUND"
            delivery.failure_message = "Recipient is no longer available."
            delivery.cancelled_at = _utcnow()
            await session.flush()
            return
        user, organization = eligible_row
        if (
            not user.is_active
            or not organization.is_active
            or user.role not in {
                UserRole.ORG_ADMIN,
                UserRole.PLANT_MANAGER,
                UserRole.OPERATOR,
                UserRole.VIEWER,
            }
            or _normalize_email(user.email) != delivery.recipient_email
        ):
            delivery.status = PlatformMaintenanceEmailDeliveryStatus.CANCELLED
            delivery.failure_code = "RECIPIENT_NOT_ELIGIBLE"
            delivery.failure_message = "Recipient is no longer eligible for this maintenance email."
            delivery.cancelled_at = _utcnow()
            await session.flush()
            return

        if not announcement.broadcast_all_tenants:
            target_tenant_ids = {target.tenant_id for target in announcement.targets}
            if user.tenant_id not in target_tenant_ids:
                delivery.status = PlatformMaintenanceEmailDeliveryStatus.CANCELLED
                delivery.failure_code = "RECIPIENT_OUTSIDE_TARGET_AUDIENCE"
                delivery.failure_message = "Recipient is no longer within the targeted organisations."
                delivery.cancelled_at = _utcnow()
                await session.flush()
                return

        try:
            effective_status = compute_platform_maintenance_effective_status(announcement, now=_utcnow())
            await mailer_svc.send_platform_maintenance_email(
                recipient=delivery.recipient_email,
                full_name=delivery.recipient_full_name,
                title=announcement.title,
                severity=announcement.severity.value,
                message=announcement.message,
                starts_at=announcement.starts_at,
                estimated_duration_minutes=announcement.estimated_duration_minutes,
                status=effective_status.value,
            )
        except Exception as exc:
            delivery.retry_count = int(delivery.retry_count or 0) + 1
            delivery.failure_code = exc.__class__.__name__
            delivery.failure_message = str(exc)
            delivery.updated_at = _utcnow()
            if delivery.retry_count >= settings.PLATFORM_MAINTENANCE_EMAIL_MAX_RETRIES:
                delivery.status = PlatformMaintenanceEmailDeliveryStatus.FAILED
                delivery.next_attempt_at = delivery.updated_at
            else:
                delivery.status = PlatformMaintenanceEmailDeliveryStatus.FAILED
                backoff_seconds = min(
                    settings.PLATFORM_MAINTENANCE_EMAIL_BACKOFF_BASE_SECONDS * (2 ** max(delivery.retry_count - 1, 0)),
                    settings.PLATFORM_MAINTENANCE_EMAIL_BACKOFF_MAX_SECONDS,
                )
                delivery.next_attempt_at = delivery.updated_at + timedelta(seconds=backoff_seconds)
            await session.flush()
            return

        now = _utcnow()
        delivery.status = PlatformMaintenanceEmailDeliveryStatus.SENT
        delivery.sent_at = now
        delivery.failure_code = None
        delivery.failure_message = None
        delivery.updated_at = now
        await session.flush()


class PlatformMaintenanceDeliveryWorker:
    def __init__(self) -> None:
        self._service = PlatformMaintenanceDeliveryService()
        self._stopping = False
        self._sync_task: asyncio.Task | None = None
        self._delivery_task: asyncio.Task | None = None

    async def start(self) -> None:
        self._stopping = False
        self._sync_task = asyncio.create_task(self._sync_loop())
        self._delivery_task = asyncio.create_task(self._delivery_loop())
        await asyncio.gather(self._sync_task, self._delivery_task)

    async def stop(self) -> None:
        self._stopping = True
        tasks = [task for task in (self._sync_task, self._delivery_task) if task is not None]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _sync_loop(self) -> None:
        while not self._stopping:
            try:
                async with AsyncSessionFactory() as session:
                    await self._service.sync_reachable_announcements(session)
                    await session.commit()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("platform_maintenance_sync_loop_failed")
            await asyncio.sleep(settings.PLATFORM_MAINTENANCE_DELIVERY_SYNC_INTERVAL_SECONDS)

    async def _delivery_loop(self) -> None:
        while not self._stopping:
            processed = 0
            try:
                async with AsyncSessionFactory() as session:
                    deliveries = await self._service.claim_due_deliveries(
                        session,
                        limit=settings.PLATFORM_MAINTENANCE_DELIVERY_BATCH_SIZE,
                    )
                    await session.commit()
                for delivery in deliveries:
                    async with AsyncSessionFactory() as session:
                        delivery_result = await session.execute(
                            select(PlatformMaintenanceEmailDelivery)
                            .options(
                                selectinload(PlatformMaintenanceEmailDelivery.announcement).selectinload(
                                    PlatformMaintenanceAnnouncement.targets
                                )
                            )
                            .where(PlatformMaintenanceEmailDelivery.id == delivery.id)
                        )
                        attached = delivery_result.scalar_one_or_none()
                        if attached is None:
                            continue
                        await self._service.send_delivery(session, attached)
                        await session.commit()
                        processed += 1
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("platform_maintenance_delivery_loop_failed")
            if processed == 0:
                await asyncio.sleep(5)


platform_maintenance_delivery_worker = PlatformMaintenanceDeliveryWorker()
