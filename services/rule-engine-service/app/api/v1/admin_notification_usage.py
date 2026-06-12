"""Super-admin notification usage APIs for billing proofs."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.params import Param
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.notification_delivery import (
    NotificationDeliveryAuditService,
    NotificationUsageFilters,
)
from services.shared.tenant_context import TenantContext

router = APIRouter()


class NotificationUsageChannelSummary(BaseModel):
    attempted_count: int = 0
    accepted_count: int = 0
    delivered_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    billable_count: int = 0


class NotificationUsageSummaryResponse(BaseModel):
    success: bool = True
    tenant_id: str
    month: str
    totals: NotificationUsageChannelSummary
    by_channel: dict[str, NotificationUsageChannelSummary]
    first_attempt_at: Optional[datetime] = None
    last_attempt_at: Optional[datetime] = None


class NotificationUsageLogRow(BaseModel):
    id: str
    tenant_id: Optional[str]
    attempted_at: datetime
    channel: str
    status: str
    event_type: str
    recipient_masked: str
    provider_name: str
    provider_message_id: Optional[str] = None
    rule_id: Optional[str] = None
    device_id: Optional[str] = None
    billable_units: int
    failure_code: Optional[str] = None
    failure_message: Optional[str] = None
    accepted_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None
    failed_at: Optional[datetime] = None
    metadata_json: Optional[dict[str, Any]] = None


class NotificationUsageLogsResponse(BaseModel):
    success: bool = True
    tenant_id: str
    month: str
    page: int
    page_size: int
    total: int
    data: list[NotificationUsageLogRow]


def _super_admin_context(request: Request) -> TenantContext:
    ctx = TenantContext.from_request(request)
    if not ctx.is_super_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "SUPER_ADMIN_REQUIRED",
                "message": "Only super admin can access notification usage APIs.",
            },
        )
    return ctx


def _build_filters(
    *,
    channel: Optional[str],
    status_value: Optional[str],
    rule_id: Optional[str],
    device_id: Optional[str],
    date_from: Optional[datetime],
    date_to: Optional[datetime],
    search: Optional[str],
) -> NotificationUsageFilters:
    def _resolve(value: Any) -> Any:
        if isinstance(value, Param):
            return value.default
        return value

    return NotificationUsageFilters(
        channel=_resolve(channel),
        status=_resolve(status_value),
        rule_id=_resolve(rule_id),
        device_id=_resolve(device_id),
        date_from=_resolve(date_from),
        date_to=_resolve(date_to),
        search=_resolve(search),
    )


@router.get("/{tenant_id}/summary", response_model=NotificationUsageSummaryResponse)
async def get_notification_usage_summary(
    tenant_id: str,
    request: Request,
    month: str = Query(..., description="Month in YYYY-MM format"),
    channel: Optional[str] = Query(None),
    status_value: Optional[str] = Query(None, alias="status"),
    rule_id: Optional[str] = Query(None),
    device_id: Optional[str] = Query(None),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    search: Optional[str] = Query(None, description="Match masked recipient or provider message id"),
    db: AsyncSession = Depends(get_db),
) -> NotificationUsageSummaryResponse:
    ctx = _super_admin_context(request)
    service = NotificationDeliveryAuditService(db, ctx)
    month_value = month.default if isinstance(month, Param) else month
    summary = await service.summarize_month(
        tenant_id=tenant_id,
        month=month_value,
        filters=_build_filters(
            channel=channel,
            status_value=status_value,
            rule_id=rule_id,
            device_id=device_id,
            date_from=date_from,
            date_to=date_to,
            search=search,
        ),
    )
    by_channel = {
        "email": NotificationUsageChannelSummary(),
        "sms": NotificationUsageChannelSummary(),
        "whatsapp": NotificationUsageChannelSummary(),
    }
    for row in summary.by_channel:
        by_channel[row.channel] = NotificationUsageChannelSummary(
            attempted_count=row.attempted_count,
            accepted_count=row.accepted_count,
            delivered_count=row.delivered_count,
            failed_count=row.failed_count,
            skipped_count=row.skipped_count,
            billable_count=row.billable_count,
        )
    return NotificationUsageSummaryResponse(
        tenant_id=tenant_id,
        month=month,
        totals=NotificationUsageChannelSummary(
            attempted_count=summary.totals.attempted_count,
            accepted_count=summary.totals.accepted_count,
            delivered_count=summary.totals.delivered_count,
            failed_count=summary.totals.failed_count,
            skipped_count=summary.totals.skipped_count,
            billable_count=summary.totals.billable_count,
        ),
        by_channel=by_channel,
        first_attempt_at=summary.totals.first_attempt_at,
        last_attempt_at=summary.totals.last_attempt_at,
    )


@router.get("/{tenant_id}/logs", response_model=NotificationUsageLogsResponse)
async def list_notification_usage_logs(
    tenant_id: str,
    request: Request,
    month: str = Query(..., description="Month in YYYY-MM format"),
    channel: Optional[str] = Query(None),
    status_value: Optional[str] = Query(None, alias="status"),
    rule_id: Optional[str] = Query(None),
    device_id: Optional[str] = Query(None),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    search: Optional[str] = Query(None, description="Match masked recipient or provider message id"),
    include_metadata: bool = Query(False),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> NotificationUsageLogsResponse:
    ctx = _super_admin_context(request)
    service = NotificationDeliveryAuditService(db, ctx)
    month_value = month.default if isinstance(month, Param) else month
    page_value = int(page.default if isinstance(page, Param) else page)
    page_size_value = int(page_size.default if isinstance(page_size, Param) else page_size)
    include_metadata_value = bool(include_metadata.default if isinstance(include_metadata, Param) else include_metadata)
    log_page = await service.list_month_logs(
        tenant_id=tenant_id,
        month=month_value,
        filters=_build_filters(
            channel=channel,
            status_value=status_value,
            rule_id=rule_id,
            device_id=device_id,
            date_from=date_from,
            date_to=date_to,
            search=search,
        ),
        page=page_value,
        page_size=page_size_value,
    )
    rows = [
        NotificationUsageLogRow(
            id=row.id,
            tenant_id=row.tenant_id,
            attempted_at=row.attempted_at,
            channel=row.channel,
            status=row.status,
            event_type=row.event_type,
            recipient_masked=row.recipient_masked,
            provider_name=row.provider_name,
            provider_message_id=row.provider_message_id,
            rule_id=row.rule_id,
            device_id=row.device_id,
            billable_units=row.billable_units,
            failure_code=row.failure_code,
            failure_message=row.failure_message,
            accepted_at=row.accepted_at,
            delivered_at=row.delivered_at,
            failed_at=row.failed_at,
            metadata_json=row.metadata_json if include_metadata_value else None,
        )
        for row in log_page.rows
    ]
    return NotificationUsageLogsResponse(
        tenant_id=tenant_id,
        month=month_value,
        page=page_value,
        page_size=page_size_value,
        total=log_page.total,
        data=rows,
    )


@router.get("/{tenant_id}/export.csv")
async def export_notification_usage_csv(
    tenant_id: str,
    request: Request,
    month: str = Query(..., description="Month in YYYY-MM format"),
    channel: Optional[str] = Query(None),
    status_value: Optional[str] = Query(None, alias="status"),
    rule_id: Optional[str] = Query(None),
    device_id: Optional[str] = Query(None),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    search: Optional[str] = Query(None, description="Match masked recipient or provider message id"),
    db: AsyncSession = Depends(get_db),
) -> Response:
    ctx = _super_admin_context(request)
    service = NotificationDeliveryAuditService(db, ctx)
    month_value = month.default if isinstance(month, Param) else month
    channel_value = channel.default if isinstance(channel, Param) else channel
    status_value_resolved = status_value.default if isinstance(status_value, Param) else status_value
    generated_at = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix: list[str] = [tenant_id, month_value]
    if channel_value:
        suffix.append(channel_value)
    if status_value_resolved:
        suffix.append(status_value_resolved)
    suffix.append(generated_at)
    filename = f"notification_usage_{'_'.join(suffix)}.csv"
    stream = service.stream_month_logs_csv(
        tenant_id=tenant_id,
        month=month_value,
        filters=_build_filters(
            channel=channel_value,
            status_value=status_value,
            rule_id=rule_id,
            device_id=device_id,
            date_from=date_from,
            date_to=date_to,
            search=search,
        ),
    )
    return StreamingResponse(
        content=stream,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Notification-Usage-Month": month_value,
            "X-Export-Generated-At": generated_at,
        },
    )
