"""Repository for durable notification outbox rows."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rule import NotificationDeliveryStatus, NotificationOutbox
from services.shared.scoped_repository import TenantScopedRepository
from services.shared.tenant_context import TenantContext

_PENDING_STATUSES = frozenset({
    NotificationDeliveryStatus.QUEUED.value,
    NotificationDeliveryStatus.ATTEMPTED.value,
})


class NotificationOutboxRepository(TenantScopedRepository[NotificationOutbox]):
    model = NotificationOutbox

    def __init__(self, session: AsyncSession, ctx: TenantContext | None = None):
        effective_ctx = ctx or TenantContext.system("svc:rule-engine-service")
        super().__init__(session, effective_ctx, allow_cross_tenant=ctx is None or effective_ctx.is_super_admin)
        self._session = session

    async def create_outbox_entry(self, row: NotificationOutbox) -> NotificationOutbox:
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def count_pending_for_tenant(self, tenant_id: str) -> int:
        stmt = (
            select(func.count())
            .select_from(NotificationOutbox)
            .where(NotificationOutbox.tenant_id == tenant_id)
            .where(NotificationOutbox.status.in_(_PENDING_STATUSES))
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def count_pending_global(self) -> int:
        stmt = (
            select(func.count())
            .select_from(NotificationOutbox)
            .where(NotificationOutbox.status.in_(_PENDING_STATUSES))
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def claim_outbox_entry(
        self,
        *,
        outbox_id: str,
        worker_id: str,
        stale_after: timedelta,
        now: datetime | None = None,
    ) -> bool:
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is not None:
            now = now.replace(tzinfo=None)
        stale_cutoff = now - stale_after
        stmt = (
            update(NotificationOutbox)
            .where(NotificationOutbox.id == outbox_id)
            .where(NotificationOutbox.next_attempt_at <= now)
            .where(
                or_(
                    NotificationOutbox.status == NotificationDeliveryStatus.QUEUED.value,
                    (
                        (NotificationOutbox.status == NotificationDeliveryStatus.ATTEMPTED.value)
                        & (
                            NotificationOutbox.processing_started_at.is_(None)
                            | (NotificationOutbox.processing_started_at < stale_cutoff)
                        )
                    ),
                )
            )
            .values(
                status=NotificationDeliveryStatus.ATTEMPTED.value,
                worker_id=worker_id,
                processing_started_at=now,
                last_attempt_at=now,
            )
        )
        result = await self._session.execute(stmt)
        await self._session.commit()
        return bool(result.rowcount)

    async def get_by_outbox_id(self, outbox_id: str) -> Optional[NotificationOutbox]:
        result = await self._session.execute(select(NotificationOutbox).where(NotificationOutbox.id == outbox_id))
        return result.scalar_one_or_none()

    async def requeue(
        self,
        *,
        outbox_id: str,
        next_attempt_at: datetime,
        failure_code: str,
        failure_message: str,
    ) -> bool:
        stmt = (
            update(NotificationOutbox)
            .where(NotificationOutbox.id == outbox_id)
            .values(
                status=NotificationDeliveryStatus.QUEUED.value,
                retry_count=NotificationOutbox.retry_count + 1,
                next_attempt_at=next_attempt_at,
                worker_id=None,
                processing_started_at=None,
                failure_code=failure_code,
                failure_message=failure_message,
            )
        )
        result = await self._session.execute(stmt)
        await self._session.commit()
        return bool(result.rowcount)

    async def mark_terminal(
        self,
        *,
        outbox_id: str,
        status: str,
        provider_message_id: str | None = None,
        failure_code: str | None = None,
        failure_message: str | None = None,
        dead_lettered: bool = False,
        when: datetime | None = None,
    ) -> bool:
        when = when or datetime.now(timezone.utc)
        values = {
            "status": status,
            "worker_id": None,
            "processing_started_at": None,
            "provider_message_id": provider_message_id,
            "failure_code": failure_code,
            "failure_message": failure_message,
        }
        if status == NotificationDeliveryStatus.PROVIDER_ACCEPTED.value:
            values["accepted_at"] = when
        elif status == NotificationDeliveryStatus.DELIVERED.value:
            values["accepted_at"] = when
            values["delivered_at"] = when
        elif status == NotificationDeliveryStatus.FAILED.value:
            values["failed_at"] = when
        elif status == NotificationDeliveryStatus.SKIPPED.value:
            values["failed_at"] = when
        if dead_lettered:
            values["dead_lettered_at"] = when
        stmt = update(NotificationOutbox).where(NotificationOutbox.id == outbox_id).values(**values)
        result = await self._session.execute(stmt)
        await self._session.commit()
        return bool(result.rowcount)

    async def list_due_queued(self, *, limit: int, now: datetime | None = None) -> list[NotificationOutbox]:
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is not None:
            now = now.replace(tzinfo=None)
        fetch_limit = max(limit * 3, limit)
        stmt = (
            select(NotificationOutbox)
            .where(NotificationOutbox.status == NotificationDeliveryStatus.QUEUED.value)
            .where(NotificationOutbox.next_attempt_at <= now)
            .order_by(NotificationOutbox.next_attempt_at.asc(), NotificationOutbox.created_at.asc())
            .limit(fetch_limit)
        )
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())
        if not rows:
            return []
        by_tenant: dict[str, list[NotificationOutbox]] = {}
        for row in rows:
            by_tenant.setdefault(row.tenant_id, []).append(row)
        tenant_queues = list(by_tenant.values())
        max_depth = max(len(q) for q in tenant_queues)
        interleaved: list[NotificationOutbox] = []
        for depth in range(max_depth):
            for q in tenant_queues:
                if depth < len(q):
                    interleaved.append(q[depth])
                    if len(interleaved) >= limit:
                        return interleaved[:limit]
        return interleaved[:limit]

    async def recover_stale_attempted(self, *, stale_after: timedelta, now: datetime | None = None) -> int:
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is not None:
            now = now.replace(tzinfo=None)
        stale_cutoff = now - stale_after
        stmt = (
            update(NotificationOutbox)
            .where(NotificationOutbox.status == NotificationDeliveryStatus.ATTEMPTED.value)
            .where(
                NotificationOutbox.processing_started_at.is_(None)
                | (NotificationOutbox.processing_started_at < stale_cutoff)
            )
            .values(
                status=NotificationDeliveryStatus.QUEUED.value,
                worker_id=None,
                processing_started_at=None,
                failure_code="STALE_ATTEMPT_RECOVERED",
                failure_message="Requeued after stale ATTEMPTED state recovered on startup",
                next_attempt_at=now,
            )
        )
        result = await self._session.execute(stmt)
        await self._session.commit()
        return int(result.rowcount)

    async def count_by_status_and_channel(self) -> dict[str, dict[str, int]]:
        stmt = (
            select(NotificationOutbox.channel, NotificationOutbox.status, func.count())
            .group_by(NotificationOutbox.channel, NotificationOutbox.status)
        )
        result = await self._session.execute(stmt)
        counts: dict[str, dict[str, int]] = {}
        for channel, status, count in result.all():
            channel_counts = counts.setdefault(str(channel), {})
            channel_counts[str(status)] = int(count)
        return counts

    async def aggregate_runtime_counters(self) -> dict[str, int]:
        stmt = select(func.coalesce(func.sum(NotificationOutbox.retry_count), 0))
        result = await self._session.execute(stmt)
        return {"retry_count": int(result.scalar_one() or 0)}
