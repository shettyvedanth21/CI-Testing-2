from __future__ import annotations

import pytest

from app.services.device_meta import DeviceMetaCache


class _Response:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


@pytest.mark.asyncio
async def test_device_meta_prefers_derived_threshold_contract(monkeypatch):
    cache = DeviceMetaCache()

    async def fake_breaker_call(fn):
        return True, await fn()

    async def fake_internal_get(_client, url, **_kwargs):
        if url.endswith("/api/v1/devices/DEV-1"):
            return _Response(
                200,
                {
                    "data": {
                        "device_name": "Press 1",
                        "energy_flow_mode": "consumption_only",
                        "polarity_mode": "normal",
                    }
                },
            )
        if url.endswith("/idle-config"):
            return _Response(
                200,
                {
                    "data": {
                        "full_load_current_a": 20.0,
                        "idle_threshold_pct_of_fla": 0.3,
                        "derived_idle_threshold_a": 6.0,
                        "idle_current_threshold": 999.0,
                    }
                },
            )
        if url.endswith("/waste-config"):
            return _Response(
                200,
                {
                    "data": {
                        "full_load_current_a": 20.0,
                        "derived_overconsumption_threshold_a": 20.0,
                        "overconsumption_current_threshold_a": 999.0,
                    }
                },
            )
        if url.endswith("/shifts"):
            return _Response(200, {"data": [{"day_of_week": 0, "shift_start": "09:00", "shift_end": "17:00"}]})
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(cache._breaker, "call", fake_breaker_call)
    monkeypatch.setattr("app.services.device_meta.internal_get", fake_internal_get)

    meta = await cache.get("DEV-1", tenant_id="tenant-1")

    assert meta["device_name"] == "Press 1"
    assert meta["full_load_current_a"] == 20.0
    assert meta["idle_threshold_pct_of_fla"] == 0.3
    assert meta["derived_idle_threshold_a"] == 6.0
    assert meta["derived_overconsumption_threshold_a"] == 20.0
    assert meta["idle_threshold"] == 6.0
    assert meta["over_threshold"] == 20.0
