from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

BASE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
SERVICES_DIR = REPO_ROOT / "services"
for path in (REPO_ROOT, SERVICES_DIR, BASE_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.environ["DATABASE_URL"] = "mysql+aiomysql://test:test@127.0.0.1:3306/test_db"
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-at-least-32-characters-long")

from app.api.v1 import devices as devices_api
from app.api.v1.router import api_router
from app.database import Base, get_db
from app.models.device import (
    Device,
    DeviceLatestTelemetrySnapshot,
    DeviceLiveState,
    DeviceProperty,
    DeviceRecentTelemetrySample,
    ParameterHealthConfig,
)
from services.shared.tenant_context import TenantContext


async def _build_app(monkeypatch: pytest.MonkeyPatch):
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc)
    async with session_factory() as session:
        session.add(
            Device(
                device_id="DEVICE-A",
                tenant_id="TENANT-A",
                plant_id="PLANT-A",
                device_name="Tenant A Device",
                device_type="compressor",
                location="Bay 1",
                last_seen_timestamp=now,
                first_telemetry_timestamp=now,
                data_source_type="metered",
                device_id_class="active",
            )
        )
        session.add(
            DeviceLiveState(
                device_id="DEVICE-A",
                tenant_id="TENANT-A",
                runtime_status="running",
                load_state="running",
                last_telemetry_ts=now,
                last_current_a=12.0,
                last_voltage_v=230.0,
                version=9,
            )
        )
        session.add(
            DeviceLatestTelemetrySnapshot(
                device_id="DEVICE-A",
                tenant_id="TENANT-A",
                sample_ts=now,
                projection_version=9,
                snapshot_version=1,
                runtime_status="running",
                load_state="running",
                current_band="in_load",
                last_power_kw=4.2,
                last_current_a=12.0,
                last_voltage_v=230.0,
                numeric_fields_json='{"power": 4200, "current": 12.0, "voltage": 230.0}',
                source_fields_json='{"power_field":"power","current_field":"current","voltage_field":"voltage"}',
                normalization_version="v1",
                updated_at=now,
            )
        )
        session.add(
            DeviceRecentTelemetrySample(
                device_id="DEVICE-A",
                tenant_id="TENANT-A",
                sample_ts=now,
                projection_version=9,
                runtime_status="running",
                load_state="running",
                current_band="in_load",
                telemetry_json='{"timestamp":"2026-05-01T10:00:00Z","device_id":"DEVICE-A","power":4200,"current":12.0,"voltage":230.0}',
                created_at=now,
            )
        )
        session.add_all(
            [
                DeviceProperty(
                    device_id="DEVICE-A",
                    tenant_id="TENANT-A",
                    property_name="power",
                    data_type="float",
                    is_numeric=True,
                    discovered_at=now,
                    last_seen_at=now,
                ),
                DeviceProperty(
                    device_id="DEVICE-A",
                    tenant_id="TENANT-A",
                    property_name="current",
                    data_type="float",
                    is_numeric=True,
                    discovered_at=now,
                    last_seen_at=now,
                ),
                DeviceProperty(
                    device_id="DEVICE-A",
                    tenant_id="TENANT-A",
                    property_name="voltage",
                    data_type="float",
                    is_numeric=True,
                    discovered_at=now,
                    last_seen_at=now,
                ),
                ParameterHealthConfig(
                    device_id="DEVICE-A",
                    tenant_id="TENANT-A",
                    parameter_name="current",
                    canonical_parameter_name="current",
                    normal_min=8.0,
                    normal_max=18.0,
                    weight=50.0,
                    ignore_zero_value=False,
                    is_active=True,
                ),
                ParameterHealthConfig(
                    device_id="DEVICE-A",
                    tenant_id="TENANT-A",
                    parameter_name="voltage",
                    canonical_parameter_name="voltage",
                    normal_min=210.0,
                    normal_max=250.0,
                    weight=50.0,
                    ignore_zero_value=False,
                    is_active=True,
                ),
            ]
        )
        await session.commit()

    app = FastAPI()

    @app.middleware("http")
    async def _inject_auth_context(request: Request, call_next):
        role = request.headers.get("X-Role", "viewer")
        request.state.tenant_context = TenantContext(
            tenant_id=request.headers.get("X-Tenant-Id", "TENANT-A"),
            user_id=request.headers.get("X-User-Id", "user-1"),
            role=role,
            plant_ids=[request.headers.get("X-Plant-Id", "PLANT-A")],
            is_super_admin=role == "super_admin",
        )
        request.state.role = role
        return await call_next(request)

    app.include_router(api_router, prefix="/api/v1")

    async def _override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db

    monkeypatch.setattr(
        devices_api,
        "get_auth_state",
        lambda request: {
            "user_id": request.state.tenant_context.user_id,
            "tenant_id": request.state.tenant_context.tenant_id,
            "role": request.state.tenant_context.role,
            "plant_ids": list(request.state.tenant_context.plant_ids),
            "is_authenticated": True,
        },
    )

    return app, engine


@pytest.mark.asyncio
async def test_detail_snapshot_endpoint_returns_projection_backed_latest_metrics(monkeypatch: pytest.MonkeyPatch):
    app, engine = await _build_app(monkeypatch)

    async def _unexpected_get_client(*args, **kwargs):
        raise AssertionError("detail-snapshot should not create outbound http clients")

    monkeypatch.setattr("app.services.live_dashboard.get_client", _unexpected_get_client)

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            response = await client.get(
                "/api/v1/devices/DEVICE-A/detail-snapshot",
                headers={"X-Tenant-Id": "TENANT-A", "X-Plant-Id": "PLANT-A"},
            )
    finally:
        await engine.dispose()

    assert response.status_code == 200
    payload = response.json()
    assert payload["device_id"] == "DEVICE-A"
    assert payload["availability"]["snapshot_ready"] is True
    assert payload["availability"]["widget_config_ready"] is True
    assert payload["availability"]["health_score_ready"] is True
    assert payload["availability"]["stale"] is True
    assert payload["snapshot"]["projection_version"] == 9
    assert payload["snapshot"]["runtime_status"] == "stopped"
    assert payload["snapshot"]["numeric_fields"]["power"] == 4200
    assert payload["snapshot"]["numeric_fields"]["current"] == 12.0
    assert payload["widget_config"]["effective_fields"] == ["current", "power", "voltage"]
    assert payload["health_score"] is not None
    assert payload["health_score"]["status"] == "Standby"
    assert payload["availability"]["recent_telemetry_ready"] is True
    assert payload["recent_telemetry"][0]["power"] == 4200


@pytest.mark.asyncio
async def test_detail_snapshot_endpoint_preserves_tenant_scope(monkeypatch: pytest.MonkeyPatch):
    app, engine = await _build_app(monkeypatch)

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            response = await client.get(
                "/api/v1/devices/DEVICE-A/detail-snapshot",
                headers={"X-Tenant-Id": "TENANT-B", "X-Plant-Id": "PLANT-B"},
            )
    finally:
        await engine.dispose()

    assert response.status_code == 404
