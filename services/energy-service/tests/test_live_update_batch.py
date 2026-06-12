from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

ROOT = Path(__file__).resolve().parents[1]
SERVICES_ROOT = ROOT.parent
sys.path = [p for p in sys.path if p not in {str(ROOT), str(SERVICES_ROOT)}]
sys.path.insert(0, str(SERVICES_ROOT))
sys.path.insert(0, str(ROOT))

from app.api import routes
from app.schemas import DeviceLifecycleRequest, LiveUpdateBatchRequest, LiveUpdateRequest


def _request(path: str = "/api/v1/energy/live-update/batch") -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": [(b"x-tenant-id", b"SH00000001"), (b"x-internal-service", b"data-service")],
    }
    return Request(scope)


@pytest.mark.asyncio
async def test_live_update_batch_isolates_invalid_rows(monkeypatch):
    publish_many_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(routes.energy_broadcaster, "publish_many", publish_many_mock)
    monkeypatch.setattr(routes, "resolve_request_tenant_id", lambda request, explicit_tenant_id=None: explicit_tenant_id or "SH00000001")

    class _FakeEngine:
        def __init__(self, _db):
            self.calls = []

        async def apply_live_updates_batch(self, *, tenant_id=None, updates=None):
            results = []
            for update in updates or []:
                telemetry = update["telemetry"]
                device_id = telemetry["device_id"]
                if device_id == "DEVICE-BAD":
                    results.append(
                        {
                            "success": False,
                            "device_id": device_id,
                            "error": "bad telemetry",
                            "error_code": "ENERGY_LIVE_UPDATE_ERROR",
                            "retryable": True,
                        }
                    )
                    continue
                results.append(
                    {
                        "success": True,
                        "device_id": device_id,
                        "data": {"device_id": device_id, "version": 1, "freshness_ts": telemetry["timestamp"]},
                        "retryable": False,
                    }
                )
            return results

    monkeypatch.setattr(routes, "EnergyEngine", _FakeEngine)

    payload = LiveUpdateBatchRequest(
        tenant_id="SH00000001",
        updates=[
            {"telemetry": {"device_id": "DEVICE-GOOD", "timestamp": "2026-04-17T00:00:00Z"}},
            {"telemetry": {"device_id": "DEVICE-BAD", "timestamp": "2026-04-17T00:00:01Z"}},
        ],
    )

    response = await routes.live_update_batch(_request(), payload, db=object())

    assert response["success"] is True
    assert response["results"][0]["success"] is True
    assert response["results"][0]["device_id"] == "DEVICE-GOOD"
    assert response["results"][1]["success"] is False
    assert response["results"][1]["device_id"] == "DEVICE-BAD"
    assert response["results"][1]["error_code"] == "ENERGY_LIVE_UPDATE_ERROR"
    publish_many_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_live_update_returns_503_on_optimistic_lock_exhaustion(monkeypatch):
    publish_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(routes.energy_broadcaster, "publish", publish_mock)
    monkeypatch.setattr(routes, "resolve_request_tenant_id", lambda request, explicit_tenant_id=None: explicit_tenant_id or "SH00000001")

    class _FakeEngine:
        def __init__(self, _db):
            pass

        async def apply_live_update(self, **_kwargs):
            return {
                "device_id": "DEVICE-1",
                "device_name": "Machine A",
                "ts": "2026-04-20T00:05:00+00:00",
                "delta_energy_kwh": 0.0,
                "delta_loss_kwh": 0.0,
                "quality_flags": ["optimistic_lock_skipped"],
                "energy_debug": {"energy_method": "counter", "quality_class": "medium", "reason_code": "ok", "algorithm_version": "1"},
                "version": 3,
                "freshness_ts": "2026-04-20T00:05:00+00:00",
                "idempotent_drop": True,
            }

    monkeypatch.setattr(routes, "EnergyEngine", _FakeEngine)

    payload = LiveUpdateRequest(
        telemetry={"device_id": "DEVICE-1", "timestamp": "2026-04-20T00:05:00+00:00", "power": 1200.0},
        tenant_id="SH00000001",
    )

    response = await routes.live_update(_request(path="/api/v1/energy/live-update"), payload, db=object())

    assert response.status_code == 503
    body = response.body if hasattr(response, "body") else {}
    import json
    content = json.loads(response.body)
    assert content["success"] is False
    assert content["error"] == "optimistic_lock_contention"
    assert content["retryable"] is True
    assert content["data"]["idempotent_drop"] is True
    assert content["data"]["delta_energy_kwh"] == 0.0
    publish_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_live_update_returns_200_on_success(monkeypatch):
    publish_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(routes.energy_broadcaster, "publish", publish_mock)
    monkeypatch.setattr(routes, "resolve_request_tenant_id", lambda request, explicit_tenant_id=None: explicit_tenant_id or "SH00000001")

    class _FakeEngine:
        def __init__(self, _db):
            pass

        async def apply_live_update(self, **_kwargs):
            return {
                "device_id": "DEVICE-1",
                "device_name": "Machine A",
                "ts": "2026-04-20T00:05:00+00:00",
                "delta_energy_kwh": 0.1,
                "delta_loss_kwh": 0.0,
                "quality_flags": [],
                "energy_debug": {"energy_method": "counter", "quality_class": "high", "reason_code": "ok", "algorithm_version": "1"},
                "version": 4,
                "freshness_ts": "2026-04-20T00:05:00+00:00",
            }

    monkeypatch.setattr(routes, "EnergyEngine", _FakeEngine)

    payload = LiveUpdateRequest(
        telemetry={"device_id": "DEVICE-1", "timestamp": "2026-04-20T00:05:00+00:00", "power": 1200.0},
        tenant_id="SH00000001",
    )

    response = await routes.live_update(_request(path="/api/v1/energy/live-update"), payload, db=object())

    assert response["success"] is True
    assert response["data"]["delta_energy_kwh"] == 0.1
    publish_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_device_lifecycle_passes_request_tenant_scope(monkeypatch):
    publish_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(routes.energy_broadcaster, "publish", publish_mock)
    monkeypatch.setattr(routes, "get_tenant_id", lambda _request: "SH00000001")

    captured = {}

    class _FakeEngine:
        def __init__(self, _db):
            pass

        async def apply_device_lifecycle(self, **kwargs):
            captured.update(kwargs)
            return {
                "device_id": kwargs["device_id"],
                "session_state": kwargs["status"],
                "version": 2,
            }

    monkeypatch.setattr(routes, "EnergyEngine", _FakeEngine)

    payload = DeviceLifecycleRequest(status="stopped")
    response = await routes.device_lifecycle("DEVICE-1", payload, _request(path="/api/v1/energy/device-lifecycle/DEVICE-1"), db=object())

    assert response["success"] is True
    assert captured["tenant_id"] == "SH00000001"
    assert captured["device_id"] == "DEVICE-1"
    assert captured["status"] == "stopped"
    publish_mock.assert_awaited_once()
