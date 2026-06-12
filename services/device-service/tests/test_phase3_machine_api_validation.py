from __future__ import annotations

import os
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
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
from app.models.device import Device, MaintenanceLog, ParameterHealthConfig
from app.schemas.device import ParameterHealthConfigCreate, ParameterHealthConfigUpdate
from services.shared.tenant_context import TenantContext


async def _build_phase3_app(monkeypatch: pytest.MonkeyPatch):
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(
            Device(
                device_id="DEVICE-A",
                tenant_id="TENANT-A",
                plant_id="PLANT-A",
                device_name="Tenant A Device",
                device_type="compressor",
                data_source_type="metered",
                device_id_class="active",
            )
        )
        session.add_all(
            [
                ParameterHealthConfig(
                    device_id="DEVICE-A",
                    tenant_id="TENANT-A",
                    parameter_name="current",
                    canonical_parameter_name="current",
                    normal_min=8.0,
                    normal_max=18.0,
                    weight=60.0,
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
                    weight=40.0,
                    ignore_zero_value=False,
                    is_active=True,
                ),
                MaintenanceLog(
                    tenant_id="TENANT-A",
                    device_id="DEVICE-A",
                    maintenance_date=date(2026, 4, 10),
                    title="Baseline Service",
                    description="Seed maintenance record",
                    cost=Decimal("125.00"),
                    performed_by="Vendor Alpha",
                    status="completed",
                    next_due_date=date(2026, 7, 10),
                    created_by="seed-user",
                ),
            ]
        )
        await session.commit()

    app = FastAPI()

    @app.middleware("http")
    async def _inject_auth_context(request: Request, call_next):
        role = request.headers.get("X-Role", "org_admin")
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
async def test_health_score_endpoint_uses_running_machine_state_contract(monkeypatch: pytest.MonkeyPatch):
    app, engine = await _build_phase3_app(monkeypatch)

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            response = await client.post(
                "/api/v1/devices/DEVICE-A/health-score",
                headers={"X-Tenant-Id": "TENANT-A", "X-Plant-Id": "PLANT-A"},
                json={
                    "values": {
                        "current": 12.0,
                        "voltage": 230.0,
                    },
                    "machine_state": "RUNNING",
                },
            )
    finally:
        await engine.dispose()

    assert response.status_code == 200
    body = response.json()
    assert body["machine_state"] == "RUNNING"
    assert body["health_score"] == 100.0
    assert body["status"] == "Excellent"
    assert body["parameters_included"] == 2


def test_parameter_health_config_create_rejects_inverted_range():
    with pytest.raises(ValidationError):
        ParameterHealthConfigCreate(
            device_id="DEVICE-A",
            tenant_id="TENANT-A",
            parameter_name="current",
            normal_min=20.0,
            normal_max=10.0,
            weight=100.0,
        )


def test_parameter_health_config_update_rejects_inverted_range():
    with pytest.raises(ValidationError):
        ParameterHealthConfigUpdate(
            normal_min=15.0,
            normal_max=10.0,
        )


@pytest.mark.parametrize(
    "payload_factory",
    [
        lambda: ParameterHealthConfigCreate(
            device_id="DEVICE-A",
            tenant_id="TENANT-A",
            parameter_name="current",
            normal_min=float("inf"),
            normal_max=10.0,
            weight=100.0,
        ),
        lambda: ParameterHealthConfigUpdate(
            normal_min=0.0,
            normal_max=float("nan"),
        ),
    ],
)
def test_parameter_health_config_rejects_non_finite_ranges(payload_factory):
    with pytest.raises(ValidationError):
        payload_factory()


@pytest.mark.asyncio
async def test_create_maintenance_log_rejects_invalid_next_due_date(monkeypatch: pytest.MonkeyPatch):
    app, engine = await _build_phase3_app(monkeypatch)

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            response = await client.post(
                "/api/v1/devices/DEVICE-A/maintenance-log",
                headers={"X-Tenant-Id": "TENANT-A", "X-Plant-Id": "PLANT-A"},
                json={
                    "maintenance_date": "2026-04-10",
                    "title": "Invalid Follow Up",
                    "description": "This should fail validation.",
                    "cost": "50.00",
                    "next_due_date": "2026-04-01",
                },
            )
    finally:
        await engine.dispose()

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_update_maintenance_log_rejects_next_due_before_existing_maintenance_date(monkeypatch: pytest.MonkeyPatch):
    app, engine = await _build_phase3_app(monkeypatch)

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            response = await client.put(
                "/api/v1/devices/DEVICE-A/maintenance-log/1",
                headers={"X-Tenant-Id": "TENANT-A", "X-Plant-Id": "PLANT-A"},
                json={"next_due_date": "2026-04-01"},
            )
    finally:
        await engine.dispose()

    assert response.status_code == 400
    body = response.json()
    assert body["detail"]["code"] == "MAINTENANCE_LOG_VALIDATION_ERROR"
    assert "next_due_date cannot be earlier than maintenance_date" in body["detail"]["message"]


@pytest.mark.asyncio
async def test_health_config_route_returns_404_for_missing_config(monkeypatch: pytest.MonkeyPatch):
    app, engine = await _build_phase3_app(monkeypatch)

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            response = await client.get(
                "/api/v1/devices/DEVICE-A/health-config/999",
                headers={"X-Tenant-Id": "TENANT-A", "X-Plant-Id": "PLANT-A"},
            )
    finally:
        await engine.dispose()

    assert response.status_code == 404
    body = response.json()
    assert body["detail"]["error"]["code"] == "HEALTH_CONFIG_NOT_FOUND"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("get", "/api/v1/devices/DEVICE-A/shifts/999", None),
        ("put", "/api/v1/devices/DEVICE-A/shifts/999", {"shift_name": "Updated Name"}),
        ("delete", "/api/v1/devices/DEVICE-A/shifts/999", None),
    ],
)
async def test_shift_routes_return_404_for_missing_shift(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
    payload: dict | None,
):
    app, engine = await _build_phase3_app(monkeypatch)

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            request_kwargs = {
                "headers": {"X-Tenant-Id": "TENANT-A", "X-Plant-Id": "PLANT-A"},
            }
            if payload is not None:
                request_kwargs["json"] = payload
            response = await getattr(client, method)(path, **request_kwargs)
    finally:
        await engine.dispose()

    assert response.status_code == 404
    body = response.json()
    assert body["detail"]["error"]["code"] == "SHIFT_NOT_FOUND"
