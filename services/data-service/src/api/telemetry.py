from fastapi import APIRouter, Depends, Query, Request
from datetime import datetime
from typing import Optional

from services.shared.tenant_context import require_tenant
from src.services.telemetry_service import TelemetryService

router = APIRouter()


def get_telemetry_service() -> TelemetryService:
    from src.main import app_state
    return app_state.telemetry_service


@router.get("/telemetry/{device_id}")
async def get_device_telemetry(
    request: Request,
    device_id: str,
    start_time: Optional[datetime] = Query(None),
    end_time: Optional[datetime] = Query(None),
    limit: int = Query(100),
    telemetry_service: TelemetryService = Depends(get_telemetry_service),
):
    tenant_id = require_tenant(request)
    return await telemetry_service.query_telemetry(
        tenant_id=tenant_id,
        device_id=device_id,
        start_time=start_time,
        end_time=end_time,
        limit=limit,
    )


@router.get("/stats/{device_id}")
async def get_device_stats(
    request: Request,
    device_id: str,
    start_time: Optional[datetime] = Query(None),
    end_time: Optional[datetime] = Query(None),
    telemetry_service: TelemetryService = Depends(get_telemetry_service),
):
    tenant_id = require_tenant(request)
    return await telemetry_service.get_stats(
        tenant_id=tenant_id,
        device_id=device_id,
        start_time=start_time,
        end_time=end_time,
    )
