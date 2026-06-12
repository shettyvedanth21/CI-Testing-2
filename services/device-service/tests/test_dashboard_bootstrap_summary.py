from __future__ import annotations

from datetime import datetime, time, timezone
from pathlib import Path
import os
import sys
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

BASE_DIR = Path(__file__).resolve().parents[1]
SERVICES_DIR = Path(__file__).resolve().parents[2]
PROJECT_ROOT = Path(__file__).resolve().parents[3]
for path in (BASE_DIR, SERVICES_DIR, PROJECT_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.environ.setdefault("DATABASE_URL", "mysql+aiomysql://test:test@127.0.0.1:3306/test_db")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

from app.database import Base
from app.models.device import Device, DeviceLiveState, DeviceShift
from app.services.live_dashboard import LiveDashboardService


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


def _all_day_shift(device_id: str, tenant_id: str) -> DeviceShift:
    return DeviceShift(
        device_id=device_id,
        tenant_id=tenant_id,
        shift_name="All Day",
        shift_start=time(0, 0),
        shift_end=time(0, 0),
        maintenance_break_minutes=0,
        day_of_week=None,
        is_active=True,
    )


@pytest.mark.asyncio
async def test_summary_returns_fresh_runtime_status(session_factory):
    async with session_factory() as session:
        now = datetime.now(timezone.utc)
        local_day = now.astimezone(ZoneInfo("Asia/Kolkata")).date()
        session.add(
            Device(
                device_id="DEVICE-S1",
                tenant_id="TENANT-A",
                plant_id="PLANT-1",
                device_name="Summary Tester",
                device_type="motor",
                location="Bay 1",
                first_telemetry_timestamp=now,
                last_seen_timestamp=now,
            )
        )
        session.add(
            DeviceLiveState(
                device_id="DEVICE-S1",
                tenant_id="TENANT-A",
                runtime_status="running",
                load_state="running",
                last_telemetry_ts=now,
                last_current_a=12.5,
                last_voltage_v=230.1,
                health_score=88.0,
                uptime_percentage=95.3,
                today_uptime_percentage=95.3,
                current_shift_uptime_percentage=15.71,
                today_idle_kwh=0.4,
                today_offhours_kwh=0.3,
                today_overconsumption_kwh=0.8,
                today_loss_kwh=1.5,
                today_loss_cost_inr=15.0,
                today_energy_kwh=12.0,
                day_bucket=local_day,
                version=42,
            )
        )
        session.add(_all_day_shift("DEVICE-S1", "TENANT-A"))
        await session.commit()

        svc = LiveDashboardService(session)
        result = await svc.get_dashboard_bootstrap_summary("DEVICE-S1", "TENANT-A")

    assert result["success"] is True
    assert result["device_id"] == "DEVICE-S1"
    assert result["device_name"] == "Summary Tester"
    assert result["device_type"] == "motor"
    assert result["runtime_status"] == "running"
    assert result["load_state"] == "running"
    assert result["health_score"] == 88.0
    assert result["uptime_percentage"] == 15.71
    assert result["current_shift_uptime_percentage"] == 15.71
    assert result["daily_uptime_percentage"] == 95.3
    assert result["version"] == 42
    assert result["last_current_a"] == 12.5
    assert result["last_voltage_v"] == 230.1
    assert result["loss_overview"]["total_loss_kwh"] == 1.5
    assert result["loss_overview"]["total_loss_cost_inr"] == 15.0
    assert result["loss_overview"]["today_energy_kwh"] == 12.0
    assert result["overview_readiness"]["summary_ready"] is True
    assert result["overview_readiness"]["health_ready"] is True
    assert result["overview_readiness"]["uptime_ready"] is True
    assert result["overview_readiness"]["loss_ready"] is True


@pytest.mark.asyncio
async def test_summary_resolves_stale_runtime_as_stopped(session_factory):
    async with session_factory() as session:
        stale_ts = datetime(2020, 1, 1, tzinfo=timezone.utc)
        session.add(
            Device(
                device_id="DEVICE-S2",
                tenant_id="TENANT-A",
                plant_id="PLANT-1",
                device_name="Stale Device",
                device_type="pump",
                location="Bay 2",
                last_seen_timestamp=stale_ts,
            )
        )
        session.add(
            DeviceLiveState(
                device_id="DEVICE-S2",
                tenant_id="TENANT-A",
                runtime_status="running",
                load_state="running",
                last_telemetry_ts=stale_ts,
                version=1,
            )
        )
        await session.commit()

        svc = LiveDashboardService(session)
        result = await svc.get_dashboard_bootstrap_summary("DEVICE-S2", "TENANT-A")

    assert result["runtime_status"] == "stopped"
    assert result["load_state"] == "unknown"


@pytest.mark.asyncio
async def test_summary_raises_on_missing_device(session_factory):
    async with session_factory() as session:
        svc = LiveDashboardService(session)
        from app.services.dashboard import DashboardDeviceNotFoundError

        with pytest.raises(DashboardDeviceNotFoundError):
            await svc.get_dashboard_bootstrap_summary("DEVICE-NOPE", "TENANT-A")


@pytest.mark.asyncio
async def test_summary_no_cross_tenant_access(session_factory):
    async with session_factory() as session:
        now = datetime.now(timezone.utc)
        session.add(
            Device(
                device_id="DEVICE-PRIVATE",
                tenant_id="TENANT-A",
                plant_id="PLANT-1",
                device_name="Private Machine",
                device_type="compressor",
                location="Secure Bay",
                last_seen_timestamp=now,
            )
        )
        session.add(
            DeviceLiveState(
                device_id="DEVICE-PRIVATE",
                tenant_id="TENANT-A",
                runtime_status="running",
                load_state="idle",
                last_telemetry_ts=now,
                version=5,
            )
        )
        await session.commit()

        svc = LiveDashboardService(session)
        from app.services.dashboard import DashboardDeviceNotFoundError

        with pytest.raises(DashboardDeviceNotFoundError):
            await svc.get_dashboard_bootstrap_summary("DEVICE-PRIVATE", "TENANT-B")


@pytest.mark.asyncio
async def test_summary_with_no_live_state(session_factory):
    async with session_factory() as session:
        session.add(
            Device(
                device_id="DEVICE-NOLS",
                tenant_id="TENANT-A",
                plant_id="PLANT-1",
                device_name="No Live State",
                device_type="fan",
                location="Bay 3",
            )
        )
        await session.commit()

        svc = LiveDashboardService(session)
        result = await svc.get_dashboard_bootstrap_summary("DEVICE-NOLS", "TENANT-A")

    assert result["device_id"] == "DEVICE-NOLS"
    assert result["runtime_status"] == "stopped"
    assert result["load_state"] == "unknown"
    assert result["health_score"] is None
    assert result["uptime_percentage"] is None
    assert result["current_shift_uptime_percentage"] is None
    assert result["daily_uptime_percentage"] is None
    assert result["version"] == 0
    assert result["loss_overview"]["total_loss_kwh"] == 0.0
    assert result["overview_readiness"]["health_ready"] is False
    assert result["overview_readiness"]["uptime_ready"] is False
    assert result["overview_readiness"]["loss_ready"] is False


@pytest.mark.asyncio
async def test_summary_keeps_shift_uptime_separate_from_daily_uptime(session_factory):
    async with session_factory() as session:
        now = datetime.now(timezone.utc)
        local_day = now.astimezone(ZoneInfo("Asia/Kolkata")).date()
        session.add(
            Device(
                device_id="DEVICE-S3",
                tenant_id="TENANT-A",
                plant_id="PLANT-1",
                device_name="Semantic Split",
                device_type="motor",
                location="Bay 4",
                first_telemetry_timestamp=now,
                last_seen_timestamp=now,
            )
        )
        session.add(
            DeviceLiveState(
                device_id="DEVICE-S3",
                tenant_id="TENANT-A",
                runtime_status="running",
                load_state="running",
                last_telemetry_ts=now,
                uptime_percentage=100.0,
                today_uptime_percentage=100.0,
                current_shift_uptime_percentage=15.71,
                day_bucket=local_day,
                version=7,
            )
        )
        session.add(_all_day_shift("DEVICE-S3", "TENANT-A"))
        await session.commit()

        svc = LiveDashboardService(session)
        result = await svc.get_dashboard_bootstrap_summary("DEVICE-S3", "TENANT-A")

    assert result["uptime_percentage"] == 15.71
    assert result["current_shift_uptime_percentage"] == 15.71
    assert result["daily_uptime_percentage"] == 100.0
    assert result["overview_readiness"]["uptime_ready"] is True


@pytest.mark.asyncio
async def test_summary_keeps_no_active_shift_truthful_even_when_daily_uptime_exists(session_factory):
    async with session_factory() as session:
        now = datetime.now(timezone.utc)
        local_day = now.astimezone(ZoneInfo("Asia/Kolkata")).date()
        session.add(
            Device(
                device_id="DEVICE-S4",
                tenant_id="TENANT-A",
                plant_id="PLANT-1",
                device_name="Outside Shift",
                device_type="motor",
                location="Bay 5",
                first_telemetry_timestamp=now,
                last_seen_timestamp=now,
            )
        )
        session.add(
            DeviceLiveState(
                device_id="DEVICE-S4",
                tenant_id="TENANT-A",
                runtime_status="running",
                load_state="running",
                last_telemetry_ts=now,
                uptime_percentage=100.0,
                today_uptime_percentage=100.0,
                current_shift_uptime_percentage=None,
                day_bucket=local_day,
                version=8,
            )
        )
        await session.commit()

        svc = LiveDashboardService(session)
        result = await svc.get_dashboard_bootstrap_summary("DEVICE-S4", "TENANT-A")

    assert result["uptime_percentage"] is None
    assert result["current_shift_uptime_percentage"] is None
    assert result["daily_uptime_percentage"] == 100.0
    assert result["overview_readiness"]["uptime_ready"] is False


@pytest.mark.asyncio
async def test_summary_suppresses_stale_current_shift_uptime_when_shift_inactive(session_factory):
    async with session_factory() as session:
        now = datetime.now(timezone.utc)
        local_now = now.astimezone(ZoneInfo("Asia/Kolkata"))
        local_day = local_now.date()
        inactive_weekday = (local_now.weekday() + 1) % 7
        session.add(
            Device(
                device_id="DEVICE-S5",
                tenant_id="TENANT-A",
                plant_id="PLANT-1",
                device_name="Stale Shift Uptime",
                device_type="motor",
                location="Bay 6",
                first_telemetry_timestamp=now,
                last_seen_timestamp=now,
            )
        )
        session.add(
            DeviceShift(
                device_id="DEVICE-S5",
                tenant_id="TENANT-A",
                shift_name="Different Day",
                shift_start=time(0, 0),
                shift_end=time(0, 0),
                maintenance_break_minutes=0,
                day_of_week=inactive_weekday,
                is_active=True,
            )
        )
        session.add(
            DeviceLiveState(
                device_id="DEVICE-S5",
                tenant_id="TENANT-A",
                runtime_status="running",
                load_state="idle",
                last_telemetry_ts=now,
                uptime_percentage=100.0,
                today_uptime_percentage=100.0,
                current_shift_uptime_percentage=79.5,
                day_bucket=local_day,
                version=9,
            )
        )
        await session.commit()

        svc = LiveDashboardService(session)
        result = await svc.get_dashboard_bootstrap_summary("DEVICE-S5", "TENANT-A")

    assert result["uptime_percentage"] is None
    assert result["current_shift_uptime_percentage"] is None
    assert result["daily_uptime_percentage"] == 100.0
    assert result["overview_readiness"]["uptime_ready"] is False
