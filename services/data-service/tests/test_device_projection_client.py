from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from tests._bootstrap import bootstrap_paths

bootstrap_paths()

from src.models import TelemetryPayload
from src.services.device_projection_client import DeviceProjectionClient, DeviceProjectionSyncError
from src.utils.circuit_breaker import STATE_CLOSED, STATE_OPEN, _BREAKERS, get_or_create_circuit_breaker


def _payload(device_id: str = "DEVICE-PROJ-1") -> TelemetryPayload:
    return TelemetryPayload(
        device_id=device_id,
        tenant_id="TENANT-A",
        timestamp=datetime.now(timezone.utc),
        schema_version="v1",
        power=120.0,
        current=0.8,
        voltage=230.0,
    )


@pytest.fixture(autouse=True)
def reset_breakers():
    _BREAKERS.clear()
    yield
    _BREAKERS.clear()


@pytest.mark.asyncio
async def test_partial_batch_success_does_not_open_circuit(monkeypatch):
    client = DeviceProjectionClient(base_url="http://device-service", timeout=1.0)
    client.circuit_breaker = get_or_create_circuit_breaker(
        "device-service-live-update",
        failure_threshold=1,
        open_timeout_sec=30,
    )

    async def _fake_send(_tenant_id: str, _request_body: dict):
        return httpx.Response(
            200,
            json={
                "success": True,
                "results": [
                    {"device_id": "DEVICE-PROJ-1", "success": True, "device": {"version": 1}, "retryable": False},
                    {
                        "device_id": "DEVICE-PROJ-2",
                        "success": False,
                        "error": "phase_type must be 'single', 'three', or null",
                        "error_code": "INVALID_DEVICE_METADATA",
                        "retryable": False,
                    },
                ],
            },
            request=httpx.Request("POST", "http://device-service/api/v1/devices/live-update/batch"),
        )

    monkeypatch.setattr(client, "_send_projection_batch_request", _fake_send)

    results = await client.sync_projection_batch([_payload("DEVICE-PROJ-1"), _payload("DEVICE-PROJ-2")])

    assert results[0]["success"] is True
    assert results[1]["error_code"] == "INVALID_DEVICE_METADATA"
    assert client.circuit_breaker.get_state() == STATE_CLOSED
    await client.close()


@pytest.mark.asyncio
async def test_client_error_is_nonretryable_and_does_not_open_circuit(monkeypatch):
    client = DeviceProjectionClient(base_url="http://device-service", timeout=1.0)
    client.circuit_breaker = get_or_create_circuit_breaker(
        "device-service-live-update",
        failure_threshold=1,
        open_timeout_sec=30,
    )

    async def _fake_send(_tenant_id: str, _request_body: dict):
        return httpx.Response(
            422,
            json={"code": "INVALID_DEVICE_METADATA"},
            request=httpx.Request("POST", "http://device-service/api/v1/devices/live-update/batch"),
        )

    monkeypatch.setattr(client, "_send_projection_batch_request", _fake_send)

    with pytest.raises(DeviceProjectionSyncError) as exc:
        await client.sync_projection_batch([_payload()])

    assert exc.value.retryable is False
    assert exc.value.category == "invalid_device_metadata"
    assert client.circuit_breaker.get_state() == STATE_CLOSED
    await client.close()


@pytest.mark.asyncio
async def test_overload_response_opens_circuit_for_retryable_failures(monkeypatch):
    client = DeviceProjectionClient(base_url="http://device-service", timeout=1.0)
    client.circuit_breaker.failure_threshold = 1
    client.circuit_breaker.open_timeout_sec = 30

    async def _fake_send(_tenant_id: str, _request_body: dict):
        return httpx.Response(
            503,
            json={"code": "OVERLOADED"},
            request=httpx.Request("POST", "http://device-service/api/v1/devices/live-update/batch"),
        )

    monkeypatch.setattr(client, "_send_projection_batch_request", _fake_send)

    with pytest.raises(DeviceProjectionSyncError) as exc:
        await client.sync_projection_batch([_payload()])

    assert exc.value.retryable is True
    assert exc.value.category == "downstream_overload"
    assert client.circuit_breaker.get_state() == STATE_OPEN
    await client.close()


@pytest.mark.asyncio
async def test_transport_timeout_is_retryable_and_classified(monkeypatch):
    client = DeviceProjectionClient(base_url="http://device-service", timeout=0.1)
    calls = {"count": 0}

    async def _fake_send(_tenant_id: str, _request_body: dict):
        calls["count"] += 1
        raise httpx.ReadTimeout("timed out", request=httpx.Request("POST", "http://device-service/api/v1/devices/live-update/batch"))

    monkeypatch.setattr(client, "_send_projection_batch_request", _fake_send)

    with pytest.raises(DeviceProjectionSyncError) as exc:
        await client.sync_projection_batch([_payload()])

    assert calls["count"] == 1
    assert exc.value.retryable is True
    assert exc.value.code == "DEVICE_PROJECTION_TRANSPORT_ERROR"
    assert exc.value.category == "transient_dependency_failure"
    assert "device_projection_transport_error" in str(exc.value)
    await client.close()
