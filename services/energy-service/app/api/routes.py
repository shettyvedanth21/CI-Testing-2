from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas import DeviceLifecycleRequest, LiveUpdateBatchRequest, LiveUpdateRequest
from app.services.broadcaster import energy_broadcaster
from app.services.energy_engine import EnergyEngine
from app.services.reconciliation_apply import ReconciliationApplyService
from shared.feature_entitlements import require_feature
from shared.tenant_context import resolve_request_tenant_id

router = APIRouter(prefix="/api/v1/energy")


class CurrentDayDeviceMetric(BaseModel):
    device_id: str
    energy_kwh: float = 0.0
    idle_kwh: float = 0.0
    offhours_kwh: float = 0.0
    overconsumption_kwh: float = 0.0
    loss_kwh: float = 0.0


class CurrentDaySyncRequest(BaseModel):
    day: date
    devices: list[CurrentDayDeviceMetric]


def get_tenant_id(request: Request) -> str | None:
    return resolve_request_tenant_id(request)


@router.get("/health")
async def health() -> dict:
    return {"status": "healthy", "service": "energy-service"}


@router.post("/live-update")
async def live_update(request: Request, payload: LiveUpdateRequest, db: AsyncSession = Depends(get_db)) -> dict:
    telemetry = payload.telemetry or {}
    device_id = str(telemetry.get("device_id") or "").strip()
    if not device_id:
        return {"success": False, "error": "device_id required"}

    engine = EnergyEngine(db)
    tenant_id = resolve_request_tenant_id(request, explicit_tenant_id=payload.tenant_id)
    result = await engine.apply_live_update(
        device_id=device_id,
        telemetry=telemetry,
        dynamic_fields=payload.dynamic_fields,
        normalized_fields=payload.normalized_fields,
        tenant_id=tenant_id,
    )
    if result.get("idempotent_drop"):
        return JSONResponse(
            status_code=503,
            content={
                "success": False,
                "error": "optimistic_lock_contention",
                "retryable": True,
                "data": result,
            },
        )
    await energy_broadcaster.publish("energy_update", result)
    return {"success": True, "data": result}


@router.post("/live-update/batch")
async def live_update_batch(request: Request, payload: LiveUpdateBatchRequest, db: AsyncSession = Depends(get_db)) -> dict:
    tenant_id = resolve_request_tenant_id(request, explicit_tenant_id=payload.tenant_id)
    engine = EnergyEngine(db)
    results = await engine.apply_live_updates_batch(
        tenant_id=tenant_id,
        updates=[
            {
                "telemetry": update.telemetry,
                "dynamic_fields": update.dynamic_fields,
                "normalized_fields": update.normalized_fields,
            }
            for update in payload.updates
        ],
    )
    success_payloads = [
        item["data"]
        for item in results
        if item.get("success") and isinstance(item.get("data"), dict)
    ]
    if success_payloads:
        await energy_broadcaster.publish_many("energy_update", success_payloads)
    return {"success": True, "results": results}


@router.post("/device-lifecycle/{device_id}")
async def device_lifecycle(device_id: str, payload: DeviceLifecycleRequest, request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    tenant_id = get_tenant_id(request)
    engine = EnergyEngine(db)
    result = await engine.apply_device_lifecycle(
        device_id=device_id,
        status=payload.status,
        at=payload.at,
        tenant_id=tenant_id,
    )
    await energy_broadcaster.publish("energy_update", {"device_id": device_id, "version": result.get("version"), "freshness_ts": payload.at.isoformat() if payload.at else None})
    return {"success": True, "data": result}


@router.get("/summary")
async def summary(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    payload = await EnergyEngine(db).get_summary(tenant_id=get_tenant_id(request))
    return {"success": True, **payload}


@router.get("/today-loss-breakdown")
async def today_loss_breakdown(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    payload = await EnergyEngine(db).get_today_loss_breakdown(tenant_id=get_tenant_id(request))
    return {"success": True, **payload}


@router.get("/calendar/monthly", dependencies=[Depends(require_feature("calendar"))])
async def calendar_monthly(
    request: Request,
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    device_ids: Optional[str] = Query(None),
    plant_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    parsed_device_ids: Optional[list[str]] = None
    if device_ids:
        parsed_device_ids = [d.strip() for d in device_ids.split(",") if d.strip()]
    payload = await EnergyEngine(db).get_monthly_calendar(
        year=year,
        month=month,
        tenant_id=get_tenant_id(request),
        device_ids=parsed_device_ids,
        plant_id=plant_id,
    )
    return {"success": True, **payload}


@router.get("/device/{device_id}/range")
async def device_range(
    request: Request,
    device_id: str,
    start_date: date = Query(...),
    end_date: date = Query(...),
    db: AsyncSession = Depends(get_db),
) -> dict:
    payload = await EnergyEngine(db).get_device_range(
        device_id=device_id,
        start=start_date,
        end=end_date,
        tenant_id=get_tenant_id(request),
    )
    return {"success": True, **payload}


@router.post("/internal/rebuild-device-days")
async def rebuild_device_days(
    request: Request,
    payload: CurrentDaySyncRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    tenant_id = get_tenant_id(request)
    if not tenant_id:
        return {"success": False, "error": "tenant_id required"}
    payload = await ReconciliationApplyService(db).sync_device_days_from_telemetry(
        tenant_id=tenant_id,
        device_ids=[item.device_id for item in payload.devices],
        day=payload.day,
        actor="device-service-sync",
        live_metrics=[item.model_dump() for item in payload.devices],
    )
    return {"success": True, **payload}
