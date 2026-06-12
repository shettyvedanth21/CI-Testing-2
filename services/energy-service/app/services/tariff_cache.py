from __future__ import annotations

import asyncio
import time
from typing import Any

from app.config import settings
from app.utils.circuit_breaker import get_or_create_circuit_breaker
from services.shared.tariff_client import fetch_tenant_tariff

import httpx


class TariffCache:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._expires_at: dict[str | None, float] = {}
        self._snapshot: dict[str | None, dict[str, Any]] = {
            None: {"rate": 0.0, "currency": "INR", "configured": False}
        }
        self._breaker = get_or_create_circuit_breaker(
            "reporting-service",
            failure_threshold=settings.CIRCUIT_BREAKER_FAILURE_THRESHOLD,
            success_threshold=settings.CIRCUIT_BREAKER_SUCCESS_THRESHOLD,
            open_timeout_sec=settings.CIRCUIT_BREAKER_OPEN_TIMEOUT_SEC,
        )
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=3.0)
        return self._client

    def _evict_expired(self, now: float) -> None:
        expired_keys = [k for k, v in self._expires_at.items() if v <= now]
        for k in expired_keys:
            del self._expires_at[k]
            self._snapshot.pop(k, None)

    async def get(self, tenant_id: str | None = None) -> dict[str, Any]:
        now = time.monotonic()
        expires_at = self._expires_at.get(tenant_id, 0.0)
        if now < expires_at:
            return dict(self._snapshot.get(tenant_id) or self._snapshot[None])

        async with self._lock:
            now = time.monotonic()
            expires_at = self._expires_at.get(tenant_id, 0.0)
            if now < expires_at:
                return dict(self._snapshot.get(tenant_id) or self._snapshot[None])

            self._evict_expired(now)

            previous_snapshot = self._snapshot.get(tenant_id)
            had_previous = previous_snapshot is not None

            try:
                client = await self._get_client()
                async def _get_tariff():
                    payload = await fetch_tenant_tariff(
                        client,
                        settings.REPORTING_SERVICE_BASE_URL,
                        tenant_id,
                        service_name="energy-service",
                    )
                    return payload

                success, response = await self._breaker.call(
                    _get_tariff
                )
                if not success or response is None:
                    raise RuntimeError("reporting-service circuit open or request failed")
                payload = response
                rate = float(payload.get("rate") or 0.0)
                currency = str(payload.get("currency") or "INR")
                self._snapshot[tenant_id] = {
                    "rate": rate,
                    "currency": currency,
                    "configured": bool(payload.get("configured", rate > 0)),
                    "source": payload.get("source"),
                    "version_id": payload.get("version_id"),
                    "effective_start_at": payload.get("effective_start_at"),
                    "effective_end_at": payload.get("effective_end_at"),
                }
                self._expires_at[tenant_id] = time.monotonic() + max(1, settings.TARIFF_CACHE_TTL_SECONDS)
            except Exception:
                if had_previous:
                    self._expires_at[tenant_id] = time.monotonic() + max(1, settings.TARIFF_CACHE_TTL_SECONDS)
                else:
                    self._expires_at[tenant_id] = time.monotonic() + max(1, min(15, settings.TARIFF_CACHE_TTL_SECONDS))

            return dict(self._snapshot.get(tenant_id) or self._snapshot[None])


tariff_cache = TariffCache()
