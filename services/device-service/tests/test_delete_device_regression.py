from __future__ import annotations

import os
import re
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

from app.api.v1.router import api_router
from app.api.v1 import devices as devices_api
from app.database import Base, get_db
from app.models.device import Device, DeviceIdSequence
from services.shared.tenant_context import TenantContext

DEVICE_ID_PATTERN = re.compile(r"^(AD|TD|VD)\d{8}$")


class _DashboardService:
    def __init__(self, session):
        self.session = session

    async def get_fleet_snapshot(self, *args, **kwargs):
        return {
            "generated_at": "2026-03-30T00:00:00+00:00",
            "stale": False,
            "warnings": [],
            "devices": [],
        }


class _ProjectionService:
    calls: list[tuple[str, str]] = []

    def __init__(self, session):
        self.session = session

    async def remove_device_projection(self, device_id: str, tenant_id: str) -> None:
        self.calls.append((device_id, tenant_id))


@pytest_asyncio.fixture
async def delete_app():
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

    try:
        async with session_factory() as session:
            session.add_all(
                [
                    DeviceIdSequence(prefix="AD", next_value=1),
                    DeviceIdSequence(prefix="TD", next_value=1),
                    DeviceIdSequence(prefix="VD", next_value=1),
                ]
            )
            await session.commit()
        yield app, session_factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_delete_device_soft_deletes_and_cleans_projection(monkeypatch, delete_app):
    app, session_factory = delete_app
    publish_mock = AsyncMock()
    _ProjectionService.calls = []

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
    monkeypatch.setattr("app.services.live_projection.LiveProjectionService", _ProjectionService)
    monkeypatch.setattr("app.services.live_dashboard.LiveDashboardService", _DashboardService)
    monkeypatch.setattr(devices_api.fleet_stream_broadcaster, "publish", publish_mock)

    async with session_factory() as session:
        session.add(
            Device(
                device_id="COMPRESSOR-01",
                tenant_id="ORG-1",
                plant_id="PLANT-1",
                device_name="Compressor 01",
                device_type="compressor",
            )
        )
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.delete("/api/v1/devices/COMPRESSOR-01", headers={"X-Tenant-Id": "ORG-1"})

    assert response.status_code == 204
    assert response.content == b""

    async with session_factory() as session:
        device = await session.get(Device, {"device_id": "COMPRESSOR-01", "tenant_id": "ORG-1"})

    assert device is not None
    assert device.deleted_at is not None
    assert _ProjectionService.calls == [("COMPRESSOR-01", "ORG-1")]
    publish_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_device_after_soft_delete_returns_conflict(monkeypatch, delete_app):
    app, session_factory = delete_app

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
    monkeypatch.setattr(devices_api, "_list_tenant_plants", AsyncMock(return_value=[{"id": "PLANT-1", "name": "Plant 1"}]))

    async with session_factory() as session:
        session.add(
            Device(
                device_id="COMPRESSOR-02",
                tenant_id="ORG-1",
                plant_id="PLANT-1",
                device_name="Compressor 02",
                device_type="compressor",
                deleted_at=datetime.utcnow(),
            )
        )
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/api/v1/devices",
            headers={"X-Tenant-Id": "ORG-1"},
            json={
                "device_name": "Compressor 02",
                "device_type": "compressor",
                "device_id_class": "active",
                "data_source_type": "metered",
                "plant_id": "PLANT-1",
            },
        )

    assert response.status_code == 201
    created_payload = response.json()["data"]
    assert created_payload["device_id"] != "COMPRESSOR-02"


@pytest.mark.asyncio
async def test_create_device_rejects_missing_plant(monkeypatch, delete_app):
    app, _session_factory = delete_app

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

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/api/v1/devices",
            headers={"X-Tenant-Id": "ORG-1"},
            json={
                "device_name": "Compressor 03",
                "device_type": "compressor",
                "device_id_class": "active",
                "data_source_type": "metered",
            },
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_update_device_rejects_clearing_plant_assignment(monkeypatch, delete_app):
    app, session_factory = delete_app

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

    async with session_factory() as session:
        session.add(
            Device(
                device_id="COMPRESSOR-UPDATE-01",
                tenant_id="ORG-1",
                plant_id="PLANT-1",
                device_name="Compressor Update",
                device_type="compressor",
            )
        )
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.put(
            "/api/v1/devices/COMPRESSOR-UPDATE-01",
            headers={"X-Tenant-Id": "ORG-1"},
            json={"plant_id": None},
        )

    assert response.status_code == 422

    async with session_factory() as session:
        device = await session.get(Device, {"device_id": "COMPRESSOR-UPDATE-01", "tenant_id": "ORG-1"})

    assert device is not None
    assert device.plant_id == "PLANT-1"


@pytest.mark.asyncio
async def test_create_device_rejects_unknown_plant(monkeypatch, delete_app):
    app, _session_factory = delete_app

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
    monkeypatch.setattr(devices_api, "_list_tenant_plants", AsyncMock(return_value=[{"id": "PLANT-1", "name": "Plant 1"}]))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/api/v1/devices",
            headers={"X-Tenant-Id": "ORG-1", "Authorization": "Bearer token"},
            json={
                "device_name": "Compressor 04",
                "device_type": "compressor",
                "device_id_class": "active",
                "data_source_type": "metered",
                "plant_id": "PLANT-404",
            },
        )

    assert response.status_code == 400
    payload = response.json()
    assert payload["detail"]["code"] == "PLANT_NOT_FOUND"


@pytest.mark.asyncio
async def test_create_device_succeeds_with_valid_plant(monkeypatch, delete_app):
    app, session_factory = delete_app

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
    monkeypatch.setattr(devices_api, "_list_tenant_plants", AsyncMock(return_value=[{"id": "PLANT-1", "name": "Plant 1"}]))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/api/v1/devices",
            headers={"X-Tenant-Id": "ORG-1", "Authorization": "Bearer token"},
            json={
                "device_name": "Compressor 05",
                "device_type": "compressor",
                "device_id_class": "active",
                "data_source_type": "metered",
                "plant_id": "PLANT-1",
            },
        )

    assert response.status_code == 201
    created_payload = response.json()["data"]
    assert DEVICE_ID_PATTERN.fullmatch(created_payload["device_id"])

    async with session_factory() as session:
        device = await session.get(Device, {"device_id": created_payload["device_id"], "tenant_id": "ORG-1"})

    assert device is not None
    assert device.plant_id == "PLANT-1"
