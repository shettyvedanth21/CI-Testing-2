from __future__ import annotations

from typing import Any

import httpx

from app.config import settings
from app.shared_http import get_internal_http_client
from services.shared.tenant_context import TenantContext, build_tenant_scoped_internal_headers


PLANT_SCOPED_ROLES = {"plant_manager", "operator", "viewer"}


class DeviceScopeService:
    def __init__(self, ctx: TenantContext):
        self._ctx = ctx

    def requires_device_scope(self) -> bool:
        return self._ctx.role in PLANT_SCOPED_ROLES

    async def resolve_accessible_device_ids(self) -> list[str] | None:
        if not self.requires_device_scope():
            return None
        if not self._ctx.plant_ids:
            return []
        devices = await self._list_scoped_devices()
        return [str(device.get("device_id")) for device in devices if device.get("device_id")]

    async def device_is_accessible(self, device_id: str) -> bool:
        if not self.requires_device_scope():
            return True
        if not self._ctx.plant_ids:
            return False
        device = await self._get_device(device_id)
        if not isinstance(device, dict):
            return False
        return device.get("plant_id") in set(self._ctx.plant_ids)

    async def _fetch_devices_page(
        self,
        client: httpx.AsyncClient,
        *,
        page: int,
        page_size: int,
        plant_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        base_url = (settings.DEVICE_SERVICE_URL or "").rstrip("/")
        if not base_url:
            raise RuntimeError("DEVICE_SERVICE_URL is not configured")

        tenant_id = self._ctx.require_tenant()
        headers = build_tenant_scoped_internal_headers("rule-engine-service", tenant_id)
        params: dict[str, Any] = {"page": page, "page_size": page_size}
        if plant_id:
            params["plant_id"] = plant_id
        response = await client.get(
            f"{base_url}/api/v1/devices",
            params=params,
            headers=headers,
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("data", []) if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            raise RuntimeError("Device service returned an unexpected response")
        total_pages = int(payload.get("total_pages") or 1) if isinstance(payload, dict) else 1
        return [row for row in rows if isinstance(row, dict)], total_pages

    async def _list_scoped_devices(self) -> list[dict[str, Any]]:
        base_url = (settings.DEVICE_SERVICE_URL or "").rstrip("/")
        if not base_url:
            raise RuntimeError("DEVICE_SERVICE_URL is not configured")

        page_size = 100
        devices: list[dict[str, Any]] = []
        seen_device_ids: set[str] = set()

        client = get_internal_http_client()
        for plant_id in self._ctx.plant_ids:
            page = 1
            while True:
                rows, total_pages = await self._fetch_devices_page(
                    client,
                    page=page,
                    page_size=page_size,
                    plant_id=plant_id,
                )
                for device in rows:
                    device_id = str(device.get("device_id") or "").strip()
                    if not device_id or device_id in seen_device_ids:
                        continue
                    seen_device_ids.add(device_id)
                    devices.append(device)
                if page >= total_pages:
                    break
                page += 1

        return devices

    async def _get_device(self, device_id: str) -> dict[str, Any] | None:
        base_url = (settings.DEVICE_SERVICE_URL or "").rstrip("/")
        if not base_url:
            raise RuntimeError("DEVICE_SERVICE_URL is not configured")

        tenant_id = self._ctx.require_tenant()
        headers = build_tenant_scoped_internal_headers("rule-engine-service", tenant_id)

        client = get_internal_http_client()
        response = await client.get(
            f"{base_url}/api/v1/devices/{device_id}",
            headers=headers,
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict):
            data = payload.get("data", payload)
            return data if isinstance(data, dict) else None
        return None
