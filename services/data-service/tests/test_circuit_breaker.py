from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from httpx import ASGITransport
from fastapi.encoders import jsonable_encoder

from src.main import app
from src.models import OutboxTarget
from src.services.telemetry_service import TelemetryPayload, TelemetryService
from src.utils.circuit_breaker import (
    STATE_CLOSED,
    STATE_HALF_OPEN,
    STATE_OPEN,
    _BREAKERS,
    get_circuit_breaker_metrics,
    get_or_create_circuit_breaker,
)


def _request_error(url: str = "http://downstream.local") -> httpx.RequestError:
    return httpx.RequestError("boom", request=httpx.Request("GET", url))


def _telemetry_payload(device_id: str = "DEVICE-CB-1") -> dict[str, object]:
    return {
        "device_id": device_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "schema_version": "v1",
        "power": 110.0,
        "current": 0.8,
        "voltage": 229.0,
    }


@pytest.fixture(autouse=True)
def reset_breakers():
    _BREAKERS.clear()
    yield
    _BREAKERS.clear()


@pytest.mark.asyncio
async def test_circuit_opens_after_threshold():
    breaker = get_or_create_circuit_breaker("device-service", failure_threshold=5, open_timeout_sec=30)

    async def _fail():
        raise _request_error()

    for _ in range(5):
        success, result = await breaker.call(_fail)
        assert success is False
        assert result is None

    assert breaker.get_state() == STATE_OPEN


@pytest.mark.asyncio
async def test_open_circuit_returns_immediately():
    breaker = get_or_create_circuit_breaker("device-service", failure_threshold=1, open_timeout_sec=30)

    async def _fail():
        raise _request_error()

    await breaker.call(_fail)
    request_mock = AsyncMock(return_value="ok")

    success, result = await breaker.call(request_mock)

    assert success is False
    assert result is None
    request_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_half_open_after_timeout():
    breaker = get_or_create_circuit_breaker("device-service", failure_threshold=1, open_timeout_sec=1)

    async def _fail():
        raise _request_error()

    await breaker.call(_fail)
    breaker._opened_at_monotonic = time.monotonic() - 2

    assert breaker.get_state() == STATE_HALF_OPEN


@pytest.mark.asyncio
async def test_closes_after_success_in_half_open():
    breaker = get_or_create_circuit_breaker(
        "device-service",
        failure_threshold=1,
        success_threshold=2,
        open_timeout_sec=1,
    )

    async def _fail():
        raise _request_error()

    async def _success():
        return {"ok": True}

    await breaker.call(_fail)
    breaker._opened_at_monotonic = time.monotonic() - 2

    success, _ = await breaker.call(_success)
    assert success is True
    assert breaker.get_state() == STATE_HALF_OPEN

    success, _ = await breaker.call(_success)
    assert success is True
    assert breaker.get_state() == STATE_CLOSED


@pytest.mark.asyncio
async def test_reopens_on_failure_in_half_open():
    breaker = get_or_create_circuit_breaker("device-service", failure_threshold=1, open_timeout_sec=1)

    async def _fail():
        raise _request_error()

    await breaker.call(_fail)
    breaker._opened_at_monotonic = time.monotonic() - 2

    success, result = await breaker.call(_fail)

    assert success is False
    assert result is None
    assert breaker.get_state() == STATE_OPEN


class FakeInfluxRepository:
    def write_telemetry(self, payload) -> bool:
        return True

    def close(self) -> None:
        return None


class FakeOutboxRepository:
    def __init__(self) -> None:
        self.rows: list[dict[str, object]] = []

    async def ensure_schema(self) -> None:
        return None

    async def enqueue_telemetry(
        self,
        *,
        device_id: str,
        telemetry_payload: dict[str, object],
        targets,
        max_retries: int | None = None,
        session=None,
    ):
        target_list = list(targets)
        for target in target_list:
            self.rows.append(
                {
                    "device_id": device_id,
                    "target": target,
                    "telemetry_payload": telemetry_payload,
                    "max_retries": max_retries,
                }
            )
        return list(self.rows)

    async def close(self) -> None:
        return None


class FakeEnrichmentService:
    async def enrich_telemetry(self, payload):
        return payload

    async def close(self) -> None:
        return None


class FakeRuleEngineClient:
    async def evaluate_rules(self, payload, projection_state=None) -> None:
        return None

    async def close(self) -> None:
        return None


class FakeProjectionClient:
    async def sync_projection(self, payload):
        raise RuntimeError("projection unavailable")

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_open_circuit_writes_to_outbox(monkeypatch):
    breaker = get_or_create_circuit_breaker("device-service", failure_threshold=1, open_timeout_sec=30)

    async def _fail():
        raise _request_error()

    await breaker.call(_fail)
    assert breaker.get_state() == STATE_OPEN

    outbox_repository = FakeOutboxRepository()
    dlq_repository = MagicMock()
    dlq_repository.get_operational_stats.return_value = {}
    telemetry_service = TelemetryService(
        influx_repository=FakeInfluxRepository(),
        dlq_repository=dlq_repository,
        outbox_repository=outbox_repository,
        enrichment_service=FakeEnrichmentService(),
        rule_engine_client=FakeRuleEngineClient(),
        device_projection_client=FakeProjectionClient(),
    )

    async def _noop_broadcast(*args, **kwargs):
        return None

    monkeypatch.setattr("src.api.websocket.broadcast_telemetry", _noop_broadcast)

    await telemetry_service.start()
    try:
        raw_payload = _telemetry_payload("DEVICE-CB-OUTBOX")
        payload = TelemetryPayload(**raw_payload)
        await telemetry_service._process_telemetry_async(  # noqa: SLF001
            payload=payload,
            correlation_id="cb-open-outbox",
            raw_payload=raw_payload,
        )
        await telemetry_service._process_derived_async(  # noqa: SLF001
            payload=payload,
            correlation_id="cb-open-outbox",
            outbox_payload=jsonable_encoder(payload.model_dump(mode="json")),
            projection_synced=False,
            projection_state=None,
            projection_error="projection unavailable",
        )
    finally:
        await telemetry_service.close()

    assert len(outbox_repository.rows) == 1
    targets = {row["target"] for row in outbox_repository.rows}
    assert targets == {OutboxTarget.ENERGY_SERVICE}


@pytest.mark.asyncio
async def test_health_endpoint_includes_circuit_state():
    get_or_create_circuit_breaker("device-service")
    get_or_create_circuit_breaker("energy-service")
    get_or_create_circuit_breaker("rule-engine-service")

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert "circuit_breakers" in payload
    assert set(get_circuit_breaker_metrics()).issubset(set(payload["circuit_breakers"]))
