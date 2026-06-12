"""Service layer for permanent notification delivery audit logging."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from io import StringIO
from typing import Any, AsyncIterator, Optional

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.rule import NotificationDeliveryLog, NotificationDeliveryStatus
from app.repositories.notification_delivery import (
    NotificationDeliveryLogRepository,
    NotificationLogFilters,
    NotificationMonthlySummary,
    NotificationTotalsSummary,
)
from app.utils.notification_delivery import hash_recipient, mask_recipient
from services.shared.tenant_context import TenantContext


@dataclass(frozen=True)
class NotificationAuditContext:
    channel: str
    raw_recipient: str
    provider_name: str
    event_type: str
    rule_id: Optional[str]
    alert_id: Optional[str]
    device_id: Optional[str]
    metadata_json: Optional[dict[str, Any]] = None


@dataclass(frozen=True)
class NotificationUsageFilters:
    channel: Optional[str] = None
    status: Optional[str] = None
    rule_id: Optional[str] = None
    device_id: Optional[str] = None
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None
    search: Optional[str] = None


@dataclass(frozen=True)
class NotificationUsageSummary:
    totals: NotificationTotalsSummary
    by_channel: list[NotificationMonthlySummary]


@dataclass(frozen=True)
class NotificationUsageLogPage:
    rows: list[NotificationDeliveryLog]
    page: int
    page_size: int
    total: int


class NotificationDeliveryAuditService:
    """Encapsulates billing-safe audit persistence and read contracts."""

    def __init__(self, session: AsyncSession, ctx: TenantContext):
        self._session = session
        self._ctx = ctx
        self._repository = NotificationDeliveryLogRepository(session, ctx)

    async def create_send_attempt(
        self,
        *,
        channel: str,
        raw_recipient: str,
        provider_name: str,
        event_type: str,
        rule_id: Optional[str] = None,
        alert_id: Optional[str] = None,
        device_id: Optional[str] = None,
        attempted_at: Optional[datetime] = None,
        metadata_json: Optional[dict[str, Any]] = None,
    ) -> NotificationDeliveryLog:
        return await self._repository.create_send_attempt(
            alert_id=alert_id,
            rule_id=rule_id,
            device_id=device_id,
            event_type=event_type,
            channel=channel,
            recipient_masked=mask_recipient(channel, raw_recipient),
            recipient_hash=hash_recipient(raw_recipient),
            provider_name=provider_name,
            attempted_at=attempted_at,
            metadata_json=metadata_json,
            status=NotificationDeliveryStatus.ATTEMPTED,
            billable_units=0,
        )

    async def create_queued_intent(
        self,
        *,
        channel: str,
        raw_recipient: str,
        provider_name: str,
        event_type: str,
        rule_id: Optional[str] = None,
        alert_id: Optional[str] = None,
        device_id: Optional[str] = None,
        attempted_at: Optional[datetime] = None,
        metadata_json: Optional[dict[str, Any]] = None,
    ) -> NotificationDeliveryLog:
        return await self._repository.create_send_attempt(
            alert_id=alert_id,
            rule_id=rule_id,
            device_id=device_id,
            event_type=event_type,
            channel=channel,
            recipient_masked=mask_recipient(channel, raw_recipient),
            recipient_hash=hash_recipient(raw_recipient),
            provider_name=provider_name,
            attempted_at=attempted_at,
            metadata_json=metadata_json,
            status=NotificationDeliveryStatus.QUEUED,
            billable_units=0,
        )

    async def mark_attempted(
        self,
        log_id: str,
        *,
        attempted_at: Optional[datetime] = None,
        metadata_json: Optional[dict[str, Any]] = None,
    ) -> Optional[NotificationDeliveryLog]:
        return await self._repository.mark_attempted(
            log_id,
            attempted_at=attempted_at,
            metadata_json=metadata_json,
        )

    async def mark_provider_accepted(
        self,
        log_id: str,
        *,
        provider_message_id: Optional[str] = None,
        accepted_at: Optional[datetime] = None,
        metadata_json: Optional[dict[str, Any]] = None,
    ) -> Optional[NotificationDeliveryLog]:
        return await self._repository.mark_provider_accepted(
            log_id,
            provider_message_id=provider_message_id,
            accepted_at=accepted_at,
            billable_units=1,
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
        return await self._repository.mark_provider_accepted_by_message_id(
            tenant_id=tenant_id,
            provider_message_id=provider_message_id,
            accepted_at=accepted_at,
            provider_name=provider_name,
            channel=channel,
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
        return await self._repository.mark_delivered(
            log_id,
            delivered_at=delivered_at,
            provider_message_id=provider_message_id,
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
        return await self._repository.mark_delivered_by_message_id(
            tenant_id=tenant_id,
            provider_message_id=provider_message_id,
            delivered_at=delivered_at,
            provider_name=provider_name,
            channel=channel,
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
        return await self._repository.mark_failed(
            log_id,
            failure_code=failure_code,
            failure_message=failure_message,
            failed_at=failed_at,
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
        return await self._repository.mark_failed_by_message_id(
            tenant_id=tenant_id,
            provider_message_id=provider_message_id,
            failure_code=failure_code,
            failure_message=failure_message,
            failed_at=failed_at,
            provider_name=provider_name,
            channel=channel,
            metadata_json=metadata_json,
        )

    async def mark_skipped(
        self,
        *,
        channel: str,
        raw_recipient: str,
        provider_name: str,
        event_type: str,
        rule_id: Optional[str] = None,
        alert_id: Optional[str] = None,
        device_id: Optional[str] = None,
        failure_code: Optional[str] = None,
        failure_message: Optional[str] = None,
        metadata_json: Optional[dict[str, Any]] = None,
    ) -> NotificationDeliveryLog:
        attempted_at = datetime.now(timezone.utc)
        return await self._repository.create_send_attempt(
            alert_id=alert_id,
            rule_id=rule_id,
            device_id=device_id,
            event_type=event_type,
            channel=channel,
            recipient_masked=mask_recipient(channel, raw_recipient),
            recipient_hash=hash_recipient(raw_recipient),
            provider_name=provider_name,
            attempted_at=attempted_at,
            metadata_json=metadata_json,
            status=NotificationDeliveryStatus.SKIPPED,
            billable_units=0,
            failure_code=failure_code,
            failure_message=failure_message,
        )

    async def mark_skipped_log(
        self,
        log_id: str,
        *,
        failure_code: Optional[str] = None,
        failure_message: Optional[str] = None,
        metadata_json: Optional[dict[str, Any]] = None,
    ) -> Optional[NotificationDeliveryLog]:
        return await self._repository.mark_skipped(
            log_id,
            failure_code=failure_code,
            failure_message=failure_message,
            metadata_json=metadata_json,
        )

    async def summarize_month(
        self,
        *,
        tenant_id: str,
        month: str,
        filters: NotificationUsageFilters | None = None,
    ) -> NotificationUsageSummary:
        month_start, month_end = self._month_bounds(month)
        totals, by_channel = await self._repository.summarize_month(
            tenant_id=tenant_id,
            month_start=month_start,
            month_end=month_end,
            filters=self._to_repo_filters(filters),
        )
        return NotificationUsageSummary(totals=totals, by_channel=by_channel)

    async def list_month_logs(
        self,
        *,
        tenant_id: str,
        month: str,
        filters: NotificationUsageFilters | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> NotificationUsageLogPage:
        month_start, month_end = self._month_bounds(month)
        row_filters = self._to_repo_filters(filters)
        self._validate_date_bounds_within_month(
            month_start=month_start,
            month_end=month_end,
            date_from=row_filters.date_from,
            date_to=row_filters.date_to,
        )
        rows, total = await self._repository.list_month_logs(
            tenant_id=tenant_id,
            month_start=month_start,
            month_end=month_end,
            filters=row_filters,
            page=page,
            page_size=page_size,
        )
        return NotificationUsageLogPage(rows=rows, page=page, page_size=page_size, total=total)

    async def export_month_logs_csv(
        self,
        *,
        tenant_id: str,
        month: str,
        filters: NotificationUsageFilters | None = None,
    ) -> str:
        month_start, month_end = self._month_bounds(month)
        row_filters = self._to_repo_filters(filters)
        self._validate_date_bounds_within_month(
            month_start=month_start,
            month_end=month_end,
            date_from=row_filters.date_from,
            date_to=row_filters.date_to,
        )
        rows = await self._repository.iter_month_logs_for_export(
            tenant_id=tenant_id,
            month_start=month_start,
            month_end=month_end,
            filters=row_filters,
        )
        output = StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "Attempted At",
                "Channel",
                "Status",
                "Billable Units",
                "Recipient",
                "Provider",
                "Provider Message Id",
                "Rule Id",
                "Device Id",
                "Event Type",
                "Accepted At",
                "Delivered At",
                "Failed At",
                "Failure Code",
                "Failure Message",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    self._fmt_dt(row.attempted_at),
                    row.channel,
                    row.status,
                    row.billable_units,
                    row.recipient_masked,
                    row.provider_name,
                    row.provider_message_id or "",
                    row.rule_id or "",
                    row.device_id or "",
                    row.event_type,
                    self._fmt_dt(row.accepted_at),
                    self._fmt_dt(row.delivered_at),
                    self._fmt_dt(row.failed_at),
                    row.failure_code or "",
                    row.failure_message or "",
                ]
            )
        return output.getvalue()

    async def stream_month_logs_csv(
        self,
        *,
        tenant_id: str,
        month: str,
        filters: NotificationUsageFilters | None = None,
    ) -> AsyncIterator[str]:
        month_start, month_end = self._month_bounds(month)
        row_filters = self._to_repo_filters(filters)
        self._validate_date_bounds_within_month(
            month_start=month_start,
            month_end=month_end,
            date_from=row_filters.date_from,
            date_to=row_filters.date_to,
        )

        header_buf = StringIO()
        header_writer = csv.writer(header_buf)
        header_writer.writerow(
            [
                "Attempted At",
                "Channel",
                "Status",
                "Billable Units",
                "Recipient",
                "Provider",
                "Provider Message Id",
                "Rule Id",
                "Device Id",
                "Event Type",
                "Accepted At",
                "Delivered At",
                "Failed At",
                "Failure Code",
                "Failure Message",
            ]
        )
        yield header_buf.getvalue()

        async for row in self._repository.stream_month_logs_for_export(
            tenant_id=tenant_id,
            month_start=month_start,
            month_end=month_end,
            filters=row_filters,
            batch_size=max(int(settings.NOTIFICATION_USAGE_EXPORT_STREAM_BATCH_SIZE), 1),
        ):
            row_buf = StringIO()
            row_writer = csv.writer(row_buf)
            row_writer.writerow(
                [
                    self._fmt_dt(row.attempted_at),
                    row.channel,
                    row.status,
                    row.billable_units,
                    row.recipient_masked,
                    row.provider_name,
                    row.provider_message_id or "",
                    row.rule_id or "",
                    row.device_id or "",
                    row.event_type,
                    self._fmt_dt(row.accepted_at),
                    self._fmt_dt(row.delivered_at),
                    self._fmt_dt(row.failed_at),
                    row.failure_code or "",
                    row.failure_message or "",
                ]
            )
            yield row_buf.getvalue()

    async def apply_retention_policy(self, *, now: Optional[datetime] = None) -> int:
        if not settings.NOTIFICATION_DELIVERY_RETENTION_ENABLED:
            return 0
        retention_months = max(int(settings.NOTIFICATION_DELIVERY_RETENTION_MONTHS), 12)
        reference = now or datetime.now(timezone.utc)
        cutoff = self._subtract_months(datetime(reference.year, reference.month, 1, tzinfo=timezone.utc), retention_months)
        return await self._repository.delete_attempts_older_than(cutoff=cutoff)

    async def list_for_month(
        self,
        *,
        year: int,
        month: int,
        channel: Optional[str] = None,
        status: Optional[str] = None,
    ) -> list[NotificationDeliveryLog]:
        month_text = f"{year:04d}-{month:02d}"
        page = await self.list_month_logs(
            tenant_id=self._ctx.require_tenant(),
            month=month_text,
            filters=NotificationUsageFilters(channel=channel, status=status),
            page=1,
            page_size=10000,
        )
        return page.rows

    async def summarize_for_month(
        self,
        *,
        year: int,
        month: int,
    ) -> list[NotificationMonthlySummary]:
        month_text = f"{year:04d}-{month:02d}"
        summary = await self.summarize_month(
            tenant_id=self._ctx.require_tenant(),
            month=month_text,
            filters=None,
        )
        return summary.by_channel

    @staticmethod
    def _fmt_dt(value: Optional[datetime]) -> str:
        if value is None:
            return ""
        return value.astimezone(timezone.utc).isoformat()

    @staticmethod
    def _month_bounds(month: str) -> tuple[datetime, datetime]:
        if re.fullmatch(r"\d{4}-\d{2}", month or "") is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "INVALID_MONTH",
                    "message": "month must be in YYYY-MM format.",
                },
            )
        try:
            dt = datetime.strptime(month, "%Y-%m")
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "INVALID_MONTH",
                    "message": "month must be in YYYY-MM format.",
                },
            ) from exc
        month_start = datetime(dt.year, dt.month, 1, tzinfo=timezone.utc)
        if dt.month == 12:
            month_end = datetime(dt.year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            month_end = datetime(dt.year, dt.month + 1, 1, tzinfo=timezone.utc)
        return month_start, month_end

    @staticmethod
    def _subtract_months(value: datetime, months: int) -> datetime:
        year = value.year
        month = value.month
        remaining = max(int(months), 0)
        while remaining > 0:
            month -= 1
            if month == 0:
                month = 12
                year -= 1
            remaining -= 1
        return datetime(year, month, 1, tzinfo=timezone.utc)

    @staticmethod
    def _validate_date_bounds_within_month(
        *,
        month_start: datetime,
        month_end: datetime,
        date_from: Optional[datetime],
        date_to: Optional[datetime],
    ) -> None:
        if date_from and not (month_start <= date_from < month_end):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "DATE_RANGE_OUTSIDE_MONTH",
                    "message": "date_from must fall within the selected month.",
                },
            )
        if date_to and not (month_start <= date_to < month_end):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "DATE_RANGE_OUTSIDE_MONTH",
                    "message": "date_to must fall within the selected month.",
                },
            )
        if date_from and date_to and date_from > date_to:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "INVALID_DATE_RANGE",
                    "message": "date_from must be <= date_to.",
                },
            )

    @staticmethod
    def _to_repo_filters(filters: NotificationUsageFilters | None) -> NotificationLogFilters:
        if filters is None:
            return NotificationLogFilters()
        return NotificationLogFilters(
            channel=filters.channel,
            status=filters.status,
            rule_id=filters.rule_id,
            device_id=filters.device_id,
            date_from=filters.date_from,
            date_to=filters.date_to,
            search=filters.search,
        )
