from __future__ import annotations

import os
import sys
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

BASE_DIR = Path(__file__).resolve().parents[1]
SERVICES_DIR = Path(__file__).resolve().parents[2]
for path in (BASE_DIR, SERVICES_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.environ["DATABASE_URL"] = "mysql+aiomysql://test:test@127.0.0.1:3306/test_db"

from app.database import Base
from app.models.device import Device, IdleRunningLog, DeviceRecentTelemetrySample
from app.services import idle_running
from app.services.idle_running import IdleRunningService
from services.shared.tenant_context import TenantContext


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


@pytest.mark.asyncio
async def test_idle_stats_persists_tenant_scoped_log(session_factory, monkeypatch):
    async with session_factory() as session:
        await session.execute(
            IdleRunningLog.__table__.delete().where(
                IdleRunningLog.device_id == "DASH-DEVICE",
                IdleRunningLog.tenant_id == "TENANT-1",
            )
        )
        await session.execute(
            Device.__table__.delete().where(
                Device.device_id == "DASH-DEVICE",
                Device.tenant_id == "TENANT-1",
            )
        )
        await session.commit()

        session.add(
            Device(
                device_id="DASH-DEVICE",
                tenant_id="TENANT-1",
                plant_id="PLANT-1",
                device_name="Dashboard Device",
                device_type="compressor",
                data_source_type="metered",
                full_load_current_a=4.0,
            )
        )
        await session.commit()

        async def fake_tariff_get(cls, tenant_id):
            return {"configured": True, "rate": 2.5, "currency": "INR", "stale": False, "cache": "miss"}

        async def fake_fetch_telemetry(self, *args, **kwargs):
            return []

        monkeypatch.setattr(idle_running.TariffCache, "get", classmethod(fake_tariff_get))
        monkeypatch.setattr(IdleRunningService, "_fetch_telemetry", fake_fetch_telemetry)

        async def fake_get_or_create_day_log(self, device_id: str, day_start: datetime, now_utc: datetime):
            entity = IdleRunningLog(
                id=1,
                device_id=device_id,
                tenant_id="TENANT-1",
                period_start=day_start,
                period_end=day_start,
                idle_duration_sec=0,
                idle_energy_kwh=0,
                idle_cost=0,
                currency="INR",
                tariff_rate_used=0,
                pf_estimated=False,
                created_at=now_utc,
                updated_at=now_utc,
            )
            self._session.add(entity)
            await self._session.flush()
            return entity

        monkeypatch.setattr(IdleRunningService, "_get_or_create_day_log", fake_get_or_create_day_log)

        service = IdleRunningService(
            session,
            TenantContext(
                tenant_id="TENANT-1",
                user_id="test-user",
                role="system",
                plant_ids=[],
                is_super_admin=False,
            ),
        )
        result = await service.get_idle_stats("DASH-DEVICE", "TENANT-1")

        log = (
            await session.execute(
                IdleRunningLog.__table__.select().where(
                    IdleRunningLog.device_id == "DASH-DEVICE",
                    IdleRunningLog.tenant_id == "TENANT-1",
                )
            )
        ).first()

    assert result["device_id"] == "DASH-DEVICE"
    assert log is not None
    assert log._mapping["tenant_id"] == "TENANT-1"


@pytest.mark.asyncio
async def test_aggregate_device_idle_uses_recent_projection_samples_without_data_service(session_factory, monkeypatch):
    base_now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    day_start = base_now.astimezone(idle_running._get_platform_tz()).replace(hour=0, minute=0, second=0, microsecond=0)
    async with session_factory() as session:
        session.add(
            Device(
                device_id="RECENT-IDLE",
                tenant_id="TENANT-1",
                plant_id="PLANT-1",
                device_name="Recent Idle Device",
                device_type="compressor",
                data_source_type="metered",
                full_load_current_a=4.0,
            )
        )
        session.add_all(
            [
                DeviceRecentTelemetrySample(
                    device_id="RECENT-IDLE",
                    tenant_id="TENANT-1",
                    sample_ts=base_now,
                    projection_version=1,
                    runtime_status="running",
                    load_state="idle",
                    current_band="normal",
                    telemetry_json=json.dumps({"timestamp": base_now.isoformat(), "current": 0.4, "voltage": 230.0, "power": 50.0}),
                ),
                DeviceRecentTelemetrySample(
                    device_id="RECENT-IDLE",
                    tenant_id="TENANT-1",
                    sample_ts=base_now + timedelta(minutes=1),
                    projection_version=2,
                    runtime_status="running",
                    load_state="idle",
                    current_band="normal",
                    telemetry_json=json.dumps({"timestamp": (base_now + timedelta(minutes=1)).isoformat(), "current": 0.5, "voltage": 230.0, "power": 55.0}),
                ),
            ]
        )
        session.add(
            IdleRunningLog(
                device_id="RECENT-IDLE",
                tenant_id="TENANT-1",
                period_start=day_start,
                period_end=base_now,
                idle_duration_sec=0,
                idle_energy_kwh=0,
                idle_cost=0,
                currency="INR",
                tariff_rate_used=0,
                pf_estimated=False,
                created_at=base_now,
                updated_at=base_now,
            )
        )
        await session.commit()

        async def fake_tariff_get(cls, tenant_id):
            return {"configured": True, "rate": 2.5, "currency": "INR", "stale": False, "cache": "miss"}

        monkeypatch.setattr(idle_running.TariffCache, "get", classmethod(fake_tariff_get))
        monkeypatch.setattr(
            IdleRunningService,
            "_fetch_telemetry",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("data-service must not be called")),
        )
        monkeypatch.setattr(
            idle_running,
            "aggregate_window",
            lambda *_args, **_kwargs: SimpleNamespace(
                total=SimpleNamespace(idle_duration_sec=300, idle_kwh=0.25, pf_estimated=False)
            ),
        )

        service = IdleRunningService(
            session,
            TenantContext(
                tenant_id="TENANT-1",
                user_id="test-user",
                role="system",
                plant_ids=[],
                is_super_admin=False,
            ),
        )
        device = await service._get_device("RECENT-IDLE", "TENANT-1")
        assert device is not None

        await service.aggregate_device_idle(device, tenant_id="TENANT-1", now_utc=base_now + timedelta(minutes=2))
        await session.commit()

        row = (
            await session.execute(
                IdleRunningLog.__table__.select().where(
                    IdleRunningLog.device_id == "RECENT-IDLE",
                    IdleRunningLog.tenant_id == "TENANT-1",
                )
            )
        ).first()

    assert row is not None
    assert float(row._mapping["idle_energy_kwh"]) == pytest.approx(0.25, abs=1e-6)
