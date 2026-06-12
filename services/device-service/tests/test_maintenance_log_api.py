from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
import os
import sys

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
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
from app.models.device import Device, MaintenanceLog
from services.shared.tenant_context import TenantContext


async def _build_maintenance_log_app(monkeypatch: pytest.MonkeyPatch):
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
        session.add_all(
            [
                Device(
                    device_id="DEVICE-A",
                    tenant_id="TENANT-A",
                    plant_id="PLANT-A",
                    device_name="Tenant A Device",
                    device_type="compressor",
                    device_id_class="active",
                ),
                Device(
                    device_id="DEVICE-B",
                    tenant_id="TENANT-B",
                    plant_id="PLANT-B",
                    device_name="Tenant B Device",
                    device_type="compressor",
                    device_id_class="active",
                ),
                Device(
                    device_id="DEVICE-A-NOLOG",
                    tenant_id="TENANT-A",
                    plant_id="PLANT-A",
                    device_name="Tenant A No Log Device",
                    device_type="compressor",
                    device_id_class="active",
                ),
            ]
        )
        session.add_all(
            [
                MaintenanceLog(
                    tenant_id="TENANT-A",
                    device_id="DEVICE-A",
                    maintenance_date=date(2026, 4, 1),
                    title="Oil Change",
                    description="Changed compressor oil and topped up levels.",
                    cost=Decimal("1499.50"),
                    performed_by="Vendor Alpha",
                    status="completed",
                    next_due_date=date(2026, 7, 1),
                    created_by="seed-user-a",
                ),
                MaintenanceLog(
                    tenant_id="TENANT-B",
                    device_id="DEVICE-B",
                    maintenance_date=date(2026, 4, 2),
                    title="Bearing Inspection",
                    description="Inspected bearings for vibration noise.",
                    cost=Decimal("320.00"),
                    performed_by="Vendor Beta",
                    status="scheduled",
                    next_due_date=date(2026, 5, 15),
                    created_by="seed-user-b",
                ),
            ]
        )
        await session.commit()

    app = FastAPI()

    @app.middleware("http")
    async def _inject_auth_context(request: Request, call_next):
        tenant_id = request.headers.get("X-Tenant-Id")
        role = request.headers.get("X-Role", "org_admin")
        request.state.tenant_context = TenantContext(
            tenant_id=tenant_id,
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

    return app, session_factory, engine


@pytest.mark.asyncio
async def test_list_and_summary_are_tenant_scoped(monkeypatch: pytest.MonkeyPatch):
    app, _session_factory, engine = await _build_maintenance_log_app(monkeypatch)

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            list_response = await client.get(
                "/api/v1/devices/DEVICE-A/maintenance-log",
                headers={"X-Tenant-Id": "TENANT-A", "X-Plant-Id": "PLANT-A"},
            )
            summary_response = await client.get(
                "/api/v1/devices/DEVICE-A/maintenance-log/summary",
                headers={"X-Tenant-Id": "TENANT-A", "X-Plant-Id": "PLANT-A"},
            )
            cross_tenant_response = await client.get(
                "/api/v1/devices/DEVICE-B/maintenance-log",
                headers={"X-Tenant-Id": "TENANT-A", "X-Plant-Id": "PLANT-A"},
            )
    finally:
        await engine.dispose()

    assert list_response.status_code == 200
    list_body = list_response.json()
    assert list_body["total"] == 1
    assert list_body["data"][0]["device_id"] == "DEVICE-A"
    assert list_body["data"][0]["tenant_id"] == "TENANT-A"
    assert list_body["data"][0]["title"] == "Oil Change"

    assert summary_response.status_code == 200
    summary = summary_response.json()["data"]
    assert summary["total_records"] == 1
    assert summary["total_cost"] == "1499.50"
    assert summary["latest_maintenance_date"] == "2026-04-01"
    assert summary["next_due_date"] == "2026-07-01"

    assert cross_tenant_response.status_code == 404
    assert cross_tenant_response.json()["detail"]["code"] == "DEVICE_NOT_FOUND"


@pytest.mark.asyncio
async def test_maintenance_summary_returns_zero_state_for_device_without_records(monkeypatch: pytest.MonkeyPatch):
    app, _session_factory, engine = await _build_maintenance_log_app(monkeypatch)

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            response = await client.get(
                "/api/v1/devices/DEVICE-A-NOLOG/maintenance-log/summary",
                headers={"X-Tenant-Id": "TENANT-A", "X-Plant-Id": "PLANT-A"},
            )
    finally:
        await engine.dispose()

    assert response.status_code == 200
    summary = response.json()["data"]
    assert summary["total_records"] == 0
    assert summary["total_cost"] == "0.00"
    assert summary["latest_maintenance_date"] is None
    assert summary["next_due_date"] is None


@pytest.mark.asyncio
async def test_create_update_and_delete_maintenance_log_record(monkeypatch: pytest.MonkeyPatch):
    app, session_factory, engine = await _build_maintenance_log_app(monkeypatch)

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            create_response = await client.post(
                "/api/v1/devices/DEVICE-A/maintenance-log",
                headers={
                    "X-Tenant-Id": "TENANT-A",
                    "X-Plant-Id": "PLANT-A",
                    "X-User-Id": "creator-1",
                },
                json={
                    "maintenance_date": "2026-04-10",
                    "title": "Filter Replacement",
                    "description": "Replaced intake filter and cleaned housing.",
                    "cost": "250.75",
                    "performed_by": "In-house Team",
                    "status": "completed",
                    "next_due_date": "2026-10-10",
                },
            )

        assert create_response.status_code == 201
        created = create_response.json()["data"]
        assert created["device_id"] == "DEVICE-A"
        assert created["tenant_id"] == "TENANT-A"
        assert created["created_by"] == "creator-1"
        assert created["cost"] == "250.75"

        record_id = int(created["id"])

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            update_response = await client.put(
                f"/api/v1/devices/DEVICE-A/maintenance-log/{record_id}",
                headers={"X-Tenant-Id": "TENANT-A", "X-Plant-Id": "PLANT-A"},
                json={
                    "status": "follow_up_required",
                    "cost": "300.00",
                    "next_due_date": "2026-11-01",
                },
            )
            delete_response = await client.delete(
                f"/api/v1/devices/DEVICE-A/maintenance-log/{record_id}",
                headers={"X-Tenant-Id": "TENANT-A", "X-Plant-Id": "PLANT-A"},
            )

        assert update_response.status_code == 200
        updated = update_response.json()["data"]
        assert updated["status"] == "follow_up_required"
        assert updated["cost"] == "300.00"
        assert updated["next_due_date"] == "2026-11-01"

        assert delete_response.status_code == 200
        delete_body = delete_response.json()
        assert delete_body["maintenance_log_id"] == record_id

        async with session_factory() as session:
            remaining = await session.execute(select(MaintenanceLog).where(MaintenanceLog.id == record_id))
            assert remaining.scalar_one_or_none() is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_update_maintenance_log_rejects_moving_maintenance_date_past_existing_next_due_date(monkeypatch: pytest.MonkeyPatch):
    app, _session_factory, engine = await _build_maintenance_log_app(monkeypatch)

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            response = await client.put(
                "/api/v1/devices/DEVICE-A/maintenance-log/1",
                headers={"X-Tenant-Id": "TENANT-A", "X-Plant-Id": "PLANT-A"},
                json={"maintenance_date": "2026-08-01"},
            )
    finally:
        await engine.dispose()

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "MAINTENANCE_LOG_VALIDATION_ERROR"
    assert "next_due_date cannot be earlier than maintenance_date" in response.json()["detail"]["message"]


@pytest.mark.asyncio
async def test_delete_maintenance_log_returns_404_when_record_missing(monkeypatch: pytest.MonkeyPatch):
    app, _session_factory, engine = await _build_maintenance_log_app(monkeypatch)

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            response = await client.delete(
                "/api/v1/devices/DEVICE-A/maintenance-log/999",
                headers={"X-Tenant-Id": "TENANT-A", "X-Plant-Id": "PLANT-A"},
            )
    finally:
        await engine.dispose()

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "MAINTENANCE_LOG_NOT_FOUND"


@pytest.mark.asyncio
async def test_viewer_cannot_mutate_maintenance_log(monkeypatch: pytest.MonkeyPatch):
    app, _session_factory, engine = await _build_maintenance_log_app(monkeypatch)

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            response = await client.post(
                "/api/v1/devices/DEVICE-A/maintenance-log",
                headers={"X-Tenant-Id": "TENANT-A", "X-Role": "viewer", "X-Plant-Id": "PLANT-A"},
                json={
                    "maintenance_date": "2026-04-10",
                    "title": "Viewer Attempt",
                    "description": "This should be blocked.",
                    "cost": "10.00",
                },
            )
    finally:
        await engine.dispose()

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "FORBIDDEN"
