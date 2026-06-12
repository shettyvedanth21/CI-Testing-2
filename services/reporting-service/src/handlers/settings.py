from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_db
from src.repositories.settings_repository import SettingsRepository
from src.repositories.tariff_repository import TariffRepository
from src.services.tenant_scope import build_service_tenant_context
from services.shared.tenant_context import TenantContext

router = APIRouter(tags=["settings"])


class TariffUpsertRequest(BaseModel):
    rate: Decimal = Field(..., gt=0)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    updated_by: Optional[str] = None


class NotificationEmailRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=320)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if "@" not in normalized or normalized.startswith("@") or normalized.endswith("@"):
            raise ValueError("email must be a valid email address")
        return normalized


def _settings_repo(request: Request, db: AsyncSession) -> SettingsRepository:
    tenant_id = TenantContext.from_request(request).require_tenant()
    return SettingsRepository(db, build_service_tenant_context(tenant_id))


def _tariff_repo(request: Request, db: AsyncSession) -> TariffRepository:
    tenant_id = TenantContext.from_request(request).require_tenant()
    return TariffRepository(db, build_service_tenant_context(tenant_id))


def _serialize_tariff(row, *, active_version=None) -> dict:
    if not row:
        return {
            "rate": None,
            "currency": "INR",
            "updated_at": None,
            "updated_by": None,
            "effective_from": None,
            "is_active": False,
        }
    return {
        "rate": float(row.energy_rate_per_kwh),
        "currency": row.currency,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "updated_by": getattr(active_version, "created_by", None),
        "effective_from": (
            active_version.effective_start_at.isoformat()
            if getattr(active_version, "effective_start_at", None)
            else None
        ),
        "is_active": active_version is not None,
    }


def _serialize_tariff_version(version, *, active_version_id: int | None) -> dict:
    return {
        "id": str(version.id),
        "rate": float(version.energy_rate_per_kwh),
        "currency": version.currency,
        "updated_at": version.created_at.isoformat() if version.created_at else None,
        "effective_from": version.effective_start_at.isoformat(),
        "updated_by": version.created_by,
        "is_active": version.id == active_version_id,
    }


def _tariff_actor(request: Request) -> str | None:
    ctx = TenantContext.from_request(request)
    return ctx.user_id or None


@router.get("/tariff")
async def get_tariff(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    repo = _tariff_repo(request, db)
    tenant_id = TenantContext.from_request(request).require_tenant()
    row = await repo.get_tariff(tenant_id)
    active_version = await repo.get_effective_version(tenant_id)
    return _serialize_tariff(row, active_version=active_version)


@router.post("/tariff")
async def upsert_tariff(
    payload: TariffUpsertRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    currency = payload.currency.upper()
    if currency not in {"INR", "USD", "EUR"}:
        raise HTTPException(status_code=400, detail={"error": "VALIDATION_ERROR", "message": "currency must be INR, USD, or EUR"})
    tenant_id = TenantContext.from_request(request).require_tenant()
    repo = _tariff_repo(request, db)
    row = await repo.upsert_tariff(
        tenant_id=tenant_id,
        data={
            "tenant_id": tenant_id,
            "energy_rate_per_kwh": payload.rate,
            "currency": currency,
            "updated_by": payload.updated_by,
            "created_by": payload.updated_by,
        },
    )
    active_version = await repo.get_effective_version(tenant_id)
    return _serialize_tariff(row, active_version=active_version)


@router.get("/tariff/history")
async def get_tariff_history(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    repo = _tariff_repo(request, db)
    tenant_id = TenantContext.from_request(request).require_tenant()
    versions = await repo.list_versions(tenant_id)
    active_version = await repo.get_effective_version(tenant_id)
    active_version_id = None if active_version is None else int(active_version.id)
    return {
        "versions": [
            _serialize_tariff_version(version, active_version_id=active_version_id)
            for version in reversed(versions)
        ]
    }


@router.patch("/tariff/history/{version_id}/activate")
async def activate_tariff_history_version(
    version_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    repo = _tariff_repo(request, db)
    tenant_id = TenantContext.from_request(request).require_tenant()
    try:
        row = await repo.activate_version(
            version_id=version_id,
            tenant_id=tenant_id,
            activated_by=_tariff_actor(request),
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=404,
            detail={"error": "NOT_FOUND", "message": "Tariff version not found"},
        ) from exc

    active_version = await repo.get_effective_version(tenant_id)
    return _serialize_tariff(row, active_version=active_version)


@router.get("/notifications")
async def get_notifications(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    repo = _settings_repo(request, db)
    emails = await repo.list_active_channels("email")
    return {
        "email": [
            {"id": row.id, "value": row.value, "is_active": row.is_active}
            for row in emails
        ],
        "whatsapp": [],
        "sms": [],
    }


@router.post("/notifications/email")
async def add_notification_email(
    payload: NotificationEmailRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    repo = _settings_repo(request, db)
    row = await repo.add_email_channel(payload.email)
    return {"id": row.id, "value": row.value, "is_active": row.is_active}


@router.delete("/notifications/email/{channel_id}")
async def delete_notification_email(
    channel_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    repo = _settings_repo(request, db)
    deleted = await repo.disable_email_channel(channel_id)
    if not deleted:
        raise HTTPException(
            status_code=404,
            detail={"error": "NOT_FOUND", "message": "Notification channel not found"},
        )
    return {"success": True, "id": channel_id}
