from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
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
os.environ.setdefault("INTERNAL_SERVICE_SHARED_SECRET", "test-internal-secret")

from app.api.v1.router import api_router
from app.api.v1 import devices as devices_api
from app.database import Base, get_db
from app.models.device import Device, DeviceIdSequence
from services.shared.tenant_context import TenantContext, build_internal_headers


@pytest_asyncio.fixture
async def app_with_db():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")

    async def _override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db

    async with session_factory() as session:
        session.add_all(
            [
                DeviceIdSequence(prefix="AD", next_value=1),
                DeviceIdSequence(prefix="TD", next_value=1),
                DeviceIdSequence(prefix="VD", next_value=1),
            ]
        )
        await session.commit()

    try:
        yield app, session_factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_inactive_plant_blocks_new_device_onboarding(monkeypatch, app_with_db):
    app, _session_factory = app_with_db

    monkeypatch.setattr(
        devices_api,
        "get_auth_state",
        lambda request: {
            "user_id": "user-1",
            "tenant_id": "ORG-1",
            "role": "org_admin",
            "plant_ids": [],
            "is_authenticated": True,
        },
    )
    monkeypatch.setattr(
        devices_api.TenantContext,
        "from_request",
        classmethod(
            lambda cls, request: TenantContext(
                tenant_id="ORG-1",
                user_id="user-1",
                role="org_admin",
                plant_ids=[],
                is_super_admin=False,
            )
        ),
    )
    monkeypatch.setattr(
        devices_api,
        "_list_tenant_plants",
        AsyncMock(return_value=[{"id": "PLANT-1", "name": "Plant 1", "is_active": False}]),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/api/v1/devices",
            headers={"X-Tenant-Id": "ORG-1"},
            json={
                "device_name": "Compressor 03",
                "device_type": "compressor",
                "device_id_class": "active",
                "data_source_type": "metered",
                "plant_id": "PLANT-1",
            },
        )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "PLANT_INACTIVE"


@pytest.mark.asyncio
async def test_inactive_plant_blocks_device_onboard_bundle(monkeypatch, app_with_db):
    app, _session_factory = app_with_db

    monkeypatch.setattr(
        devices_api,
        "get_auth_state",
        lambda request: {
            "user_id": "user-1",
            "tenant_id": "ORG-1",
            "role": "org_admin",
            "plant_ids": [],
            "is_authenticated": True,
        },
    )
    monkeypatch.setattr(
        devices_api.TenantContext,
        "from_request",
        classmethod(
            lambda cls, request: TenantContext(
                tenant_id="ORG-1",
                user_id="user-1",
                role="org_admin",
                plant_ids=[],
                is_super_admin=False,
            )
        ),
    )
    monkeypatch.setattr(
        devices_api,
        "_list_tenant_plants",
        AsyncMock(return_value=[{"id": "PLANT-1", "name": "Plant 1", "is_active": False}]),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/api/v1/devices/onboard",
            headers={"X-Tenant-Id": "ORG-1"},
            json={
                "device_name": "Compressor 03",
                "device_type": "compressor",
                "device_id_class": "active",
                "data_source_type": "metered",
                "phase_type": "single",
                "plant_id": "PLANT-1",
            },
        )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "PLANT_INACTIVE"


@pytest.mark.asyncio
async def test_internal_plant_device_count_is_tenant_scoped(monkeypatch, app_with_db):
    app, session_factory = app_with_db

    monkeypatch.setattr(
        devices_api.TenantContext,
        "from_request",
        classmethod(
            lambda cls, request: TenantContext(
                tenant_id="ORG-1",
                user_id="auth-service",
                role="internal_service",
                plant_ids=[],
                is_super_admin=False,
            )
        ),
    )

    async with session_factory() as session:
        session.add_all(
            [
                Device(
                    device_id="ORG1-DEVICE-1",
                    tenant_id="ORG-1",
                    plant_id="PLANT-1",
                    device_name="Device A",
                    device_type="compressor",
                ),
                Device(
                    device_id="ORG2-DEVICE-1",
                    tenant_id="ORG-2",
                    plant_id="PLANT-1",
                    device_name="Device B",
                    device_type="compressor",
                    deleted_at=datetime.utcnow(),
                ),
                Device(
                    device_id="ORG2-DEVICE-2",
                    tenant_id="ORG-2",
                    plant_id="PLANT-1",
                    device_name="Device C",
                    device_type="compressor",
                ),
            ]
        )
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get(
            "/api/v1/devices/internal/plants/PLANT-1/device-count",
            headers=build_internal_headers("auth-service", "ORG-1"),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["tenant_id"] == "ORG-1"
    assert payload["device_count"] == 1


@pytest.mark.asyncio
async def test_internal_active_device_summary_counts_only_non_deleted_active_inventory(monkeypatch, app_with_db):
    app, session_factory = app_with_db

    monkeypatch.setattr(
        devices_api.TenantContext,
        "from_request",
        classmethod(
            lambda cls, request: TenantContext(
                tenant_id=None,
                user_id="auth-service",
                role="internal_service",
                plant_ids=[],
                is_super_admin=True,
            )
        ),
    )

    async with session_factory() as session:
        session.add_all(
            [
                Device(
                    device_id="ORG1-ACTIVE-1",
                    tenant_id="ORG-1",
                    plant_id="PLANT-1",
                    device_name="Active Inventory Device",
                    device_type="compressor",
                    device_id_class="active",
                ),
                Device(
                    device_id="ORG2-ACTIVE-1",
                    tenant_id="ORG-2",
                    plant_id="PLANT-2",
                    device_name="Deleted Active Inventory Device",
                    device_type="compressor",
                    device_id_class="active",
                    deleted_at=datetime.utcnow(),
                ),
                Device(
                    device_id="ORG1-TEST-1",
                    tenant_id="ORG-1",
                    plant_id="PLANT-1",
                    device_name="Test Device",
                    device_type="compressor",
                    device_id_class="test",
                ),
                Device(
                    device_id="ORG2-VIRTUAL-1",
                    tenant_id="ORG-2",
                    plant_id="PLANT-2",
                    device_name="Virtual Device",
                    device_type="compressor",
                    device_id_class="virtual",
                ),
            ]
        )
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get(
            "/api/v1/devices/internal/summary/active-device-count",
            headers=build_internal_headers("auth-service"),
        )

    assert response.status_code == 200
    assert response.json() == {"total_active_devices": 1}
