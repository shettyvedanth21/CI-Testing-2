from __future__ import annotations

from pathlib import Path
import os
import sys

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

BASE_DIR = Path(__file__).resolve().parents[1]
SERVICES_DIR = BASE_DIR.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
if str(SERVICES_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICES_DIR))

os.environ["DATABASE_URL"] = "mysql+aiomysql://test:test@127.0.0.1:3306/test_db"
os.environ["JWT_SECRET_KEY"] = "test-secret-key-at-least-32-characters-long"

from app.api.v1.router import api_router
from app.api.v1 import devices as devices_api
from app.database import Base, get_db
from app.models.device import Device
from services.shared.tenant_context import TenantContext


class _CapturePropertyService:
    def __init__(self, session):
        self.session = session

    async def get_common_properties(self, device_ids, tenant_id=None):
        _CapturePropertyService.captured = {"device_ids": list(device_ids), "tenant_id": tenant_id}
        return ["power", "current"]

    async def get_all_devices_properties(self, tenant_id=None, accessible_plant_ids=None, limit=100, offset=0):
        _CapturePropertyService.captured = {
            "tenant_id": tenant_id,
            "accessible_plant_ids": list(accessible_plant_ids) if accessible_plant_ids is not None else None,
            "limit": limit,
            "offset": offset,
        }
        return {"DEVICE-1": ["power", "current"]}


@pytest.mark.asyncio
async def test_common_properties_endpoint_scopes_to_request_tenant(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    app = FastAPI()

    @app.middleware("http")
    async def _inject_auth_context(request: Request, call_next):
        request.state.tenant_context = TenantContext(
            tenant_id="tenant-a",
            user_id="user-1",
            role="org_admin",
            plant_ids=[],
            is_super_admin=False,
        )
        return await call_next(request)

    app.include_router(api_router, prefix="/api/v1")

    async def _override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    monkeypatch.setattr("app.services.device_property.DevicePropertyService", _CapturePropertyService)

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            response = await client.post(
                "/api/v1/devices/properties/common",
                json={"device_ids": ["DEVICE-1", "DEVICE-2"]},
            )
    finally:
        await engine.dispose()

    assert response.status_code == 200
    assert response.json() == {"success": True, "properties": ["power", "current"], "device_count": 2}
    assert _CapturePropertyService.captured["tenant_id"] == "tenant-a"
    assert _CapturePropertyService.captured["device_ids"] == ["DEVICE-1", "DEVICE-2"]


@pytest.mark.asyncio
async def test_all_device_properties_endpoint_passes_accessible_plants(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    app = FastAPI()

    @app.middleware("http")
    async def _inject_auth_context(request: Request, call_next):
        request.state.tenant_context = TenantContext(
            tenant_id="tenant-a",
            user_id="user-1",
            role="operator",
            plant_ids=["plant-a"],
            is_super_admin=False,
        )
        return await call_next(request)

    app.include_router(api_router, prefix="/api/v1")

    async def _override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    monkeypatch.setattr("app.services.device_property.DevicePropertyService", _CapturePropertyService)

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            response = await client.get("/api/v1/devices/properties")
    finally:
        await engine.dispose()

    assert response.status_code == 200
    assert _CapturePropertyService.captured["tenant_id"] == "tenant-a"
    assert _CapturePropertyService.captured["accessible_plant_ids"] == ["plant-a"]


@pytest.mark.asyncio
async def test_common_properties_endpoint_rejects_out_of_scope_devices(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    app = FastAPI()

    @app.middleware("http")
    async def _inject_auth_context(request: Request, call_next):
        request.state.tenant_context = TenantContext(
            tenant_id="tenant-a",
            user_id="user-1",
            role="operator",
            plant_ids=["plant-a"],
            is_super_admin=False,
        )
        return await call_next(request)

    app.include_router(api_router, prefix="/api/v1")

    async def _override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db

    async with session_factory() as session:
        session.add_all(
            [
                Device(device_id="DEVICE-A", tenant_id="tenant-a", plant_id="plant-a", device_name="A", device_type="meter"),
                Device(device_id="DEVICE-B", tenant_id="tenant-a", plant_id="plant-b", device_name="B", device_type="meter"),
            ]
        )
        await session.commit()

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            response = await client.post(
                "/api/v1/devices/properties/common",
                json={"device_ids": ["DEVICE-A", "DEVICE-B"]},
            )
    finally:
        await engine.dispose()

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "DEVICE_ACCESS_DENIED"


@pytest.mark.asyncio
async def test_device_properties_endpoint_hides_out_of_scope_device():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    app = FastAPI()

    @app.middleware("http")
    async def _inject_auth_context(request: Request, call_next):
        request.state.tenant_context = TenantContext(
            tenant_id="tenant-a",
            user_id="user-1",
            role="operator",
            plant_ids=["plant-a"],
            is_super_admin=False,
        )
        return await call_next(request)

    app.include_router(api_router, prefix="/api/v1")

    async def _override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db

    async with session_factory() as session:
        session.add(
            Device(device_id="DEVICE-B", tenant_id="tenant-a", plant_id="plant-b", device_name="B", device_type="meter")
        )
        await session.commit()

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            response = await client.get("/api/v1/devices/DEVICE-B/properties")
    finally:
        await engine.dispose()

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "DEVICE_NOT_FOUND"
