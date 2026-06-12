from __future__ import annotations

from typing import Any

import httpx
from fastapi import HTTPException

from src.config import settings
from services.shared.tenant_context import TenantContext, build_tenant_scoped_internal_headers


PLANT_SCOPED_ROLES = {"plant_manager", "operator", "viewer"}


class ReportingDeviceScopeService:
    def __init__(self, ctx: TenantContext):
        self._ctx = ctx

    def _is_plant_scoped_role(self) -> bool:
        return self._ctx.role in PLANT_SCOPED_ROLES

    def _device_is_accessible(self, device: dict[str, Any]) -> bool:
        if not self._is_plant_scoped_role():
            return True
        if not self._ctx.plant_ids:
            return False
        return str(device.get("plant_id") or "") in set(self._ctx.plant_ids)

    def _headers(self) -> dict[str, str]:
        tenant_id = self._ctx.require_tenant()
        return build_tenant_scoped_internal_headers("reporting-service", tenant_id)

    async def _fetch_devices_page(
        self,
        client: httpx.AsyncClient,
        *,
        tenant_id: str,
        page: int,
        page_size: int,
        plant_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], int | None]:
        params: dict[str, Any] = {
            "tenant_id": tenant_id,
            "page": page,
            "page_size": page_size,
        }
        if plant_id:
            params["plant_id"] = plant_id

        response = await client.get(
            f"{settings.DEVICE_SERVICE_URL}/api/v1/devices",
            params=params,
            headers=self._headers(),
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload if isinstance(payload, list) else payload.get("data", [])
        if not isinstance(rows, list):
            raise RuntimeError("Unexpected device-service response while resolving report scope.")
        devices = [row for row in rows if isinstance(row, dict)]
        total_pages = payload.get("total_pages") if isinstance(payload, dict) else None
        return devices, int(total_pages) if total_pages is not None else None

    async def resolve_accessible_devices(self) -> list[dict[str, Any]]:
        if not settings.DEVICE_SERVICE_URL:
            raise RuntimeError("DEVICE_SERVICE_URL is not configured")

        tenant_id = self._ctx.require_tenant()
        page_size = 100
        devices: list[dict[str, Any]] = []
        seen_device_ids: set[str] = set()
        requested_plant_ids: list[str | None]

        if self._is_plant_scoped_role():
            requested_plant_ids = [plant_id for plant_id in self._ctx.plant_ids if plant_id]
            if not requested_plant_ids:
                return []
        else:
            requested_plant_ids = [None]

        async with httpx.AsyncClient(timeout=30.0) as client:
            for plant_id in requested_plant_ids:
                page = 1
                while True:
                    page_devices, total_pages = await self._fetch_devices_page(
                        client,
                        tenant_id=tenant_id,
                        page=page,
                        page_size=page_size,
                        plant_id=plant_id,
                    )
                    for device in page_devices:
                        device_id = str(device.get("device_id") or "").strip()
                        if not device_id or device_id in seen_device_ids:
                            continue
                        if not self._device_is_accessible(device):
                            continue
                        seen_device_ids.add(device_id)
                        devices.append(device)

                    if total_pages is not None:
                        if page >= total_pages:
                            break
                    elif len(page_devices) < page_size:
                        break
                    page += 1

        return devices

    async def resolve_accessible_device_ids(self) -> list[str]:
        devices = await self.resolve_accessible_devices()
        return [str(device["device_id"]) for device in devices if device.get("device_id")]

    async def validate_accessible_device(self, device_id: str) -> dict[str, Any]:
        if not settings.DEVICE_SERVICE_URL:
            raise RuntimeError("DEVICE_SERVICE_URL is not configured")

        tenant_id = self._ctx.require_tenant()
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{settings.DEVICE_SERVICE_URL}/api/v1/devices/{device_id}",
                params={"tenant_id": tenant_id},
                headers=self._headers(),
            )

        if response.status_code == 404:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "DEVICE_NOT_FOUND",
                    "message": f"Device '{device_id}' not found. Please verify the device ID.",
                },
            )
        response.raise_for_status()

        payload = response.json()
        device = payload.get("data", payload) if isinstance(payload, dict) else payload
        if not isinstance(device, dict):
            raise RuntimeError("Unexpected device-service response while validating report device.")

        if not self._device_is_accessible(device):
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "DEVICE_NOT_FOUND",
                    "message": f"Device '{device_id}' not found. Please verify the device ID.",
                },
            )
        return device
