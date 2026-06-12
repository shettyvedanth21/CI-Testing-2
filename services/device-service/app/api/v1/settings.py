"""Device-service settings endpoints for waste analysis defaults."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.device import ErrorResponse
from shared.feature_entitlements import require_feature

router = APIRouter(tags=["settings"], dependencies=[Depends(require_feature("settings"))])


class SiteWasteConfigRequest(BaseModel):
    default_unoccupied_weekday_start_time: str = Field(..., pattern=r"^\d{2}:\d{2}$")
    default_unoccupied_weekday_end_time: str = Field(..., pattern=r"^\d{2}:\d{2}$")
    default_unoccupied_weekend_start_time: str = Field(..., pattern=r"^\d{2}:\d{2}$")
    default_unoccupied_weekend_end_time: str = Field(..., pattern=r"^\d{2}:\d{2}$")
    timezone: Optional[str] = "Asia/Kolkata"
    updated_by: Optional[str] = None
    tenant_id: Optional[str] = None


@router.get(
    "/waste-config",
    response_model=dict,
    responses={404: {"model": ErrorResponse, "description": "Not found"}},
)
async def get_site_waste_config(
    tenant_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    from app.services.idle_running import IdleRunningService

    service = IdleRunningService(db)
    data = await service.get_site_waste_config(tenant_id)
    return {"success": True, **data}


@router.put(
    "/waste-config",
    response_model=dict,
    responses={400: {"model": ErrorResponse, "description": "Validation error"}},
)
async def set_site_waste_config(
    payload: SiteWasteConfigRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    from app.services.idle_running import IdleRunningService

    service = IdleRunningService(db)

    data = await service.set_site_waste_config(
        default_unoccupied_weekday_start_time=payload.default_unoccupied_weekday_start_time,
        default_unoccupied_weekday_end_time=payload.default_unoccupied_weekday_end_time,
        default_unoccupied_weekend_start_time=payload.default_unoccupied_weekend_start_time,
        default_unoccupied_weekend_end_time=payload.default_unoccupied_weekend_end_time,
        timezone_name=payload.timezone,
        updated_by=payload.updated_by,
        tenant_id=payload.tenant_id,
    )
    return {"success": True, **data}
