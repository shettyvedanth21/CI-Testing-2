from __future__ import annotations

from typing import Any

import httpx
from fastapi import HTTPException

from services.shared.tenant_context import TenantContext, build_tenant_scoped_internal_headers
from src.config.settings import get_settings


PLANT_SCOPED_ROLES = {"plant_manager", "operator", "viewer"}


class AnalyticsDeviceScopeService:
    def __init__(self, ctx: TenantContext):
        self._ctx = ctx
        self._settings = get_settings()

    def _is_plant_scoped_role(self) -> bool:
        return self._ctx.role in PLANT_SCOPED_ROLES

    def _headers(self) -> dict[str, str]:
        tenant_id = self._ctx.require_tenant()
        return build_tenant_scoped_internal_headers("analytics-service", tenant_id)

    def _device_is_accessible(self, device: dict[str, Any]) -> bool:
        if not self._is_plant_scoped_role():
            return True
        if not self._ctx.plant_ids:
            return False
        return str(device.get("plant_id") or "") in set(self._ctx.plant_ids)

    async def resolve_accessible_devices(self) -> list[dict[str, Any]]:
        if not self._settings.device_service_url:
            raise RuntimeError("device_service_url is not configured")

        tenant_id = self._ctx.require_tenant()
        page = 1
        page_size = 100
        devices: list[dict[str, Any]] = []

        async with httpx.AsyncClient(timeout=30.0) as client:
            while True:
                response = await client.get(
                    f"{self._settings.device_service_url}/api/v1/devices",
                    params={"tenant_id": tenant_id, "page": page, "page_size": page_size},
                    headers=self._headers(),
                )
                response.raise_for_status()
                payload = response.json()
                rows = payload if isinstance(payload, list) else payload.get("data", [])
                if not isinstance(rows, list):
                    raise RuntimeError("Unexpected device-service response while resolving analytics scope.")
                devices.extend([row for row in rows if isinstance(row, dict)])

                total_pages = payload.get("total_pages") if isinstance(payload, dict) else None
                if total_pages is not None:
                    if page >= int(total_pages):
                        break
                elif len(rows) < page_size:
                    break
                page += 1

        return [device for device in devices if self._device_is_accessible(device)]

    async def resolve_accessible_device_ids(self) -> list[str]:
        devices = await self.resolve_accessible_devices()
        return [str(device["device_id"]) for device in devices if device.get("device_id")]

    async def normalize_requested_device_ids(self, requested_device_ids: list[str]) -> list[str]:
        if not self._is_plant_scoped_role():
            normalized: list[str] = []
            seen: set[str] = set()
            for device_id in requested_device_ids:
                normalized_id = str(device_id)
                if not normalized_id or normalized_id in seen:
                    continue
                seen.add(normalized_id)
                normalized.append(normalized_id)
            if normalized:
                return normalized

        accessible_ids = await self.resolve_accessible_device_ids()
        if not accessible_ids and not requested_device_ids:
            return []
        if not accessible_ids:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "ANALYTICS_SCOPE_FORBIDDEN",
                    "message": "Fleet analytics can only run for devices inside your assigned plant scope.",
                },
            )

        if not requested_device_ids:
            return accessible_ids

        accessible_set = set(accessible_ids)
        normalized: list[str] = []
        seen: set[str] = set()
        for device_id in requested_device_ids:
            normalized_id = str(device_id)
            if normalized_id not in accessible_set:
                raise HTTPException(
                    status_code=403,
                    detail={
                        "error": "ANALYTICS_SCOPE_FORBIDDEN",
                        "message": "Fleet analytics can only run for devices inside your assigned plant scope.",
                    },
                )
            if normalized_id not in seen:
                seen.add(normalized_id)
                normalized.append(normalized_id)
        return normalized
