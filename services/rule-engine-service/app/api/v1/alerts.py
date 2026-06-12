"""API endpoints for alert management."""

from typing import List, Optional
from uuid import UUID
from datetime import datetime
import logging

import httpx
from fastapi import APIRouter, Depends, Query, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.repositories.rule import AlertRepository, ActivityEventRepository
from app.schemas.rule import AlertResponse, ActivityEventResponse, ErrorResponse
from app.services.device_scope import DeviceScopeService
from app.services.rule import ActivityEventService
from services.shared.tenant_context import TenantContext

router = APIRouter()
logger = logging.getLogger(__name__)


def _tenant_context(request: Request) -> TenantContext:
    ctx = TenantContext.from_request(request)
    ctx.require_tenant()
    return ctx


async def _resolve_accessible_device_ids(ctx: TenantContext) -> list[str] | None:
    try:
        return await DeviceScopeService(ctx).resolve_accessible_device_ids()
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "DEVICE_SCOPE_UNAVAILABLE",
                "message": "Unable to validate device access right now.",
            },
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "DEVICE_SCOPE_UNAVAILABLE",
                "message": str(exc),
            },
        ) from exc


class AlertListResponse(BaseModel):
    """Schema for paginated alert list response."""

    success: bool = True
    data: List[AlertResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class AlertSingleResponse(BaseModel):
    success: bool = True
    data: AlertResponse


class AlertAcknowledgeRequest(BaseModel):
    acknowledged_by: Optional[str] = None


class ActivityEventListResponse(BaseModel):
    success: bool = True
    data: List[ActivityEventResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class ActivityUnreadCountResponse(BaseModel):
    success: bool = True
    data: dict


class ActivityActionResponse(BaseModel):
    success: bool = True
    data: dict


class ActivitySummaryResponse(BaseModel):
    success: bool = True
    data: dict


# ---------------------------------------------------------------------
# List alerts
# ---------------------------------------------------------------------
@router.get(
    "",
    response_model=AlertListResponse,
    responses={
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def list_alerts(
    request: Request,
    device_id: Optional[str] = Query(None, description="Filter by device ID"),
    rule_id: Optional[UUID] = Query(None, description="Filter by rule ID"),
    status: Optional[str] = Query(None, description="Filter by alert status"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    db: AsyncSession = Depends(get_db),
) -> AlertListResponse:
    """List all alerts with optional filtering and pagination."""

    ctx = _tenant_context(request)
    repository = AlertRepository(db, ctx)
    accessible_device_ids = await _resolve_accessible_device_ids(ctx)

    alerts, total = await repository.list_alerts(
        device_id=device_id,
        rule_id=str(rule_id) if rule_id is not None else None,
        status=status,
        page=page,
        page_size=page_size,
        accessible_device_ids=accessible_device_ids,
    )

    total_pages = (total + page_size - 1) // page_size

    return AlertListResponse(
        data=alerts,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


# ---------------------------------------------------------------------
# Acknowledge alert
# ---------------------------------------------------------------------
@router.patch(
    "/{alert_id}/acknowledge",
    response_model=AlertSingleResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Alert not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def acknowledge_alert(
    alert_id: UUID,
    payload: AlertAcknowledgeRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AlertSingleResponse:
    """
    Acknowledge an alert.
    """

    ctx = _tenant_context(request)
    repository = AlertRepository(db, ctx)
    accessible_device_ids = await _resolve_accessible_device_ids(ctx)

    # Reuse existing repository lookup logic
    alert = await repository.get_by_id(
        alert_id=str(alert_id),
        accessible_device_ids=accessible_device_ids,
    )

    if not alert:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "success": False,
                "error": {
                    "code": "ALERT_NOT_FOUND",
                    "message": f"Alert with ID '{alert_id}' not found",
                },
                "timestamp": datetime.utcnow().isoformat(),
            },
        )

    alert = await repository.acknowledge_alert(
        alert_id=str(alert_id),
        acknowledged_by=payload.acknowledged_by,
    )

    try:
        activity_service = ActivityEventService(db, ctx)
        await activity_service.create_event(
            event_type="alert_acknowledged",
            title="Alert Acknowledged",
            message=f"Alert acknowledged for device '{alert.device_id}'.",
            device_id=alert.device_id,
            rule_id=str(alert.rule_id),
            alert_id=str(alert.alert_id),
            metadata_json={
                "status": alert.status,
                "acknowledged_by": payload.acknowledged_by,
            },
        )
    except Exception as exc:
        logger.warning("Failed to persist alert_acknowledged activity event", extra={"error": str(exc)})

    return AlertSingleResponse(data=alert)


# ---------------------------------------------------------------------
# Resolve alert
# ---------------------------------------------------------------------
@router.patch(
    "/{alert_id}/resolve",
    response_model=AlertSingleResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Alert not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def resolve_alert(
    alert_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AlertSingleResponse:
    """
    Mark an alert as resolved.
    """

    ctx = _tenant_context(request)
    repository = AlertRepository(db, ctx)
    accessible_device_ids = await _resolve_accessible_device_ids(ctx)

    alert = await repository.get_by_id(
        alert_id=str(alert_id),
        accessible_device_ids=accessible_device_ids,
    )

    if not alert:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "success": False,
                "error": {
                    "code": "ALERT_NOT_FOUND",
                    "message": f"Alert with ID '{alert_id}' not found",
                },
                "timestamp": datetime.utcnow().isoformat(),
            },
        )

    alert = await repository.resolve_alert(
        alert_id=str(alert_id),
    )

    try:
        activity_service = ActivityEventService(db, ctx)
        await activity_service.create_event(
            event_type="alert_resolved",
            title="Alert Resolved",
            message=f"Alert resolved for device '{alert.device_id}'.",
            device_id=alert.device_id,
            rule_id=str(alert.rule_id),
            alert_id=str(alert.alert_id),
            metadata_json={"status": alert.status},
        )
    except Exception as exc:
        logger.warning("Failed to persist alert_resolved activity event", extra={"error": str(exc)})

    return AlertSingleResponse(data=alert)


@router.get(
    "/events",
    response_model=ActivityEventListResponse,
)
async def list_activity_events(
    request: Request,
    device_id: Optional[str] = Query(None, description="Filter by device ID"),
    event_type: Optional[str] = Query(None, description="Filter by event type"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    db: AsyncSession = Depends(get_db),
) -> ActivityEventListResponse:
    ctx = _tenant_context(request)
    repository = ActivityEventRepository(db, ctx)
    accessible_device_ids = await _resolve_accessible_device_ids(ctx)
    events, total = await repository.list_events(
        device_id=device_id,
        event_type=event_type,
        page=page,
        page_size=page_size,
        accessible_device_ids=accessible_device_ids,
    )
    total_pages = (total + page_size - 1) // page_size
    return ActivityEventListResponse(
        data=events,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get(
    "/events/unread-count",
    response_model=ActivityUnreadCountResponse,
)
async def get_unread_event_count(
    request: Request,
    device_id: Optional[str] = Query(None, description="Filter by device ID"),
    db: AsyncSession = Depends(get_db),
) -> ActivityUnreadCountResponse:
    ctx = _tenant_context(request)
    repository = ActivityEventRepository(db, ctx)
    accessible_device_ids = await _resolve_accessible_device_ids(ctx)
    count = await repository.unread_count(device_id=device_id, accessible_device_ids=accessible_device_ids)
    return ActivityUnreadCountResponse(data={"count": count})


@router.patch(
    "/events/mark-all-read",
    response_model=ActivityActionResponse,
)
async def mark_all_events_read(
    request: Request,
    device_id: Optional[str] = Query(None, description="Filter by device ID"),
    db: AsyncSession = Depends(get_db),
) -> ActivityActionResponse:
    ctx = _tenant_context(request)
    repository = ActivityEventRepository(db, ctx)
    accessible_device_ids = await _resolve_accessible_device_ids(ctx)
    updated = await repository.mark_all_read(device_id=device_id, accessible_device_ids=accessible_device_ids)
    await db.commit()
    return ActivityActionResponse(data={"updated": updated})


@router.delete(
    "/events",
    response_model=ActivityActionResponse,
)
async def clear_event_history(
    request: Request,
    device_id: Optional[str] = Query(None, description="Filter by device ID"),
    db: AsyncSession = Depends(get_db),
) -> ActivityActionResponse:
    ctx = _tenant_context(request)
    repository = ActivityEventRepository(db, ctx)
    accessible_device_ids = await _resolve_accessible_device_ids(ctx)
    deleted = await repository.clear_history(device_id=device_id, accessible_device_ids=accessible_device_ids)
    await db.commit()
    return ActivityActionResponse(data={"deleted": deleted})


@router.get(
    "/events/summary",
    response_model=ActivitySummaryResponse,
)
async def get_activity_summary(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ActivitySummaryResponse:
    """Get system-wide activity summary for dashboard cards."""
    ctx = _tenant_context(request)
    activity_repository = ActivityEventRepository(db, ctx)
    alert_repository = AlertRepository(db, ctx)
    accessible_device_ids = await _resolve_accessible_device_ids(ctx)

    event_counts = await activity_repository.count_by_event_types(
        [
            "rule_created",
            "rule_triggered",
            "alert_resolved",
            "alert_cleared",
            "rule_updated",
            "rule_deleted",
            "rule_archived",
        ],
        accessible_device_ids=accessible_device_ids,
    )
    alert_counts = await alert_repository.count_by_status(accessible_device_ids=accessible_device_ids)

    active_alerts = int(alert_counts.get("open", 0)) + int(alert_counts.get("acknowledged", 0))
    alerts_cleared = int(event_counts.get("alert_resolved", 0)) + int(event_counts.get("alert_cleared", 0))

    return ActivitySummaryResponse(
        data={
            "active_alerts": active_alerts,
            "alerts_triggered": int(event_counts.get("rule_triggered", 0)),
            "alerts_cleared": alerts_cleared,
            "rules_created": int(event_counts.get("rule_created", 0)),
            "rules_updated": int(event_counts.get("rule_updated", 0)),
            "rules_deleted": int(event_counts.get("rule_deleted", 0)) + int(event_counts.get("rule_archived", 0)),
        }
    )
