from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import os
import sys

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

BASE_DIR = Path(__file__).resolve().parents[1]
SERVICES_DIR = Path(__file__).resolve().parents[2]
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEVICE_SERVICE_DIR = SERVICES_DIR / "device-service"
for path in (BASE_DIR, DEVICE_SERVICE_DIR, SERVICES_DIR, PROJECT_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.environ.setdefault("DATABASE_URL", "mysql+aiomysql://test:test@127.0.0.1:3306/test_db")

from app.database import Base
from app.models import Device, DeviceLiveState
from app.services import live_projection as live_projection_module
from app.services.live_projection import LiveProjectionService


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


async def _make_service(session):
    service = LiveProjectionService(session)
    service._health = SimpleNamespace(calculate_health_score=AsyncMock(return_value={"health_score": None}))
    return service


async def _seed_device(session, *, device_id: str = "IDLE-DEVICE-1") -> None:
    session.add(
        Device(
            device_id=device_id,
            tenant_id="TENANT-A",
            plant_id="PLANT-1",
            device_name="Idle Device",
            device_type="compressor",
            full_load_current_a=20.0,
            created_at=datetime(2026, 4, 12, 0, 0, 0),
        )
    )
    await session.commit()


async def _apply_idle_sample(service: LiveProjectionService, *, device_id: str, ts: datetime) -> dict:
    return await service.apply_live_update(
        device_id=device_id,
        tenant_id="TENANT-A",
        telemetry_payload={
            "timestamp": ts.isoformat(),
            "current": 2.0,
            "voltage": 230.0,
            "power": 460.0,
        },
        dynamic_fields={"current": 2.0, "voltage": 230.0, "power": 460.0},
    )


async def _apply_running_sample(service: LiveProjectionService, *, device_id: str, ts: datetime) -> dict:
    return await service.apply_live_update(
        device_id=device_id,
        tenant_id="TENANT-A",
        telemetry_payload={
            "timestamp": ts.isoformat(),
            "current": 8.0,
            "voltage": 230.0,
            "power": 1840.0,
        },
        dynamic_fields={"current": 8.0, "voltage": 230.0, "power": 1840.0},
    )


@pytest.mark.asyncio
async def test_idle_streak_starts_on_first_idle_sample(session_factory, monkeypatch):
    async with session_factory() as session:
        await _seed_device(session)
        monkeypatch.setattr(live_projection_module.TariffCache, "get", AsyncMock(return_value={"rate": 0.0, "currency": "INR"}))
        service = await _make_service(session)

        first_ts = datetime.now(timezone.utc)
        item = await _apply_idle_sample(service, device_id="IDLE-DEVICE-1", ts=first_ts)

        live_state = await session.get(DeviceLiveState, {"device_id": "IDLE-DEVICE-1", "tenant_id": "TENANT-A"})
        assert item["load_state"] == "idle"
        assert item["idle_streak_started_at"] == first_ts.isoformat()
        assert item["idle_streak_duration_sec"] == 0
        assert live_state is not None
        assert live_state.idle_streak_started_at is not None
        assert live_state.idle_streak_started_at.replace(tzinfo=timezone.utc).isoformat() == first_ts.isoformat()
        assert live_state.idle_streak_duration_sec == 0


@pytest.mark.asyncio
async def test_idle_streak_continues_across_continuous_idle_telemetry(session_factory, monkeypatch):
    async with session_factory() as session:
        await _seed_device(session)
        monkeypatch.setattr(live_projection_module.TariffCache, "get", AsyncMock(return_value={"rate": 0.0, "currency": "INR"}))
        service = await _make_service(session)

        first_ts = datetime(2026, 4, 12, 10, 0, 0, tzinfo=timezone.utc)
        second_ts = datetime(2026, 4, 12, 10, 0, 30, tzinfo=timezone.utc)
        await _apply_idle_sample(service, device_id="IDLE-DEVICE-1", ts=first_ts)
        item = await _apply_idle_sample(service, device_id="IDLE-DEVICE-1", ts=second_ts)

        assert item["idle_streak_started_at"] == first_ts.isoformat()
        assert item["idle_streak_duration_sec"] == 30


@pytest.mark.asyncio
async def test_idle_streak_resets_on_non_idle_transition(session_factory, monkeypatch):
    async with session_factory() as session:
        await _seed_device(session)
        monkeypatch.setattr(live_projection_module.TariffCache, "get", AsyncMock(return_value={"rate": 0.0, "currency": "INR"}))
        service = await _make_service(session)

        await _apply_idle_sample(
            service,
            device_id="IDLE-DEVICE-1",
            ts=datetime.now(timezone.utc),
        )
        item = await _apply_running_sample(
            service,
            device_id="IDLE-DEVICE-1",
            ts=datetime.now(timezone.utc),
        )

        assert item["load_state"] == "running"
        assert item["idle_streak_started_at"] is None
        assert item["idle_streak_duration_sec"] == 0


@pytest.mark.asyncio
async def test_idle_streak_resets_on_telemetry_continuity_break(session_factory, monkeypatch):
    async with session_factory() as session:
        await _seed_device(session)
        monkeypatch.setattr(live_projection_module.TariffCache, "get", AsyncMock(return_value={"rate": 0.0, "currency": "INR"}))
        service = await _make_service(session)

        first_ts = datetime(2026, 4, 12, 10, 0, 0, tzinfo=timezone.utc)
        gap_ts = datetime(2026, 4, 12, 10, 1, 1, tzinfo=timezone.utc)
        await _apply_idle_sample(service, device_id="IDLE-DEVICE-1", ts=first_ts)
        item = await _apply_idle_sample(service, device_id="IDLE-DEVICE-1", ts=gap_ts)

        assert item["idle_streak_started_at"] == gap_ts.isoformat()
        assert item["idle_streak_duration_sec"] == 0


@pytest.mark.asyncio
async def test_idle_streak_survives_day_rollover_when_idle_is_continuous(session_factory, monkeypatch):
    async with session_factory() as session:
        await _seed_device(session)
        monkeypatch.setattr(live_projection_module.TariffCache, "get", AsyncMock(return_value={"rate": 0.0, "currency": "INR"}))
        service = await _make_service(session)

        first_ts = datetime(2026, 4, 12, 18, 29, 45, tzinfo=timezone.utc)  # 23:59:45 IST
        second_ts = datetime(2026, 4, 12, 18, 30, 15, tzinfo=timezone.utc)  # 00:00:15 IST next local day
        await _apply_idle_sample(service, device_id="IDLE-DEVICE-1", ts=first_ts)
        item = await _apply_idle_sample(service, device_id="IDLE-DEVICE-1", ts=second_ts)

        assert item["idle_streak_started_at"] == first_ts.isoformat()
        assert item["idle_streak_duration_sec"] == 30
