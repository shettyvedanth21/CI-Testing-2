from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response, status
from passlib.context import CryptContext
from pydantic import ValidationError
from sqlalchemy import select

from app.config import settings
from app.database import get_db
from app.dependencies import require_super_admin
from app.models.auth import PlatformMaintenanceStatus, User, UserRole
from app.repositories.org_repository import OrgRepository
from app.repositories.platform_maintenance_repository import PlatformMaintenanceRepository
from app.repositories.user_repository import UserRepository
from app.schemas.auth import CreateTenantRequest, CreateUserRequest, SuperAdminSummaryResponse, TenantResponse, UserResponse
from app.schemas.platform_maintenance import (
    CreatePlatformMaintenanceAnnouncementRequest,
    PlatformMaintenanceAnnouncementResponse,
    UpdatePlatformMaintenanceAnnouncementRequest,
)
from app.services.platform_maintenance_status import compute_platform_maintenance_effective_status
from app.services.tenant_id_service import TenantIdAllocationError
from services.shared.tenant_context import build_internal_headers

router = APIRouter(prefix="/api/admin", tags=["super-admin"], dependencies=[Depends(require_super_admin)])

org_repo = OrgRepository()
user_repo = UserRepository()
platform_maintenance_repo = PlatformMaintenanceRepository()
pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
UTC = timezone.utc


def _effective_status(announcement, *, now: datetime) -> PlatformMaintenanceStatus:
    return compute_platform_maintenance_effective_status(announcement, now=now)


def _normalize_response_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _serialize_announcement(announcement, *, now: datetime) -> PlatformMaintenanceAnnouncementResponse:
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
        target_tenant_ids=sorted(target.tenant_id for target in announcement.targets),
        created_by=announcement.created_by_user_id,
        updated_by=announcement.updated_by_user_id,
        created_at=_normalize_response_datetime(announcement.created_at),
        updated_at=_normalize_response_datetime(announcement.updated_at),
    )


async def _get_total_active_devices() -> int:
    base_url = settings.DEVICE_SERVICE_BASE_URL.rstrip("/")
    if not base_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "SUPER_ADMIN_SUMMARY_UNAVAILABLE",
                "message": "Device service summary dependency is not configured.",
            },
        )

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                f"{base_url}/api/v1/devices/internal/summary/active-device-count",
                headers=build_internal_headers("auth-service"),
            )
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "SUPER_ADMIN_SUMMARY_UNAVAILABLE",
                "message": "Unable to load active device summary right now. Please try again.",
            },
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "SUPER_ADMIN_SUMMARY_UNAVAILABLE",
                "message": "Unable to load active device summary right now. Please try again.",
            },
        ) from exc

    if response.status_code >= 400:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "SUPER_ADMIN_SUMMARY_UNAVAILABLE",
                "message": "Unable to load active device summary right now. Please try again.",
            },
        )

    payload = response.json()
    count = payload.get("total_active_devices")
    if not isinstance(count, int):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "SUPER_ADMIN_SUMMARY_UNAVAILABLE",
                "message": "Device service summary returned an unexpected response.",
            },
        )
    return count


async def _assert_target_tenants_exist(db, tenant_ids: list[str]) -> None:
    if not tenant_ids:
        return
    orgs = await org_repo.list_by_ids(db, tenant_ids)
    found_ids = {org.id for org in orgs}
    missing_ids = sorted(set(tenant_ids) - found_ids)
    if missing_ids:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "TARGET_TENANT_NOT_FOUND",
                "message": "One or more target tenants were not found",
                "tenant_ids": missing_ids,
            },
        )
    inactive_ids = sorted(org.id for org in orgs if not org.is_active)
    if inactive_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "TARGET_TENANT_INACTIVE",
                "message": "Target organisations must be active before they can receive a maintenance notice",
                "tenant_ids": inactive_ids,
            },
        )


async def _assert_no_overlapping_platform_maintenance(
    db,
    *,
    starts_at: datetime,
    estimated_duration_minutes: int,
    broadcast_all_tenants: bool,
    target_tenant_ids: list[str],
    exclude_announcement_id: str | None = None,
) -> None:
    overlaps = await platform_maintenance_repo.list_overlapping_announcements(
        db,
        starts_at=starts_at,
        estimated_duration_minutes=estimated_duration_minutes,
        broadcast_all_tenants=broadcast_all_tenants,
        target_tenant_ids=target_tenant_ids,
        exclude_announcement_id=exclude_announcement_id,
    )
    if overlaps:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "PLATFORM_MAINTENANCE_OVERLAP",
                "message": "Maintenance windows cannot overlap for the same tenant audience.",
                "announcement_ids": sorted(str(announcement.id) for announcement in overlaps),
            },
        )


def _resolve_announcement_update(
    announcement,
    body: UpdatePlatformMaintenanceAnnouncementRequest,
) -> CreatePlatformMaintenanceAnnouncementRequest:
    payload = body.model_dump(exclude_unset=True)
    broadcast_all_tenants = payload.get("broadcast_all_tenants", announcement.broadcast_all_tenants)
    target_tenant_ids = payload.get("target_tenant_ids")
    if target_tenant_ids is None:
        target_tenant_ids = [target.tenant_id for target in announcement.targets]
    try:
        return CreatePlatformMaintenanceAnnouncementRequest(
            title=payload.get("title", announcement.title),
            severity=payload.get("severity", announcement.severity),
            message=payload.get("message", announcement.message),
            starts_at=payload.get("starts_at", announcement.starts_at),
            estimated_duration_minutes=payload.get(
                "estimated_duration_minutes",
                announcement.estimated_duration_minutes,
            ),
            status=payload.get("status", announcement.status),
            broadcast_all_tenants=broadcast_all_tenants,
            target_tenant_ids=target_tenant_ids,
        )
    except ValidationError as exc:
        details: list[dict[str, str | list[str | int]]] = []
        for err in exc.errors(include_url=False):
            details.append(
                {
                    "loc": [str(part) if not isinstance(part, int) else part for part in err.get("loc", ())],
                    "msg": str(err.get("msg", "Invalid value")),
                    "type": str(err.get("type", "value_error")),
                }
            )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "VALIDATION_ERROR",
                "message": "Maintenance notice update is invalid",
                "details": details,
            },
        ) from exc


@router.post("/tenants", response_model=TenantResponse, status_code=status.HTTP_201_CREATED)
async def create_tenant(body: CreateTenantRequest, db=Depends(get_db)) -> TenantResponse:
    existing = await org_repo.get_by_slug(db, body.slug)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "SLUG_TAKEN", "message": "Tenant slug already exists"},
        )

    try:
        org = await org_repo.create(db, body.name, body.slug)
    except TenantIdAllocationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "TENANT_ID_ALLOCATION_FAILED",
                "message": "Tenant identity allocation is temporarily unavailable",
            },
        ) from exc
    return TenantResponse.model_validate(org)

@router.get("/tenants", response_model=list[TenantResponse], status_code=status.HTTP_200_OK)
async def list_tenants(db=Depends(get_db)) -> list[TenantResponse]:
    orgs = await org_repo.list_all(db)
    return [TenantResponse.model_validate(org) for org in orgs]


@router.get("/summary", response_model=SuperAdminSummaryResponse, status_code=status.HTTP_200_OK)
async def get_super_admin_summary(db=Depends(get_db)) -> SuperAdminSummaryResponse:
    orgs_task = org_repo.list_all(db)
    active_devices_task = _get_total_active_devices()
    orgs, total_active_devices = await asyncio.gather(orgs_task, active_devices_task)
    return SuperAdminSummaryResponse(
        total_organisations=len(orgs),
        total_active_devices=total_active_devices,
    )


@router.post("/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(body: CreateUserRequest, db=Depends(get_db)) -> UserResponse:
    if body.role != "org_admin":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "INVALID_ROLE",
                "message": "This endpoint only creates org_admin users. Use /api/v1/tenants/{tenant_id}/users for other roles.",
            },
        )

    tenant_id = body.tenant_id
    org = await org_repo.get_by_id(db, tenant_id)
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "TENANT_NOT_FOUND", "message": "Tenant not found"},
        )
    if not org.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "ORG_SUSPENDED",
                "message": "Organization is suspended. New invites and resource creation are blocked.",
            },
        )

    existing = await user_repo.get_by_email(db, body.email)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "EMAIL_TAKEN", "message": "Email already exists"},
        )

    if not body.password:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "PASSWORD_REQUIRED", "message": "Password is required for tenant admin creation."},
        )

    hashed_password = pwd_ctx.hash(body.password)
    user = await user_repo.create(
        db,
        email=body.email,
        hashed_password=hashed_password,
        role=UserRole.ORG_ADMIN,
        tenant_id=tenant_id,
        full_name=body.full_name,
    )
    user.activated_at = datetime.now(UTC).replace(tzinfo=None)
    return UserResponse.model_validate(user)


@router.patch("/tenants/{tenant_id}/suspend", response_model=TenantResponse, status_code=status.HTTP_200_OK)
async def suspend_tenant(tenant_id: str, db=Depends(get_db)) -> TenantResponse:
    org = await org_repo.get_by_id(db, tenant_id)
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "ORG_NOT_FOUND", "message": "Organization not found"},
        )
    if not org.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "ORG_ALREADY_SUSPENDED", "message": "Organization is already suspended"},
        )

    org = await org_repo.update(db, tenant_id, {"is_active": False})
    return TenantResponse.model_validate(org)


@router.patch("/tenants/{tenant_id}/reactivate", response_model=TenantResponse, status_code=status.HTTP_200_OK)
async def reactivate_tenant(tenant_id: str, db=Depends(get_db)) -> TenantResponse:
    org = await org_repo.get_by_id(db, tenant_id)
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "ORG_NOT_FOUND", "message": "Organization not found"},
        )
    if org.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "ORG_ALREADY_ACTIVE", "message": "Organization is already active"},
        )

    org = await org_repo.update(db, tenant_id, {"is_active": True})
    return TenantResponse.model_validate(org)


@router.get("/users", response_model=list[UserResponse], status_code=status.HTTP_200_OK)
async def list_users(
    tenant_id: str | None = None,
    db=Depends(get_db),
) -> list[UserResponse]:
    if tenant_id is None:
        result = await db.execute(select(User).order_by(User.created_at.desc()))
        users = list(result.scalars().all())
    else:
        users = await user_repo.list_by_tenant(db, tenant_id)
    return [UserResponse.model_validate(user) for user in users]


@router.get(
    "/platform-maintenance",
    response_model=list[PlatformMaintenanceAnnouncementResponse],
    status_code=status.HTTP_200_OK,
)
async def list_platform_maintenance_announcements(db=Depends(get_db)) -> list[PlatformMaintenanceAnnouncementResponse]:
    now = datetime.now(UTC)
    announcements = await platform_maintenance_repo.list_all(db)
    return [_serialize_announcement(announcement, now=now) for announcement in announcements]


@router.post(
    "/platform-maintenance",
    response_model=PlatformMaintenanceAnnouncementResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_platform_maintenance_announcement(
    body: CreatePlatformMaintenanceAnnouncementRequest,
    claims: dict = Depends(require_super_admin),
    db=Depends(get_db),
) -> PlatformMaintenanceAnnouncementResponse:
    if not body.broadcast_all_tenants:
        await _assert_target_tenants_exist(db, body.target_tenant_ids)
    await _assert_no_overlapping_platform_maintenance(
        db,
        starts_at=body.starts_at,
        estimated_duration_minutes=body.estimated_duration_minutes,
        broadcast_all_tenants=body.broadcast_all_tenants,
        target_tenant_ids=body.target_tenant_ids,
    )

    announcement = await platform_maintenance_repo.create(
        db,
        title=body.title,
        severity=body.severity,
        message=body.message,
        starts_at=body.starts_at,
        estimated_duration_minutes=body.estimated_duration_minutes,
        status=body.status,
        broadcast_all_tenants=body.broadcast_all_tenants,
        target_tenant_ids=body.target_tenant_ids,
        created_by_user_id=str(claims["sub"]),
    )
    return _serialize_announcement(announcement, now=datetime.now(UTC))


@router.get(
    "/platform-maintenance/{announcement_id}",
    response_model=PlatformMaintenanceAnnouncementResponse,
    status_code=status.HTTP_200_OK,
)
async def get_platform_maintenance_announcement(
    announcement_id: str,
    db=Depends(get_db),
) -> PlatformMaintenanceAnnouncementResponse:
    announcement = await platform_maintenance_repo.get_by_id(db, announcement_id)
    if announcement is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "PLATFORM_MAINTENANCE_NOT_FOUND", "message": "Platform maintenance announcement not found"},
        )
    return _serialize_announcement(announcement, now=datetime.now(UTC))


@router.patch(
    "/platform-maintenance/{announcement_id}",
    response_model=PlatformMaintenanceAnnouncementResponse,
    status_code=status.HTTP_200_OK,
)
async def update_platform_maintenance_announcement(
    announcement_id: str,
    body: UpdatePlatformMaintenanceAnnouncementRequest,
    claims: dict = Depends(require_super_admin),
    db=Depends(get_db),
) -> PlatformMaintenanceAnnouncementResponse:
    announcement = await platform_maintenance_repo.get_by_id(db, announcement_id)
    if announcement is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "PLATFORM_MAINTENANCE_NOT_FOUND", "message": "Platform maintenance announcement not found"},
        )

    resolved_body = _resolve_announcement_update(announcement, body)
    if not resolved_body.broadcast_all_tenants:
        await _assert_target_tenants_exist(db, resolved_body.target_tenant_ids)
    await _assert_no_overlapping_platform_maintenance(
        db,
        starts_at=resolved_body.starts_at,
        estimated_duration_minutes=resolved_body.estimated_duration_minutes,
        broadcast_all_tenants=resolved_body.broadcast_all_tenants,
        target_tenant_ids=resolved_body.target_tenant_ids,
        exclude_announcement_id=announcement_id,
    )

    updated = await platform_maintenance_repo.update(
        db,
        announcement,
        title=resolved_body.title,
        severity=resolved_body.severity,
        message=resolved_body.message,
        starts_at=resolved_body.starts_at,
        estimated_duration_minutes=resolved_body.estimated_duration_minutes,
        status=resolved_body.status,
        broadcast_all_tenants=resolved_body.broadcast_all_tenants,
        target_tenant_ids=resolved_body.target_tenant_ids,
        updated_by_user_id=str(claims["sub"]),
    )
    return _serialize_announcement(updated, now=datetime.now(UTC))


@router.delete(
    "/platform-maintenance/{announcement_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_platform_maintenance_announcement(
    announcement_id: str,
    db=Depends(get_db),
) -> Response:
    announcement = await platform_maintenance_repo.get_by_id(db, announcement_id)
    if announcement is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "PLATFORM_MAINTENANCE_NOT_FOUND", "message": "Platform maintenance announcement not found"},
        )

    await platform_maintenance_repo.delete(db, announcement)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
