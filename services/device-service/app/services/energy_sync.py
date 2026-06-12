from __future__ import annotations

import logging
from datetime import date

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.device import DeviceLiveState
from app.services.shared_http import get_client, request_with_retries
from services.shared.tenant_context import build_internal_headers

logger = logging.getLogger(__name__)


async def sync_energy_device_days(
    *,
    session: AsyncSession,
    tenant_id: str,
    device_ids: list[str],
    day: date,
) -> dict[str, object]:
    base_url = (settings.ENERGY_SERVICE_BASE_URL or "").rstrip("/")
    normalized_ids = sorted({str(device_id).strip() for device_id in device_ids if str(device_id).strip()})
    if not base_url or not normalized_ids:
        return {"attempted": False, "updated": 0, "device_ids": normalized_ids}

    rows = (
        await session.execute(
            select(DeviceLiveState).where(
                DeviceLiveState.tenant_id == tenant_id,
                DeviceLiveState.device_id.in_(normalized_ids),
            )
        )
    ).scalars().all()
    devices_payload = [
        {
            "device_id": str(row.device_id),
            "energy_kwh": float(row.today_energy_kwh or 0.0),
            "idle_kwh": float(row.today_idle_kwh or 0.0),
            "offhours_kwh": float(row.today_offhours_kwh or 0.0),
            "overconsumption_kwh": float(row.today_overconsumption_kwh or 0.0),
            "loss_kwh": float(row.today_loss_kwh or 0.0),
        }
        for row in rows
        if str(row.device_id or "").strip()
    ]
    if not devices_payload:
        return {"attempted": False, "updated": 0, "device_ids": normalized_ids, "skipped": "no_live_rows"}

    headers = build_internal_headers("device-service", tenant_id)
    try:
        client = await get_client(base_url)
        response = await request_with_retries(
            client,
            "POST",
            "/api/v1/energy/internal/rebuild-device-days",
            operation="device_service_sync_energy_device_days",
            headers=headers,
            json={
                "day": day.isoformat(),
                "devices": devices_payload,
            },
            timeout=20.0,
        )
    except httpx.HTTPError as exc:
        logger.warning(
            "energy_device_day_sync_failed",
            extra={"tenant_id": tenant_id, "device_ids": normalized_ids, "day": day.isoformat(), "error": str(exc)},
        )
        return {"attempted": True, "updated": 0, "device_ids": normalized_ids, "error": str(exc)}

    if response.status_code != 200:
        logger.warning(
            "energy_device_day_sync_rejected",
            extra={
                "tenant_id": tenant_id,
                "device_ids": normalized_ids,
                "day": day.isoformat(),
                "status_code": response.status_code,
            },
        )
        return {
            "attempted": True,
            "updated": 0,
            "device_ids": normalized_ids,
            "status_code": response.status_code,
        }

    payload = response.json() if response.content else {}
    data = payload if isinstance(payload, dict) else {}
    return {
        "attempted": True,
        "updated": int(data.get("updated", 0) or 0),
        "device_ids": normalized_ids,
        "skipped": list(data.get("skipped", []) or []),
    }
