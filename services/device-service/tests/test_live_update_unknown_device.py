from datetime import datetime, timezone
from pathlib import Path
import os
import sys
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

os.environ["DATABASE_URL"] = "mysql+aiomysql://test:test@127.0.0.1:3306/test_db"

from app.api.v1.router import api_router
from app.api.v1 import devices as devices_api
from app.database import Base, get_db
from app.models.device import Device, DeviceLiveState


class _ProjectionShouldNotRun:
    def __init__(self, session):
        self.session = session

    async def apply_live_update(self, *args, **kwargs):
        raise AssertionError("projection should not run for unknown devices")


class _DashboardShouldNotRun:
    def __init__(self, session):
        self.session = session

    async def publish_device_update(self, *args, **kwargs):
        raise AssertionError("dashboard publish should not run for unknown devices")


class _PropertyService:
    def __init__(self, session):
        self.session = session

    async def sync_from_telemetry(self, *args, **kwargs):
        return None


class _ProjectionHappyPath:
    def __init__(self, session):
        self.session = session

    async def apply_live_update(
        self,
        device_id,
        tenant_id,
        telemetry_payload,
        dynamic_fields,
        normalized_fields=None,
    ):
        state = await self.session.get(DeviceLiveState, {"device_id": device_id, "tenant_id": tenant_id})
        if state is None:
            state = DeviceLiveState(
                device_id=device_id,
                tenant_id=tenant_id,
                runtime_status="running",
                version=1,
                last_telemetry_ts=datetime.now(timezone.utc),
            )
            self.session.add(state)
        else:
            state.runtime_status = "running"
            state.version += 1
            state.last_telemetry_ts = datetime.now(timezone.utc)
        await self.session.commit()
        return {"device_id": device_id, "version": state.version, "runtime_status": state.runtime_status}


class _DashboardHappyPath:
    def __init__(self, session):
        self.session = session

    async def publish_device_update(self, device_id, tenant_id, partial=True):
        return {
            "generated_at": "2026-03-24T00:00:00+00:00",
            "stale": False,
            "warnings": [],
            "devices": [{"device_id": device_id, "version": 1}],
            "partial": partial,
            "version": 1,
        }


@pytest_asyncio.fixture
async def live_update_app():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _disable_sqlite_foreign_keys(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=OFF")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text(
                """
                CREATE TABLE telemetry_outbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id VARCHAR(50),
                    target VARCHAR(255),
                    status VARCHAR(32)
                )
                """
            )
        )

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")

    async def _override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db

    try:
        yield app, session_factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_live_update_returns_404_without_side_effects_for_unknown_device(monkeypatch, live_update_app):
    app, session_factory = live_update_app
    publish_mock = AsyncMock()
    monkeypatch.setattr("app.services.live_projection.LiveProjectionService", _ProjectionShouldNotRun)
    monkeypatch.setattr("app.services.live_dashboard.LiveDashboardService", _DashboardShouldNotRun)
    monkeypatch.setattr("app.services.device_property.DevicePropertyService", _PropertyService)
    monkeypatch.setattr(devices_api.fleet_stream_broadcaster, "publish", publish_mock)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/api/v1/devices/UNKNOWN-DEVICE/live-update",
            json={
                "telemetry": {"power": 12.5},
                "dynamic_fields": {"power": 12.5},
                "tenant_id": "tenant-a",
            },
        )

    assert response.status_code == 404
    assert response.json() == {"error": "DEVICE_NOT_FOUND", "device_id": "UNKNOWN-DEVICE"}

    async with session_factory() as session:
        properties_count = await session.scalar(text("SELECT COUNT(*) FROM device_properties"))
        live_state_count = await session.scalar(text("SELECT COUNT(*) FROM device_live_state"))
        outbox_count = await session.scalar(
            text("SELECT COUNT(*) FROM telemetry_outbox WHERE target = 'device-service'")
        )

    assert properties_count == 0
    assert live_state_count == 0
    assert outbox_count == 0
    publish_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_live_update_happy_path_still_updates_known_device(monkeypatch, live_update_app):
    app, session_factory = live_update_app
    publish_mock = AsyncMock()
    monkeypatch.setattr("app.services.live_projection.LiveProjectionService", _ProjectionHappyPath)
    monkeypatch.setattr("app.services.live_dashboard.LiveDashboardService", _DashboardHappyPath)
    monkeypatch.setattr("app.services.device_property.DevicePropertyService", _PropertyService)
    monkeypatch.setattr(devices_api.fleet_stream_broadcaster, "publish", publish_mock)

    async with session_factory() as session:
        session.add(
            Device(
                device_id="KNOWN-DEVICE",
                tenant_id="tenant-a",
                plant_id="PLANT-1",
                device_name="Known Device",
                device_type="compressor",
            )
        )
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/api/v1/devices/KNOWN-DEVICE/live-update",
            json={
                "telemetry": {"power": 18.2},
                "dynamic_fields": {"power": 18.2},
                "tenant_id": "tenant-a",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["device"]["device_id"] == "KNOWN-DEVICE"
    assert body["device"]["runtime_status"] == "running"

    async with session_factory() as session:
        properties_count = await session.scalar(
            text("SELECT COUNT(*) FROM device_properties WHERE device_id = 'KNOWN-DEVICE'")
        )
        live_state_count = await session.scalar(
            text("SELECT COUNT(*) FROM device_live_state WHERE device_id = 'KNOWN-DEVICE'")
        )
        outbox_count = await session.scalar(
            text("SELECT COUNT(*) FROM telemetry_outbox WHERE target = 'device-service'")
        )

    assert properties_count == 0
    assert live_state_count == 1
    assert outbox_count == 0
    publish_mock.assert_awaited_once()
