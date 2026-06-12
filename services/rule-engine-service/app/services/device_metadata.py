from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.config import settings
from app.shared_http import get_internal_http_client
from services.shared.tenant_context import TenantContext, build_tenant_scoped_internal_headers


@dataclass(frozen=True)
class DeviceMetadata:
    device_id: str
    device_name: str | None
    device_location: str | None


class DeviceMetadataService:
    """Fetches device metadata required for user-facing alert enrichment."""

    def __init__(self, ctx: TenantContext):
        self._ctx = ctx

    async def get_device_metadata(self, device_id: str) -> DeviceMetadata:
        payload = await self._get_device(device_id)
        if not isinstance(payload, dict):
            return DeviceMetadata(device_id=device_id, device_name=None, device_location=None)
        return DeviceMetadata(
            device_id=device_id,
            device_name=self._extract_first_string(payload, ["device_name", "name"]),
            device_location=self._extract_first_string(payload, ["location", "device_location", "site_location"]),
        )

    async def _get_device(self, device_id: str) -> dict[str, Any] | None:
        base_url = (settings.DEVICE_SERVICE_URL or "").rstrip("/")
        if not base_url:
            return None

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

    @staticmethod
    def _extract_first_string(payload: dict[str, Any], keys: list[str]) -> str | None:
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str):
                cleaned = value.strip()
                if cleaned:
                    return cleaned
        return None
