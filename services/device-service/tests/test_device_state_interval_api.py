from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import os
import sys

import pytest
import pytest_asyncio
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
from app.models.device import Device, DeviceStateInterval
from services.shared.tenant_context import TenantContext


def _parse_csv(header_value: str | None) -> list[str]:
    if not header_value:
        return []
    return [value.strip() for value in header_value.split(",") if value.strip()]


@pytest_asyncio.fixture
async def state_interval_api_app(monkeypatch: pytest.MonkeyPatch):
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
                    device_id="DEVICE-A-1",
                    tenant_id="TENANT-A",
                    plant_id="PLANT-A",
                    device_name="Tenant A Primary",
                    device_type="compressor",
                    device_id_class="active",
                ),
                Device(
                    device_id="DEVICE-A-2",
                    tenant_id="TENANT-A",
                    plant_id="PLANT-B",
                    device_name="Tenant A Secondary",
                    device_type="compressor",
                    device_id_class="active",
                ),
                Device(
                    device_id="DEVICE-B-1",
                    tenant_id="TENANT-B",
                    plant_id="PLANT-Z",
                    device_name="Tenant B Device",
                    device_type="compressor",
                    device_id_class="active",
                ),
            ]
        )
        session.add_all(
            [
                DeviceStateInterval(
                    tenant_id="TENANT-A",
                    device_id="DEVICE-A-1",
                    state_type="idle",
                    started_at=datetime(2026, 4, 12, 10, 0, 0, tzinfo=timezone.utc),
                    ended_at=datetime(2026, 4, 12, 10, 10, 0, tzinfo=timezone.utc),
                    duration_sec=600,
                    is_open=False,
                    opened_by_sample_ts=datetime(2026, 4, 12, 10, 0, 0, tzinfo=timezone.utc),
                    closed_by_sample_ts=datetime(2026, 4, 12, 10, 10, 0, tzinfo=timezone.utc),
                    opened_reason="load_state_idle",
                    closed_reason="load_state_exit",
                    source="live_projection",
                ),
                DeviceStateInterval(
                    tenant_id="TENANT-A",
                    device_id="DEVICE-A-1",
                    state_type="runtime_on",
                    started_at=datetime(2026, 4, 12, 11, 0, 0, tzinfo=timezone.utc),
                    ended_at=None,
                    duration_sec=None,
                    is_open=True,
                    opened_by_sample_ts=datetime(2026, 4, 12, 11, 0, 0, tzinfo=timezone.utc),
                    closed_by_sample_ts=None,
                    opened_reason="telemetry_running",
                    closed_reason=None,
                    source="live_projection",
                ),
                DeviceStateInterval(
                    tenant_id="TENANT-A",
                    device_id="DEVICE-A-1",
                    state_type="overconsumption",
                    started_at=datetime(2026, 4, 12, 11, 10, 0, tzinfo=timezone.utc),
                    ended_at=datetime(2026, 4, 12, 11, 20, 0, tzinfo=timezone.utc),
                    duration_sec=600,
                    is_open=False,
                    opened_by_sample_ts=datetime(2026, 4, 12, 11, 10, 0, tzinfo=timezone.utc),
                    closed_by_sample_ts=datetime(2026, 4, 12, 11, 20, 0, tzinfo=timezone.utc),
                    opened_reason="current_band_overconsumption",
                    closed_reason="current_band_exit",
                    source="live_projection",
                ),
                DeviceStateInterval(
                    tenant_id="TENANT-B",
                    device_id="DEVICE-B-1",
                    state_type="runtime_on",
                    started_at=datetime(2026, 4, 12, 9, 0, 0, tzinfo=timezone.utc),
                    ended_at=None,
                    duration_sec=None,
                    is_open=True,
                    opened_by_sample_ts=datetime(2026, 4, 12, 9, 0, 0, tzinfo=timezone.utc),
                    source="live_projection",
                ),
            ]
        )
        await session.commit()

    app = FastAPI()

    @app.middleware("http")
    async def _inject_auth_context(request: Request, call_next):
        tenant_id = request.headers.get("X-Tenant-Id")
        role = request.headers.get("X-Role", "org_admin")
        plant_ids = _parse_csv(request.headers.get("X-Plant-Ids"))
        request.state.tenant_context = TenantContext(
            tenant_id=tenant_id,
            user_id="user-1",
            role=role,
            plant_ids=plant_ids,
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
            "user_id": "user-1",
            "tenant_id": request.state.tenant_context.tenant_id,
            "role": request.state.tenant_context.role,
            "plant_ids": list(request.state.tenant_context.plant_ids),
            "is_authenticated": True,
        },
    )

    try:
        yield app
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_per_device_state_interval_query_returns_expected_rows(state_interval_api_app):
    async with AsyncClient(transport=ASGITransport(app=state_interval_api_app), base_url="http://testserver") as client:
        response = await client.get(
            "/api/v1/devices/DEVICE-A-1/state-intervals",
            headers={"X-Tenant-Id": "TENANT-A", "X-Role": "org_admin"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["total"] == 3
    assert body["limit"] == 100
    assert body["offset"] == 0
    assert len(body["data"]) == 3
    first = body["data"][0]
    expected_fields = {
        "id",
        "device_id",
        "tenant_id",
        "state_type",
        "started_at",
        "ended_at",
        "duration_sec",
        "is_open",
        "opened_by_sample_ts",
        "closed_by_sample_ts",
        "opened_reason",
        "closed_reason",
        "source",
        "created_at",
        "updated_at",
    }
    assert expected_fields.issubset(set(first.keys()))
    assert first["device_id"] == "DEVICE-A-1"
    assert first["tenant_id"] == "TENANT-A"


@pytest.mark.asyncio
async def test_state_type_filter_works(state_interval_api_app):
    async with AsyncClient(transport=ASGITransport(app=state_interval_api_app), base_url="http://testserver") as client:
        response = await client.get(
            "/api/v1/devices/DEVICE-A-1/state-intervals",
            params={"state_type": "idle"},
            headers={"X-Tenant-Id": "TENANT-A", "X-Role": "org_admin"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert len(body["data"]) == 1
    assert body["data"][0]["state_type"] == "idle"


@pytest.mark.asyncio
async def test_date_range_filter_works_with_interval_overlap(state_interval_api_app):
    async with AsyncClient(transport=ASGITransport(app=state_interval_api_app), base_url="http://testserver") as client:
        response = await client.get(
            "/api/v1/devices/DEVICE-A-1/state-intervals",
            params={
                "start_time": "2026-04-12T10:05:00+00:00",
                "end_time": "2026-04-12T10:15:00+00:00",
            },
            headers={"X-Tenant-Id": "TENANT-A", "X-Role": "org_admin"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert len(body["data"]) == 1
    assert body["data"][0]["state_type"] == "idle"


@pytest.mark.asyncio
async def test_is_open_filter_works(state_interval_api_app):
    async with AsyncClient(transport=ASGITransport(app=state_interval_api_app), base_url="http://testserver") as client:
        response = await client.get(
            "/api/v1/devices/DEVICE-A-1/state-intervals",
            params={"is_open": "true"},
            headers={"X-Tenant-Id": "TENANT-A", "X-Role": "org_admin"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert len(body["data"]) == 1
    assert body["data"][0]["state_type"] == "runtime_on"
    assert body["data"][0]["is_open"] is True


@pytest.mark.asyncio
async def test_tenant_isolation_prevents_cross_tenant_reads(state_interval_api_app):
    async with AsyncClient(transport=ASGITransport(app=state_interval_api_app), base_url="http://testserver") as client:
        response = await client.get(
            "/api/v1/devices/DEVICE-B-1/state-intervals",
            headers={"X-Tenant-Id": "TENANT-A", "X-Role": "org_admin"},
        )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_out_of_scope_device_access_is_blocked(state_interval_api_app):
    async with AsyncClient(transport=ASGITransport(app=state_interval_api_app), base_url="http://testserver") as client:
        response = await client.get(
            "/api/v1/devices/DEVICE-A-2/state-intervals",
            headers={"X-Tenant-Id": "TENANT-A", "X-Role": "operator", "X-Plant-Ids": "PLANT-A"},
        )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "PLANT_ACCESS_DENIED"


@pytest.mark.asyncio
async def test_pagination_limit_offset_works(state_interval_api_app):
    async with AsyncClient(transport=ASGITransport(app=state_interval_api_app), base_url="http://testserver") as client:
        response = await client.get(
            "/api/v1/devices/DEVICE-A-1/state-intervals",
            params={"limit": 1, "offset": 1},
            headers={"X-Tenant-Id": "TENANT-A", "X-Role": "org_admin"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert body["limit"] == 1
    assert body["offset"] == 1
    assert len(body["data"]) == 1
