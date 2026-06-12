"""Repository layer for permanent notification delivery audit logs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Optional

from sqlalchemy import and_, case, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rule import NotificationDeliveryLog, NotificationDeliveryStatus
from services.shared.scoped_repository import TenantScopedRepository
from services.shared.tenant_context import TenantContext


@dataclass(frozen=True)
class NotificationMonthlySummary:
    channel: str
    attempted_count: int
    accepted_count: int
    delivered_count: int
    failed_count: int
    skipped_count: int
    billable_count: int


@dataclass(frozen=True)
class NotificationTotalsSummary:
    attempted_count: int
    accepted_count: int
    delivered_count: int
    failed_count: int
    skipped_count: int
    billable_count: int
    first_attempt_at: Optional[datetime]
    last_attempt_at: Optional[datetime]


@dataclass(frozen=True)
class NotificationLogFilters:
    channel: Optional[str] = None
    status: Optional[str] = None
    rule_id: Optional[str] = None
    device_id: Optional[str] = None
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None
    search: Optional[str] = None


class NotificationDeliveryLogRepository(TenantScopedRepository[NotificationDeliveryLog]):
    """Repository for notification delivery audit rows."""

    model = NotificationDeliveryLog

    def __init__(self, session: AsyncSession, ctx: TenantContext):
        # Super-admin read APIs need cross-tenant reads with explicit tenant filters.
        super().__init__(session, ctx, allow_cross_tenant=ctx.is_super_admin)

    async def create_send_attempt(
        self,
        *,
        alert_id: Optional[str],
        rule_id: Optional[str],
        device_id: Optional[str],
        event_type: str,
        channel: str,
        recipient_masked: str,
        recipient_hash: str,
        provider_name: str,
        attempted_at: Optional[datetime] = None,
        metadata_json: Optional[dict[str, Any]] = None,
        status: NotificationDeliveryStatus = NotificationDeliveryStatus.ATTEMPTED,
        billable_units: int = 0,
        failure_code: Optional[str] = None,
        failure_message: Optional[str] = None,
    ) -> NotificationDeliveryLog:
        if not self._tenant_id:
            raise ValueError("notification delivery audit rows require a tenant scope for writes.")
        attempted_on = attempted_at or datetime.now(timezone.utc)
        normalized_status = self._normalize_status(status.value if isinstance(status, NotificationDeliveryStatus) else str(status))
        normalized_billable = self._normalize_billable_units(
            status=normalized_status,
            billable_units=billable_units,
        )
        row = NotificationDeliveryLog(
            tenant_id=self._tenant_id,
            alert_id=alert_id,
            rule_id=rule_id,
            device_id=device_id,
            event_type=event_type,
            channel=channel,
            recipient_masked=recipient_masked,
            recipient_hash=recipient_hash,
            provider_name=provider_name,
            status=normalized_status,
            billable_units=normalized_billable,
            attempted_at=attempted_on,
            accepted_at=attempted_on if normalized_status == NotificationDeliveryStatus.PROVIDER_ACCEPTED.value else None,
            delivered_at=attempted_on if normalized_status == NotificationDeliveryStatus.DELIVERED.value else None,
            failed_at=attempted_on if normalized_status == NotificationDeliveryStatus.FAILED.value else None,
            failure_code=failure_code,
            failure_message=failure_message,
            metadata_json=metadata_json,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def mark_provider_accepted(
        self,
        log_id: str,
        *,
        provider_message_id: Optional[str] = None,
        accepted_at: Optional[datetime] = None,
        billable_units: int = 1,
        metadata_json: Optional[dict[str, Any]] = None,
    ) -> Optional[NotificationDeliveryLog]:
        row = await self.get_by_id(log_id)
        if row is None:
            return None
        now = accepted_at or datetime.now(timezone.utc)
        return await self._transition_row_status(
            row=row,
            target_status=NotificationDeliveryStatus.PROVIDER_ACCEPTED.value,
            when=now,
            provider_message_id=provider_message_id,
            billable_units=billable_units,
            metadata_json=metadata_json,
        )

    async def mark_attempted(
        self,
        log_id: str,
        *,
        attempted_at: Optional[datetime] = None,
        metadata_json: Optional[dict[str, Any]] = None,
    ) -> Optional[NotificationDeliveryLog]:
        row = await self.get_by_id(log_id)
        if row is None:
            return None
        now = attempted_at or datetime.now(timezone.utc)
        row.attempted_at = now
        return await self._transition_row_status(
            row=row,
            target_status=NotificationDeliveryStatus.ATTEMPTED.value,
            when=now,
            billable_units=0,
            metadata_json=metadata_json,
        )

    async def mark_delivered(
        self,
        log_id: str,
        *,
        delivered_at: Optional[datetime] = None,
        provider_message_id: Optional[str] = None,
        metadata_json: Optional[dict[str, Any]] = None,
    ) -> Optional[NotificationDeliveryLog]:
        row = await self.get_by_id(log_id)
        if row is None:
            return None
        now = delivered_at or datetime.now(timezone.utc)
        return await self._transition_row_status(
            row=row,
            target_status=NotificationDeliveryStatus.DELIVERED.value,
            when=now,
            provider_message_id=provider_message_id,
            billable_units=1,
            metadata_json=metadata_json,
        )

    async def mark_failed(
        self,
        log_id: str,
        *,
        failure_code: Optional[str] = None,
        failure_message: Optional[str] = None,
        failed_at: Optional[datetime] = None,
        metadata_json: Optional[dict[str, Any]] = None,
    ) -> Optional[NotificationDeliveryLog]:
        row = await self.get_by_id(log_id)
        if row is None:
            return None
        now = failed_at or datetime.now(timezone.utc)
        return await self._transition_row_status(
            row=row,
            target_status=NotificationDeliveryStatus.FAILED.value,
            when=now,
            failure_code=failure_code,
            failure_message=failure_message,
            billable_units=0,
            metadata_json=metadata_json,
        )

    async def mark_skipped(
        self,
        log_id: str,
        *,
        failure_code: Optional[str] = None,
        failure_message: Optional[str] = None,
        metadata_json: Optional[dict[str, Any]] = None,
    ) -> Optional[NotificationDeliveryLog]:
        row = await self.get_by_id(log_id)
        if row is None:
            return None
        return await self._transition_row_status(
            row=row,
            target_status=NotificationDeliveryStatus.SKIPPED.value,
            when=row.attempted_at,
            failure_code=failure_code,
            failure_message=failure_message,
            billable_units=0,
            metadata_json=metadata_json,
        )

    async def mark_provider_accepted_by_message_id(
        self,
        *,
        tenant_id: str,
        provider_message_id: str,
        accepted_at: Optional[datetime] = None,
        provider_name: Optional[str] = None,
        channel: Optional[str] = None,
        metadata_json: Optional[dict[str, Any]] = None,
    ) -> Optional[NotificationDeliveryLog]:
        row = await self._find_by_provider_message_id(
            tenant_id=tenant_id,
            provider_message_id=provider_message_id,
            provider_name=provider_name,
            channel=channel,
        )
        if row is None:
            return None
        return await self._transition_row_status(
            row=row,
            target_status=NotificationDeliveryStatus.PROVIDER_ACCEPTED.value,
            when=accepted_at or datetime.now(timezone.utc),
            provider_message_id=provider_message_id,
            billable_units=1,
            metadata_json=metadata_json,
        )

    async def mark_delivered_by_message_id(
        self,
        *,
        tenant_id: str,
        provider_message_id: str,
        delivered_at: Optional[datetime] = None,
        provider_name: Optional[str] = None,
        channel: Optional[str] = None,
        metadata_json: Optional[dict[str, Any]] = None,
    ) -> Optional[NotificationDeliveryLog]:
        row = await self._find_by_provider_message_id(
            tenant_id=tenant_id,
            provider_message_id=provider_message_id,
            provider_name=provider_name,
            channel=channel,
        )
        if row is None:
            return None
        return await self._transition_row_status(
            row=row,
            target_status=NotificationDeliveryStatus.DELIVERED.value,
            when=delivered_at or datetime.now(timezone.utc),
            provider_message_id=provider_message_id,
            billable_units=1,
            metadata_json=metadata_json,
        )

    async def mark_failed_by_message_id(
        self,
        *,
        tenant_id: str,
        provider_message_id: str,
        failure_code: Optional[str] = None,
        failure_message: Optional[str] = None,
        failed_at: Optional[datetime] = None,
        provider_name: Optional[str] = None,
        channel: Optional[str] = None,
        metadata_json: Optional[dict[str, Any]] = None,
    ) -> Optional[NotificationDeliveryLog]:
        row = await self._find_by_provider_message_id(
            tenant_id=tenant_id,
            provider_message_id=provider_message_id,
            provider_name=provider_name,
            channel=channel,
        )
        if row is None:
            return None
        return await self._transition_row_status(
            row=row,
            target_status=NotificationDeliveryStatus.FAILED.value,
            when=failed_at or datetime.now(timezone.utc),
            failure_code=failure_code,
            failure_message=failure_message,
            billable_units=0,
            metadata_json=metadata_json,
        )

    async def summarize_month(
        self,
        *,
        tenant_id: str,
        month_start: datetime,
        month_end: datetime,
        filters: NotificationLogFilters | None = None,
    ) -> tuple[NotificationTotalsSummary, list[NotificationMonthlySummary]]:
        base_predicates = self._build_predicates(
            tenant_id=tenant_id,
            month_start=month_start,
            month_end=month_end,
            filters=filters or NotificationLogFilters(),
        )
        totals_stmt = select(
            func.sum(case((NotificationDeliveryLog.status != NotificationDeliveryStatus.QUEUED.value, 1), else_=0)).label(
                "attempted_count"
            ),
            func.sum(
                case(
                    (
                        NotificationDeliveryLog.status.in_(
                            [
                                NotificationDeliveryStatus.PROVIDER_ACCEPTED.value,
                                NotificationDeliveryStatus.DELIVERED.value,
                            ]
                        ),
                        1,
                    ),
                    else_=0,
                )
            ).label("accepted_count"),
            func.sum(case((NotificationDeliveryLog.status == NotificationDeliveryStatus.DELIVERED.value, 1), else_=0)).label(
                "delivered_count"
            ),
            func.sum(case((NotificationDeliveryLog.status == NotificationDeliveryStatus.FAILED.value, 1), else_=0)).label(
                "failed_count"
            ),
            func.sum(case((NotificationDeliveryLog.status == NotificationDeliveryStatus.SKIPPED.value, 1), else_=0)).label(
                "skipped_count"
            ),
            func.coalesce(func.sum(NotificationDeliveryLog.billable_units), 0).label("billable_count"),
            func.min(NotificationDeliveryLog.attempted_at).label("first_attempt_at"),
            func.max(NotificationDeliveryLog.attempted_at).label("last_attempt_at"),
        ).where(and_(*base_predicates))
        totals_row = (await self._session.execute(totals_stmt)).one()
        totals = NotificationTotalsSummary(
            attempted_count=int(totals_row.attempted_count or 0),
            accepted_count=int(totals_row.accepted_count or 0),
            delivered_count=int(totals_row.delivered_count or 0),
            failed_count=int(totals_row.failed_count or 0),
            skipped_count=int(totals_row.skipped_count or 0),
            billable_count=int(totals_row.billable_count or 0),
            first_attempt_at=totals_row.first_attempt_at,
            last_attempt_at=totals_row.last_attempt_at,
        )

        by_channel_stmt = (
            select(
                NotificationDeliveryLog.channel.label("channel"),
                func.sum(case((NotificationDeliveryLog.status != NotificationDeliveryStatus.QUEUED.value, 1), else_=0)).label(
                    "attempted_count"
                ),
                func.sum(
                    case(
                        (
                            NotificationDeliveryLog.status.in_(
                                [
                                    NotificationDeliveryStatus.PROVIDER_ACCEPTED.value,
                                    NotificationDeliveryStatus.DELIVERED.value,
                                ]
                            ),
                            1,
                        ),
                        else_=0,
                    )
                ).label("accepted_count"),
                func.sum(
                    case((NotificationDeliveryLog.status == NotificationDeliveryStatus.DELIVERED.value, 1), else_=0)
                ).label("delivered_count"),
                func.sum(case((NotificationDeliveryLog.status == NotificationDeliveryStatus.FAILED.value, 1), else_=0)).label(
                    "failed_count"
                ),
                func.sum(case((NotificationDeliveryLog.status == NotificationDeliveryStatus.SKIPPED.value, 1), else_=0)).label(
                    "skipped_count"
                ),
                func.coalesce(func.sum(NotificationDeliveryLog.billable_units), 0).label("billable_count"),
            )
            .where(and_(*base_predicates))
            .group_by(NotificationDeliveryLog.channel)
            .order_by(NotificationDeliveryLog.channel.asc())
        )
        channel_rows = (await self._session.execute(by_channel_stmt)).all()
        by_channel = [
            NotificationMonthlySummary(
                channel=row.channel,
                attempted_count=int(row.attempted_count or 0),
                accepted_count=int(row.accepted_count or 0),
                delivered_count=int(row.delivered_count or 0),
                failed_count=int(row.failed_count or 0),
                skipped_count=int(row.skipped_count or 0),
                billable_count=int(row.billable_count or 0),
            )
            for row in channel_rows
        ]
        return totals, by_channel

    async def list_month_logs(
        self,
        *,
        tenant_id: str,
        month_start: datetime,
        month_end: datetime,
        filters: NotificationLogFilters,
        page: int,
        page_size: int,
    ) -> tuple[list[NotificationDeliveryLog], int]:
        predicates = self._build_predicates(
            tenant_id=tenant_id,
            month_start=month_start,
            month_end=month_end,
            filters=filters,
        )
        base_stmt = select(NotificationDeliveryLog).where(and_(*predicates))
        count_stmt = select(func.count(NotificationDeliveryLog.id)).where(and_(*predicates))

        total = int((await self._session.execute(count_stmt)).scalar() or 0)
        offset = (page - 1) * page_size
        data_stmt = (
            base_stmt.order_by(NotificationDeliveryLog.attempted_at.asc(), NotificationDeliveryLog.id.asc())
            .offset(offset)
            .limit(page_size)
        )
        rows = list((await self._session.execute(data_stmt)).scalars().all())
        return rows, total

    async def iter_month_logs_for_export(
        self,
        *,
        tenant_id: str,
        month_start: datetime,
        month_end: datetime,
        filters: NotificationLogFilters,
    ) -> list[NotificationDeliveryLog]:
        predicates = self._build_predicates(
            tenant_id=tenant_id,
            month_start=month_start,
            month_end=month_end,
            filters=filters,
        )
        stmt = (
            select(NotificationDeliveryLog)
            .where(and_(*predicates))
            .order_by(NotificationDeliveryLog.attempted_at.asc(), NotificationDeliveryLog.id.asc())
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def stream_month_logs_for_export(
        self,
        *,
        tenant_id: str,
        month_start: datetime,
        month_end: datetime,
        filters: NotificationLogFilters,
        batch_size: int = 500,
    ) -> AsyncIterator[NotificationDeliveryLog]:
        predicates = self._build_predicates(
            tenant_id=tenant_id,
            month_start=month_start,
            month_end=month_end,
            filters=filters,
        )
        stmt = (
            select(NotificationDeliveryLog)
            .where(and_(*predicates))
            .order_by(NotificationDeliveryLog.attempted_at.asc(), NotificationDeliveryLog.id.asc())
            .execution_options(yield_per=max(batch_size, 1))
        )
        stream = await self._session.stream_scalars(stmt)
        async for row in stream:
            yield row

    async def delete_attempts_older_than(self, *, cutoff: datetime) -> int:
        stmt = delete(NotificationDeliveryLog).where(NotificationDeliveryLog.attempted_at < cutoff)
        if self._tenant_id is not None:
            stmt = stmt.where(NotificationDeliveryLog.tenant_id == self._tenant_id)
        result = await self._session.execute(stmt.execution_options(synchronize_session=False))
        await self._session.flush()
        return int(result.rowcount or 0)

    async def _find_by_provider_message_id(
        self,
        *,
        tenant_id: str,
        provider_message_id: str,
        provider_name: Optional[str] = None,
        channel: Optional[str] = None,
    ) -> Optional[NotificationDeliveryLog]:
        message_id = (provider_message_id or "").strip()
        if not message_id:
            return None
        stmt = select(NotificationDeliveryLog).where(
            NotificationDeliveryLog.tenant_id == tenant_id,
            NotificationDeliveryLog.provider_message_id == message_id,
        )
        if provider_name:
            stmt = stmt.where(NotificationDeliveryLog.provider_name == provider_name)
        if channel:
            stmt = stmt.where(NotificationDeliveryLog.channel == channel)
        stmt = stmt.order_by(NotificationDeliveryLog.attempted_at.desc(), NotificationDeliveryLog.id.desc()).limit(1)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def _transition_row_status(
        self,
        *,
        row: NotificationDeliveryLog,
        target_status: str,
        when: datetime,
        provider_message_id: Optional[str] = None,
        failure_code: Optional[str] = None,
        failure_message: Optional[str] = None,
        billable_units: Optional[int] = None,
        metadata_json: Optional[dict[str, Any]] = None,
    ) -> NotificationDeliveryLog:
        current_status = self._normalize_status(row.status)
        normalized_target = self._normalize_status(target_status)
        if not self._can_transition(current=current_status, target=normalized_target):
            await self._session.refresh(row)
            return row

        row.status = normalized_target
        if provider_message_id is not None:
            row.provider_message_id = provider_message_id
        if metadata_json is not None:
            row.metadata_json = metadata_json

        if normalized_target == NotificationDeliveryStatus.PROVIDER_ACCEPTED.value:
            row.accepted_at = row.accepted_at or when
            row.failed_at = None
            row.failure_code = None
            row.failure_message = None
        elif normalized_target == NotificationDeliveryStatus.DELIVERED.value:
            row.accepted_at = row.accepted_at or when
            row.delivered_at = row.delivered_at or when
            row.failed_at = None
            row.failure_code = None
            row.failure_message = None
        elif normalized_target == NotificationDeliveryStatus.FAILED.value:
            row.failed_at = row.failed_at or when
            row.failure_code = failure_code
            row.failure_message = failure_message
            row.delivered_at = None
        elif normalized_target == NotificationDeliveryStatus.SKIPPED.value:
            row.failure_code = failure_code
            row.failure_message = failure_message
            row.accepted_at = None
            row.delivered_at = None
            row.failed_at = row.failed_at

        row.billable_units = self._normalize_billable_units(
            status=normalized_target,
            billable_units=row.billable_units if billable_units is None else billable_units,
        )
        await self._session.flush()
        await self._session.refresh(row)
        return row

    @staticmethod
    def _normalize_status(value: str) -> str:
        status = (value or "").strip().lower()
        allowed = {item.value for item in NotificationDeliveryStatus}
        if status not in allowed:
            raise ValueError(f"Unsupported notification delivery status: {value}")
        return status

    @staticmethod
    def _normalize_billable_units(*, status: str, billable_units: int) -> int:
        normalized_status = NotificationDeliveryLogRepository._normalize_status(status)
        parsed_units = max(int(billable_units), 0)
        if normalized_status in {
            NotificationDeliveryStatus.FAILED.value,
            NotificationDeliveryStatus.SKIPPED.value,
            NotificationDeliveryStatus.ATTEMPTED.value,
            NotificationDeliveryStatus.QUEUED.value,
        }:
            return 0
        if normalized_status in {
            NotificationDeliveryStatus.PROVIDER_ACCEPTED.value,
            NotificationDeliveryStatus.DELIVERED.value,
        }:
            return 1
        return parsed_units

    @staticmethod
    def _can_transition(*, current: str, target: str) -> bool:
        if current == target:
            return True
        allowed_transitions: dict[str, set[str]] = {
            NotificationDeliveryStatus.QUEUED.value: {
                NotificationDeliveryStatus.ATTEMPTED.value,
                NotificationDeliveryStatus.SKIPPED.value,
            },
            NotificationDeliveryStatus.ATTEMPTED.value: {
                NotificationDeliveryStatus.PROVIDER_ACCEPTED.value,
                NotificationDeliveryStatus.DELIVERED.value,
                NotificationDeliveryStatus.FAILED.value,
                NotificationDeliveryStatus.SKIPPED.value,
            },
            NotificationDeliveryStatus.PROVIDER_ACCEPTED.value: {
                NotificationDeliveryStatus.DELIVERED.value,
                NotificationDeliveryStatus.FAILED.value,
            },
            NotificationDeliveryStatus.DELIVERED.value: set(),
            NotificationDeliveryStatus.FAILED.value: set(),
            NotificationDeliveryStatus.SKIPPED.value: set(),
        }
        return target in allowed_transitions.get(current, set())

    @staticmethod
    def _build_predicates(
        *,
        tenant_id: str,
        month_start: datetime,
        month_end: datetime,
        filters: NotificationLogFilters,
    ) -> list[Any]:
        predicates: list[Any] = [
            NotificationDeliveryLog.tenant_id == tenant_id,
            NotificationDeliveryLog.attempted_at >= month_start,
            NotificationDeliveryLog.attempted_at < month_end,
        ]
        if filters.channel:
            predicates.append(NotificationDeliveryLog.channel == filters.channel)
        if filters.status:
            predicates.append(NotificationDeliveryLog.status == filters.status)
        if filters.rule_id:
            predicates.append(NotificationDeliveryLog.rule_id == filters.rule_id)
        if filters.device_id:
            predicates.append(NotificationDeliveryLog.device_id == filters.device_id)
        if filters.date_from:
            predicates.append(NotificationDeliveryLog.attempted_at >= filters.date_from)
        if filters.date_to:
            predicates.append(NotificationDeliveryLog.attempted_at <= filters.date_to)
        if filters.search:
            term = f"%{filters.search.strip()}%"
            predicates.append(
                or_(
                    NotificationDeliveryLog.recipient_masked.ilike(term),
                    NotificationDeliveryLog.provider_message_id.ilike(term),
                )
            )
        return predicates
