from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from src.config import settings
from services.shared.tenant_context import build_internal_headers
from services.shared.tariff_client import fetch_tenant_tariff

logger = logging.getLogger(__name__)


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


_device_client: httpx.AsyncClient | None = None
_energy_client: httpx.AsyncClient | None = None
_reporting_client: httpx.AsyncClient | None = None


def get_device_http_client() -> httpx.AsyncClient:
    global _device_client
    if _device_client is None or _device_client.is_closed:
        _device_client = httpx.AsyncClient(timeout=15.0)
    return _device_client


def get_energy_http_client() -> httpx.AsyncClient:
    global _energy_client
    if _energy_client is None or _energy_client.is_closed:
        _energy_client = httpx.AsyncClient(timeout=20.0)
    return _energy_client


def get_reporting_http_client() -> httpx.AsyncClient:
    global _reporting_client
    if _reporting_client is None or _reporting_client.is_closed:
        _reporting_client = httpx.AsyncClient(timeout=12.0)
    return _reporting_client


async def close_shared_http_clients() -> None:
    for client_attr in ("_device_client", "_energy_client", "_reporting_client"):
        client = globals().get(client_attr)
        if client is not None and not client.is_closed:
            await client.aclose()
        globals()[client_attr] = None


@dataclass
class TariffSnapshot:
    rate: Optional[float]
    currency: str
    configured: bool
    stale: bool = False


class TariffCache:
    def __init__(self):
        self._snapshot: dict[str | None, TariffSnapshot] = {}
        self._expires_at: dict[str | None, float] = {}

    async def get(self, tenant_id: str | None) -> TariffSnapshot:
        now = time.time()
        snapshot = self._snapshot.get(tenant_id)
        expires_at = self._expires_at.get(tenant_id, 0.0)
        if snapshot and now < expires_at:
            return snapshot

        try:
            client = get_reporting_http_client()
            payload = await fetch_tenant_tariff(
                client,
                settings.REPORTING_SERVICE_URL,
                tenant_id,
                service_name="waste-analysis-service",
            )
            rate = payload.get("rate")
            currency = (payload.get("currency") or "INR").upper()
            configured = bool(payload.get("configured"))
            snapshot = TariffSnapshot(
                rate=float(rate) if rate is not None else None,
                currency=currency,
                configured=configured,
            )
            self._snapshot[tenant_id] = snapshot
            self._expires_at[tenant_id] = now + max(1, settings.TARIFF_CACHE_TTL_SECONDS)
            return snapshot
        except Exception as exc:  # pragma: no cover
            logger.warning("tariff_fetch_failed error=%s", exc)
            if snapshot:
                return TariffSnapshot(
                    rate=snapshot.rate,
                    currency=snapshot.currency,
                    configured=snapshot.configured,
                    stale=True,
                )
            return TariffSnapshot(rate=None, currency="INR", configured=False, stale=True)


class DeviceClient:
    async def list_devices(self, tenant_id: str | None = None) -> list[dict[str, Any]]:
        client = get_device_http_client()
        headers = build_internal_headers("waste-analysis-service", tenant_id)
        resp = await client.get(
            f"{settings.DEVICE_SERVICE_URL}/api/v1/devices",
            params={"tenant_id": tenant_id} if tenant_id else None,
            headers=headers,
            timeout=30.0,
        )
        if resp.status_code != 200:
            return []
        payload = resp.json()
        return payload if isinstance(payload, list) else payload.get("data", [])

    async def get_device(self, device_id: str, tenant_id: str | None = None) -> Optional[dict[str, Any]]:
        client = get_device_http_client()
        headers = build_internal_headers("waste-analysis-service", tenant_id)
        resp = await client.get(
            f"{settings.DEVICE_SERVICE_URL}/api/v1/devices/{device_id}",
            params={"tenant_id": tenant_id} if tenant_id else None,
            headers=headers,
        )
        if resp.status_code != 200:
            return None
        payload = resp.json()
        if isinstance(payload, dict):
            return payload.get("data", payload)
        return None

    async def get_shift_config(self, device_id: str, tenant_id: str | None = None) -> list[dict[str, Any]]:
        client = get_device_http_client()
        headers = build_internal_headers("waste-analysis-service", tenant_id)
        resp = await client.get(
            f"{settings.DEVICE_SERVICE_URL}/api/v1/devices/{device_id}/shifts",
            params={"tenant_id": tenant_id} if tenant_id else None,
            headers=headers,
        )
        if resp.status_code != 200:
            return []
        payload = resp.json()
        return payload.get("data", []) if isinstance(payload, dict) else []

    async def get_idle_config(self, device_id: str, tenant_id: str | None = None) -> dict[str, Any]:
        client = get_device_http_client()
        headers = build_internal_headers("waste-analysis-service", tenant_id)
        resp = await client.get(
            f"{settings.DEVICE_SERVICE_URL}/api/v1/devices/{device_id}/idle-config",
            params={"tenant_id": tenant_id} if tenant_id else None,
            headers=headers,
        )
        if resp.status_code != 200:
            return {}
        payload = resp.json()
        cfg = payload.get("data", payload) if isinstance(payload, dict) else {}
        if not isinstance(cfg, dict):
            return {}
        return {
            "device_id": cfg.get("device_id") or device_id,
            "configured": bool(cfg.get("configured")),
            "full_load_current_a": _to_float(cfg.get("full_load_current_a")),
            "idle_threshold_pct_of_fla": _to_float(cfg.get("idle_threshold_pct_of_fla")),
            "derived_idle_threshold_a": _to_float(
                cfg.get("derived_idle_threshold_a") or cfg.get("idle_current_threshold")
            ),
            "derived_overconsumption_threshold_a": _to_float(cfg.get("derived_overconsumption_threshold_a")),
            "idle_current_threshold": _to_float(cfg.get("idle_current_threshold")),
        }

    async def get_waste_config(self, device_id: str, tenant_id: str | None = None) -> dict[str, Any]:
        client = get_device_http_client()
        headers = build_internal_headers("waste-analysis-service", tenant_id)
        resp = await client.get(
            f"{settings.DEVICE_SERVICE_URL}/api/v1/devices/{device_id}/waste-config",
            params={"tenant_id": tenant_id} if tenant_id else None,
            headers=headers,
        )
        if resp.status_code != 200:
            return {}
        payload = resp.json()
        cfg = payload.get("data", payload) if isinstance(payload, dict) else {}
        if not isinstance(cfg, dict):
            return {}
        return {
            **cfg,
            "full_load_current_a": _to_float(cfg.get("full_load_current_a")),
            "idle_threshold_pct_of_fla": _to_float(cfg.get("idle_threshold_pct_of_fla")),
            "derived_idle_threshold_a": _to_float(
                cfg.get("derived_idle_threshold_a") or cfg.get("idle_current_threshold")
            ),
            "derived_overconsumption_threshold_a": _to_float(
                cfg.get("derived_overconsumption_threshold_a")
                or cfg.get("overconsumption_current_threshold_a")
            ),
            "idle_current_threshold": _to_float(cfg.get("idle_current_threshold")),
            "overconsumption_current_threshold_a": _to_float(cfg.get("overconsumption_current_threshold_a")),
        }

    async def get_site_waste_config(self, tenant_id: str | None = None) -> dict[str, Any]:
        client = get_device_http_client()
        headers = build_internal_headers("waste-analysis-service", tenant_id)
        resp = await client.get(
            f"{settings.DEVICE_SERVICE_URL}/api/v1/settings/waste-config",
            params={"tenant_id": tenant_id} if tenant_id else None,
            headers=headers,
        )
        if resp.status_code != 200:
            return {}
        payload = resp.json()
        return payload.get("data", payload) if isinstance(payload, dict) else {}


class EnergyClient:
    async def get_device_range(
        self,
        device_id: str,
        start_date: str,
        end_date: str,
        tenant_id: str | None = None,
    ) -> Optional[dict[str, Any]]:
        client = get_energy_http_client()
        headers = build_internal_headers("waste-analysis-service", tenant_id)
        resp = await client.get(
            f"{settings.ENERGY_SERVICE_URL}/api/v1/energy/device/{device_id}/range",
            params={
                "start_date": start_date,
                "end_date": end_date,
                **({"tenant_id": tenant_id} if tenant_id else {}),
            },
            headers=headers,
        )
        if resp.status_code != 200:
            return None
        payload = resp.json()
        if not isinstance(payload, dict) or not payload.get("success"):
            return None
        return payload


tariff_cache = TariffCache()
device_client = DeviceClient()
energy_client = EnergyClient()
