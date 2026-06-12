from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any

import httpx

from app.config import settings
from app.utils.circuit_breaker import get_or_create_circuit_breaker
from app.services.internal_http import internal_get


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


class DeviceMetaCache:
    _TTL_SECONDS = 60.0

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._cache: dict[tuple[str | None, str], tuple[float, dict[str, Any]]] = {}
        self._breaker = get_or_create_circuit_breaker(
            "device-service",
            failure_threshold=settings.CIRCUIT_BREAKER_FAILURE_THRESHOLD,
            success_threshold=settings.CIRCUIT_BREAKER_SUCCESS_THRESHOLD,
            open_timeout_sec=settings.CIRCUIT_BREAKER_OPEN_TIMEOUT_SEC,
        )
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=2.5)
        return self._client

    def _evict_expired(self, now: float) -> None:
        expired_keys = [k for k, v in self._cache.items() if v[0] <= now]
        for k in expired_keys:
            del self._cache[k]

    async def get(self, device_id: str, tenant_id: str | None = None) -> dict[str, Any]:
        now = time.monotonic()
        cache_key = (tenant_id, device_id)
        cached = self._cache.get(cache_key)
        if cached and cached[0] > now:
            return cached[1]

        async with self._lock:
            now = time.monotonic()
            cached = self._cache.get(cache_key)
            if cached and cached[0] > now:
                return cached[1]

            self._evict_expired(now)

            data = {
                "full_load_current_a": None,
                "idle_threshold_pct_of_fla": None,
                "derived_idle_threshold_a": None,
                "derived_overconsumption_threshold_a": None,
                "idle_threshold": None,
                "over_threshold": None,
                "shifts": [],
                "device_name": device_id,
                "energy_flow_mode": "consumption_only",
                "polarity_mode": "normal",
            }
            try:
                client = await self._get_client()
                success, dev_r = await self._breaker.call(
                    lambda: internal_get(
                        client,
                        f"{settings.DEVICE_SERVICE_BASE_URL}/api/v1/devices/{device_id}",
                        service_name="energy-service",
                        tenant_id=tenant_id,
                    )
                )
                if not success or dev_r is None:
                    self._cache[cache_key] = (time.monotonic() + self._TTL_SECONDS, data)
                    return data
                if dev_r.status_code == 200:
                    dev_payload = dev_r.json()
                    dev_data = dev_payload.get("data", dev_payload)
                    if isinstance(dev_data, dict):
                        data["device_name"] = dev_data.get("device_name") or device_id
                        data["energy_flow_mode"] = dev_data.get("energy_flow_mode") or "consumption_only"
                        data["polarity_mode"] = dev_data.get("polarity_mode") or "normal"
                success, idle_r = await self._breaker.call(
                    lambda: internal_get(
                        client,
                        f"{settings.DEVICE_SERVICE_BASE_URL}/api/v1/devices/{device_id}/idle-config",
                        service_name="energy-service",
                        tenant_id=tenant_id,
                    )
                )
                if not success or idle_r is None:
                    self._cache[cache_key] = (time.monotonic() + self._TTL_SECONDS, data)
                    return data
                if idle_r.status_code == 200:
                    idle_payload = idle_r.json()
                    idle = idle_payload.get("data", idle_payload) if isinstance(idle_payload, dict) else {}
                    if isinstance(idle, dict):
                        data["full_load_current_a"] = _to_float(idle.get("full_load_current_a"))
                        data["idle_threshold_pct_of_fla"] = _to_float(idle.get("idle_threshold_pct_of_fla"))
                        data["derived_idle_threshold_a"] = _to_float(
                            idle.get("derived_idle_threshold_a") or idle.get("idle_current_threshold")
                        )
                        data["derived_overconsumption_threshold_a"] = _to_float(
                            idle.get("derived_overconsumption_threshold_a")
                            or idle.get("overconsumption_current_threshold_a")
                            or data["full_load_current_a"]
                        )
                        data["idle_threshold"] = data["derived_idle_threshold_a"]
                success, waste_r = await self._breaker.call(
                    lambda: internal_get(
                        client,
                        f"{settings.DEVICE_SERVICE_BASE_URL}/api/v1/devices/{device_id}/waste-config",
                        service_name="energy-service",
                        tenant_id=tenant_id,
                    )
                )
                if not success or waste_r is None:
                    self._cache[cache_key] = (time.monotonic() + self._TTL_SECONDS, data)
                    return data
                if waste_r.status_code == 200:
                    waste_payload = waste_r.json()
                    waste = waste_payload.get("data", waste_payload) if isinstance(waste_payload, dict) else {}
                    if isinstance(waste, dict):
                        data["full_load_current_a"] = (
                            _to_float(waste.get("full_load_current_a")) or data["full_load_current_a"]
                        )
                        data["idle_threshold_pct_of_fla"] = (
                            _to_float(waste.get("idle_threshold_pct_of_fla")) or data["idle_threshold_pct_of_fla"]
                        )
                        data["derived_overconsumption_threshold_a"] = _to_float(
                            waste.get("derived_overconsumption_threshold_a")
                            or waste.get("overconsumption_current_threshold_a")
                        ) or data["derived_overconsumption_threshold_a"]
                        data["over_threshold"] = data["derived_overconsumption_threshold_a"]
                success, shift_r = await self._breaker.call(
                    lambda: internal_get(
                        client,
                        f"{settings.DEVICE_SERVICE_BASE_URL}/api/v1/devices/{device_id}/shifts",
                        service_name="energy-service",
                        tenant_id=tenant_id,
                    )
                )
                if not success or shift_r is None:
                    self._cache[cache_key] = (time.monotonic() + self._TTL_SECONDS, data)
                    return data
                if shift_r.status_code == 200:
                    shift_payload = shift_r.json()
                    rows = shift_payload.get("data", shift_payload if isinstance(shift_payload, list) else [])
                    if isinstance(rows, list):
                        data["shifts"] = [s for s in rows if isinstance(s, dict) and s.get("is_active", True)]
            except Exception:
                pass

            self._cache[cache_key] = (time.monotonic() + self._TTL_SECONDS, data)
            return data


meta_cache = DeviceMetaCache()


def parse_ts(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=datetime.UTC)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=datetime.UTC)
