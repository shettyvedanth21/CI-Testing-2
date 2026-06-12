from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.database import get_db
from app.dependencies import require_any_authenticated
from app.models.auth import PlatformMaintenanceStatus
from app.repositories.platform_maintenance_repository import PlatformMaintenanceRepository
from app.schemas.platform_maintenance import (
    CurrentPlatformMaintenanceResponse,
    PlatformMaintenanceAnnouncementResponse,
)
from app.services.platform_maintenance_status import compute_platform_maintenance_effective_status

router = APIRouter(prefix="/api/v1/platform-maintenance", tags=["platform-maintenance"])

platform_maintenance_repo = PlatformMaintenanceRepository()
UTC = timezone.utc


def _effective_status(announcement, *, now: datetime) -> str:
    return compute_platform_maintenance_effective_status(announcement, now=now)


def _normalize_response_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _serialize_announcement(announcement, *, now: datetime) -> PlatformMaintenanceAnnouncementResponse:
    target_tenant_ids = sorted(target.tenant_id for target in announcement.targets)
    return PlatformMaintenanceAnnouncementResponse(
        id=announcement.id,
        title=announcement.title,
        severity=announcement.severity,
        message=announcement.message,
        starts_at=_normalize_response_datetime(announcement.starts_at),
        estimated_duration_minutes=announcement.estimated_duration_minutes,
        ends_at=_normalize_response_datetime(announcement.ends_at),
        status=announcement.status,
        effective_status=_effective_status(announcement, now=now),
        broadcast_all_tenants=announcement.broadcast_all_tenants,
        target_tenant_ids=target_tenant_ids,
        created_by=announcement.created_by_user_id,
        updated_by=announcement.updated_by_user_id,
        created_at=_normalize_response_datetime(announcement.created_at),
        updated_at=_normalize_response_datetime(announcement.updated_at),
    )


def _resolve_tenant_id(request: Request, claims: dict) -> str:
    if claims.get("role") == "super_admin":
        tenant_id = (
            request.headers.get("X-Target-Tenant-Id")
            or request.query_params.get("tenant_id")
        )
        if tenant_id:
            return tenant_id
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "TENANT_SCOPE_REQUIRED", "message": "Tenant scope is required for this action."},
        )

    tenant_id = claims.get("tenant_id")
    if tenant_id:
        return tenant_id
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"code": "TENANT_SCOPE_REQUIRED", "message": "Tenant scope is required for this action."},
    )


@router.get("/current", response_model=CurrentPlatformMaintenanceResponse, status_code=status.HTTP_200_OK)
async def get_current_platform_maintenance(
    request: Request,
    claims: dict = Depends(require_any_authenticated),
    db=Depends(get_db),
) -> CurrentPlatformMaintenanceResponse:
    tenant_id = _resolve_tenant_id(request, claims)
    now = datetime.now(UTC)
    announcements = await platform_maintenance_repo.list_current_for_tenant(db, tenant_id, now=now)
    return CurrentPlatformMaintenanceResponse(
        tenant_id=tenant_id,
        announcements=[_serialize_announcement(announcement, now=now) for announcement in announcements],
    )
