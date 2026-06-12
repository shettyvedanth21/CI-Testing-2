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
SERVICES_DIR = Path(__file__).resolve().parents[2]
PROJECT_ROOT = Path(__file__).resolve().parents[3]
for path in (BASE_DIR, SERVICES_DIR, PROJECT_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.environ["DATABASE_URL"] = "mysql+aiomysql://test:test@127.0.0.1:3306/test_db"
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

from app.api.v1.router import api_router
from app.api.v1 import devices as devices_api
from app.database import Base, get_db
from app.models.device import Device
from services.shared.tenant_context import TenantContext


class _PropertyService:
    def __init__(self, session):
        self.session = session

    async def sync_from_telemetry(self, *args, **kwargs):
        return None

    async def sync_from_telemetry_batch(self, *args, **kwargs):
        return None


class _PropertyServiceFailure(_PropertyService):
    async def sync_from_telemetry_batch(self, *args, **kwargs):
        raise RuntimeError("property sync deadlock")


class _ProjectionMixedBatch:
    def __init__(self, session):
        self.session = session

    async def apply_live_updates_batch(self, *, tenant_id, updates):
        results = []
        published_items = []
        for update in updates:
            device_id = update["device_id"]
            if device_id == "DEVICE-BAD":
                results.append(
                    {
                        "device_id": device_id,
                        "success": False,
                        "error": "phase_type must be 'single', 'three', or null",
                        "error_code": "INVALID_DEVICE_METADATA",
                        "retryable": False,
                    }
                )
                continue
            item = {
                "device_id": device_id,
                "tenant_id": tenant_id,
                "runtime_status": "running",
                "load_state": "running",
                "version": 1,
            }
            results.append(
                {
                    "device_id": device_id,
                    "success": True,
                    "device": item,
                    "retryable": False,
                }
            )
            published_items.append(item)
        return results, published_items


@pytest_asyncio.fixture
async def contract_app():
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

    @app.middleware("http")
    async def _inject_auth_state(request, call_next):
        tenant_id = request.headers.get("X-Tenant-Id")
        request.state.tenant_context = TenantContext(
            tenant_id=tenant_id,
            user_id="test-user",
            role="org_admin",
            plant_ids=["PLANT-1"],
            is_super_admin=False,
        )
        request.state.role = "org_admin"
        request.state.user_id = "test-user"
        request.state.plant_ids = ["PLANT-1"]
        request.state.tenant_id = tenant_id
        return await call_next(request)

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
async def test_get_device_normalizes_legacy_phase_type_alias(contract_app):
    app, session_factory = contract_app
    async with session_factory() as session:
        session.add(
            Device(
                device_id="DEVICE-LEGACY",
                tenant_id="tenant-a",
                plant_id="PLANT-1",
                device_name="Legacy Device",
                device_type="compressor",
                phase_type="single_phase",
                created_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get(
            "/api/v1/devices/DEVICE-LEGACY",
            headers={"X-Tenant-Id": "tenant-a"},
        )

    assert response.status_code == 200
    assert response.json()["data"]["phase_type"] == "single"


@pytest.mark.asyncio
async def test_get_device_returns_422_for_invalid_phase_type_metadata(contract_app):
    app, session_factory = contract_app
    async with session_factory() as session:
        session.add(
            Device(
                device_id="DEVICE-INVALID",
                tenant_id="tenant-a",
                plant_id="PLANT-1",
                device_name="Invalid Device",
                device_type="compressor",
                phase_type="bogus",
                created_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get(
            "/api/v1/devices/DEVICE-INVALID",
            headers={"X-Tenant-Id": "tenant-a"},
        )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "INVALID_DEVICE_METADATA"
    assert detail["device_id"] == "DEVICE-INVALID"


@pytest.mark.asyncio
async def test_list_devices_coerces_invalid_phase_type_without_500(contract_app):
    app, session_factory = contract_app
    async with session_factory() as session:
        session.add_all(
            [
                Device(
                    device_id="DEVICE-GOOD",
                    tenant_id="tenant-a",
                    plant_id="PLANT-1",
                    device_name="Good Device",
                    device_type="compressor",
                    phase_type="single",
                    created_at=datetime.now(timezone.utc),
                ),
                Device(
                    device_id="DEVICE-LEGACY",
                    tenant_id="tenant-a",
                    plant_id="PLANT-1",
                    device_name="Legacy Device",
                    device_type="compressor",
                    phase_type="single_phase",
                    created_at=datetime.now(timezone.utc),
                ),
                Device(
                    device_id="DEVICE-INVALID",
                    tenant_id="tenant-a",
                    plant_id="PLANT-1",
                    device_name="Invalid Device",
                    device_type="compressor",
                    phase_type="bogus",
                    created_at=datetime.now(timezone.utc),
                ),
            ]
        )
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get(
            "/api/v1/devices?page=1&page_size=10",
            headers={"X-Tenant-Id": "tenant-a"},
        )

    assert response.status_code == 200
    body = response.json()
    phase_types = {row["device_id"]: row["phase_type"] for row in body["data"]}
    assert phase_types["DEVICE-GOOD"] == "single"
    assert phase_types["DEVICE-LEGACY"] == "single"
    assert phase_types["DEVICE-INVALID"] is None


@pytest.mark.asyncio
async def test_batch_live_update_isolates_invalid_metadata_per_item(monkeypatch, contract_app):
    app, session_factory = contract_app
    publish_mock = AsyncMock()
    monkeypatch.setattr("app.services.live_projection.LiveProjectionService", _ProjectionMixedBatch)
    monkeypatch.setattr("app.services.device_property.DevicePropertyService", _PropertyService)
    monkeypatch.setattr(devices_api.fleet_stream_broadcaster, "publish", publish_mock)

    async with session_factory() as session:
        session.add_all(
            [
                Device(
                    device_id="DEVICE-GOOD",
                    tenant_id="tenant-a",
                    plant_id="PLANT-1",
                    device_name="Good Device",
                    device_type="compressor",
                    phase_type="single",
                ),
                Device(
                    device_id="DEVICE-BAD",
                    tenant_id="tenant-a",
                    plant_id="PLANT-1",
                    device_name="Bad Device",
                    device_type="compressor",
                    phase_type="bogus",
                ),
            ]
        )
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/api/v1/devices/live-update/batch",
            headers={"X-Tenant-Id": "tenant-a"},
            json={
                "tenant_id": "tenant-a",
                "updates": [
                    {"device_id": "DEVICE-GOOD", "telemetry": {"power": 12.5}, "dynamic_fields": {"power": 12.5}},
                    {"device_id": "DEVICE-BAD", "telemetry": {"power": 18.0}, "dynamic_fields": {"power": 18.0}},
                ],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["results"][0]["success"] is True
    assert body["results"][1]["success"] is False
    assert body["results"][1]["error_code"] == "INVALID_DEVICE_METADATA"
    assert body["results"][1]["retryable"] is False
    publish_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_batch_live_update_continues_when_property_sync_degrades(monkeypatch, contract_app):
    app, session_factory = contract_app
    publish_mock = AsyncMock()
    monkeypatch.setattr("app.services.live_projection.LiveProjectionService", _ProjectionMixedBatch)
    monkeypatch.setattr("app.services.device_property.DevicePropertyService", _PropertyServiceFailure)
    monkeypatch.setattr(devices_api.fleet_stream_broadcaster, "publish", publish_mock)

    async with session_factory() as session:
        session.add(
            Device(
                device_id="DEVICE-GOOD",
                tenant_id="tenant-a",
                plant_id="PLANT-1",
                device_name="Good Device",
                device_type="compressor",
                phase_type="single",
            )
        )
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/api/v1/devices/live-update/batch",
            headers={"X-Tenant-Id": "tenant-a"},
            json={
                "tenant_id": "tenant-a",
                "updates": [
                    {"device_id": "DEVICE-GOOD", "telemetry": {"power": 12.5}, "dynamic_fields": {"power": 12.5}},
                ],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["results"][0]["success"] is True
    assert body["property_sync_warning"] == "property sync deadlock"
    publish_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_batch_live_update_skips_stale_samples_without_regressing_state(monkeypatch, contract_app):
    app, session_factory = contract_app
    publish_mock = AsyncMock()
    monkeypatch.setattr(devices_api.fleet_stream_broadcaster, "publish", publish_mock)
    now = datetime.now(timezone.utc)
    newer_ts = now.replace(microsecond=0)
    older_ts = newer_ts.replace(second=max(newer_ts.second - 4, 0))

    async with session_factory() as session:
        session.add(
            Device(
                device_id="DEVICE-STALE",
                tenant_id="tenant-a",
                plant_id="PLANT-1",
                device_name="Stale Device",
                device_type="compressor",
                phase_type="single",
                created_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/api/v1/devices/live-update/batch",
            headers={"X-Tenant-Id": "tenant-a"},
            json={
                "tenant_id": "tenant-a",
                "updates": [
                    {
                        "device_id": "DEVICE-STALE",
                        "telemetry": {
                            "timestamp": newer_ts.isoformat().replace("+00:00", "Z"),
                            "power": 200.0,
                            "current": 0.8,
                            "voltage": 230.0,
                        },
                    },
                    {
                        "device_id": "DEVICE-STALE",
                        "telemetry": {
                            "timestamp": older_ts.isoformat().replace("+00:00", "Z"),
                            "power": 100.0,
                            "current": 0.1,
                            "voltage": 230.0,
                        },
                    },
                ],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert [item["success"] for item in body["results"]] == [True, True]
    assert body["results"][0]["device"]["device_id"] == "DEVICE-STALE"
    assert body["results"][1]["device"]["device_id"] == "DEVICE-STALE"
    assert body["results"][1]["device"]["last_seen_timestamp"] == newer_ts.isoformat()
    assert body["results"][0]["device"]["version"] == body["results"][1]["device"]["version"]
    publish_mock.assert_awaited_once()
