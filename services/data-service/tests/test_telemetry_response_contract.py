from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from starlette.middleware.base import BaseHTTPMiddleware

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SERVICES_DIR = PROJECT_ROOT / "services"
for path in (PROJECT_ROOT, SERVICES_DIR):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

os.environ.setdefault("JWT_SECRET_KEY", "test-secret")
os.environ.setdefault("INTERNAL_SERVICE_SHARED_SECRET", "test-secret")

from src.api.routes import create_router, get_telemetry_service
from src.models import EnrichmentStatus, TelemetryHistoryTimeoutError, TelemetryPoint, TelemetryQuery
from services.shared.tenant_context import TenantContext, build_internal_headers


class _TelemetryContractServiceStub:
    async def get_telemetry(self, **_: object):
        return [
            TelemetryPoint(
                timestamp=datetime(2026, 4, 8, tzinfo=timezone.utc),
                device_id="DEVICE-1",
                schema_version="v1",
                enrichment_status=EnrichmentStatus.PENDING,
                power=123.4,
                tenant_id="tenant-a",
                table=7,
                debug_marker="ignore-me",
            )
        ]

    async def get_latest(self, **_: object):
        return TelemetryPoint(
            timestamp=datetime(2026, 4, 8, 1, tzinfo=timezone.utc),
            device_id="DEVICE-1",
            power=111.1,
            tenant_id="tenant-a",
            table=3,
        )

    async def get_latest_batch(self, **_: object):
        return {
            "DEVICE-1": TelemetryPoint(
                timestamp=datetime(2026, 4, 8, 2, tzinfo=timezone.utc),
                device_id="DEVICE-1",
                current=9.5,
                tenant_id="tenant-a",
                enriched_at="2026-04-08T02:00:00Z",
            )
        }

    async def get_earliest(self, **_: object):
        return TelemetryPoint(
            timestamp=datetime(2026, 4, 7, 2, tzinfo=timezone.utc),
            device_id="DEVICE-1",
            power=99.1,
            tenant_id="tenant-a",
        )


class _TelemetryHistoryTimeoutServiceStub(_TelemetryContractServiceStub):
    async def get_telemetry(self, **_: object):
        raise TelemetryHistoryTimeoutError()


def _build_test_app(service: object) -> FastAPI:
    app = FastAPI()
    app.include_router(create_router())
    app.dependency_overrides[get_telemetry_service] = lambda: service

    class _InjectContextMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            tenant_id = request.headers.get("X-Tenant-Id") or "tenant-a"
            request.state.tenant_context = TenantContext(
                tenant_id=tenant_id,
                user_id="contract-test",
                role="internal_service",
                plant_ids=[],
                is_super_admin=False,
            )
            request.state.tenant_id = tenant_id
            request.state.tenant_id = tenant_id
            request.state.role = "internal_service"
            request.state.plant_ids = []
            return await call_next(request)

    app.add_middleware(_InjectContextMiddleware)
    return app


def test_telemetry_point_api_dict_keeps_only_contract_fields() -> None:
    point = TelemetryPoint(
        timestamp=datetime(2026, 4, 8, tzinfo=timezone.utc),
        device_id="DEVICE-1",
        power=123.4,
        current=9.8,
        tenant_id="tenant-a",
        table=1,
        debug_marker="ignore-me",
    )

    payload = point.to_api_dict()

    assert payload["device_id"] == "DEVICE-1"
    assert payload["schema_version"] == "v1"
    assert payload["enrichment_status"] == EnrichmentStatus.PENDING
    assert payload["power"] == 123.4
    assert payload["current"] == 9.8
    assert "tenant_id" not in payload
    assert "table" not in payload
    assert "debug_marker" not in payload


def test_telemetry_point_api_dict_preserves_phase_diagnostics() -> None:
    point = TelemetryPoint(
        timestamp=datetime(2026, 4, 8, tzinfo=timezone.utc),
        device_id="DEVICE-1",
        current=10.0,
        voltage=230.0,
        current_l1=999.0,
        current_l2=0.0,
        current_l3=-999.0,
        voltage_l1=500.0,
        voltage_l2=0.0,
        voltage_l3=-100.0,
        power_l1=100.0,
        power_factor_l1=0.5,
    )

    payload = point.to_api_dict()

    assert payload["current"] == 10.0
    assert payload["voltage"] == 230.0
    assert payload["current_l1"] == 999.0
    assert payload["current_l2"] == 0.0
    assert payload["current_l3"] == -999.0
    assert payload["voltage_l1"] == 500.0
    assert payload["voltage_l2"] == 0.0
    assert payload["voltage_l3"] == -100.0
    assert payload["power_l1"] == 100.0
    assert payload["power_factor_l1"] == 0.5


@pytest.mark.asyncio
async def test_routes_serialize_only_public_contract_fields() -> None:
    app = _build_test_app(_TelemetryContractServiceStub())

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers=build_internal_headers("contract-test", "tenant-a"),
    ) as client:
        telemetry_response = await client.get("/api/v1/data/telemetry/DEVICE-1")
        latest_response = await client.get("/api/v1/data/telemetry/DEVICE-1/latest")
        earliest_response = await client.get("/api/v1/data/telemetry/DEVICE-1/earliest")
        batch_response = await client.post(
            "/api/v1/data/telemetry/latest-batch",
            json={"device_ids": ["DEVICE-1"]},
        )

    assert telemetry_response.status_code == 200
    telemetry_item = telemetry_response.json()["data"]["items"][0]
    assert telemetry_item["schema_version"] == "v1"
    assert telemetry_item["enrichment_status"] == "pending"
    assert telemetry_item["power"] == 123.4
    assert "tenant_id" not in telemetry_item
    assert "table" not in telemetry_item
    assert "debug_marker" not in telemetry_item

    assert latest_response.status_code == 200
    latest_item = latest_response.json()["data"]["item"]
    assert latest_item["schema_version"] == "v1"
    assert latest_item["enrichment_status"] == "pending"
    assert latest_item["power"] == 111.1
    assert "tenant_id" not in latest_item
    assert "table" not in latest_item

    assert earliest_response.status_code == 200
    earliest_item = earliest_response.json()["data"]["item"]
    assert earliest_item["schema_version"] == "v1"
    assert earliest_item["enrichment_status"] == "pending"
    assert earliest_item["power"] == 99.1
    assert "tenant_id" not in earliest_item

    assert batch_response.status_code == 200
    batch_item = batch_response.json()["data"]["items"]["DEVICE-1"]
    assert batch_item["schema_version"] == "v1"
    assert batch_item["enrichment_status"] == "pending"
    assert batch_item["current"] == 9.5
    assert "tenant_id" not in batch_item
    assert "enriched_at" not in batch_item


@pytest.mark.asyncio
async def test_history_timeout_returns_explicit_degraded_error_contract() -> None:
    app = _build_test_app(_TelemetryHistoryTimeoutServiceStub())

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers=build_internal_headers("contract-test", "tenant-a"),
    ) as client:
        telemetry_response = await client.get("/api/v1/data/telemetry/DEVICE-1")

    assert telemetry_response.status_code == 504
    payload = telemetry_response.json()["detail"]
    assert payload["success"] is False
    assert payload["error"]["code"] == "TELEMETRY_HISTORY_TIMEOUT"
    assert payload["error"]["section"] == "history"
    assert payload["error"]["source"] == "influx"
    assert payload["error"]["retryable"] is True


@pytest.mark.asyncio
async def test_custom_query_uses_public_contract_shape() -> None:
    app = _build_test_app(_TelemetryContractServiceStub())

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers=build_internal_headers("contract-test", "tenant-a"),
    ) as client:
        response = await client.post(
            "/api/v1/data/query",
            json=TelemetryQuery(
                device_id="DEVICE-1",
                start_time=datetime(2026, 4, 8, tzinfo=timezone.utc),
                end_time=datetime(2026, 4, 8, 1, tzinfo=timezone.utc),
            ).model_dump(mode="json"),
        )

    assert response.status_code == 200
    item = response.json()["data"]["items"][0]
    assert item["device_id"] == "DEVICE-1"
    assert item["schema_version"] == "v1"
    assert item["enrichment_status"] == "pending"
    assert "tenant_id" not in item
    assert "table" not in item
    assert "debug_marker" not in item
