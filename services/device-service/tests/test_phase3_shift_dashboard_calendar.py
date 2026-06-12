from __future__ import annotations

import os
import sys
import json
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
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
from app.models.device import Device, DeviceLiveState, DeviceRecentTelemetrySample
from app.schemas.device import ShiftCreate
from app.services import shift as shift_module
from app.services.dashboard import DashboardService
from app.services.shift import ShiftOverlapError, ShiftService
from services.shared.tenant_context import TenantContext


def _tenant_ctx(tenant_id: str = "TENANT-A") -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        user_id="tester",
        role="org_admin",
        plant_ids=[],
        is_super_admin=False,
    )


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


async def _seed_device(session, *, device_id: str = "DEVICE-1", tenant_id: str = "TENANT-A") -> Device:
    device = Device(
        device_id=device_id,
        tenant_id=tenant_id,
        plant_id="PLANT-1",
        device_name="Compressor",
        device_type="compressor",
        data_source_type="metered",
    )
    session.add(device)
    await session.commit()
    return device


@pytest.mark.asyncio
async def test_shift_service_rejects_overnight_overlap_on_following_day(session_factory):
    async with session_factory() as session:
        await _seed_device(session)
        service = ShiftService(session)

        first = await service.create_shift(
            ShiftCreate(
                device_id="DEVICE-1",
                tenant_id="TENANT-A",
                shift_name="Night A",
                shift_start=time(22, 0),
                shift_end=time(2, 0),
                maintenance_break_minutes=0,
                day_of_week=0,
                is_active=True,
            )
        )

        with pytest.raises(ShiftOverlapError) as exc:
            await service.create_shift(
                ShiftCreate(
                    device_id="DEVICE-1",
                    tenant_id="TENANT-A",
                    shift_name="Night B",
                    shift_start=time(1, 0),
                    shift_end=time(3, 0),
                    maintenance_break_minutes=0,
                    day_of_week=1,
                    is_active=True,
                )
            )

    assert first.id is not None
    assert exc.value.conflicts[0]["shift_name"] == "Night A"


@pytest.mark.asyncio
async def test_shift_service_rejects_cross_tenant_device_reference(session_factory):
    async with session_factory() as session:
        await _seed_device(session, device_id="DEVICE-FOREIGN", tenant_id="TENANT-A")
        service = ShiftService(session)

        with pytest.raises(ValueError, match="Device 'DEVICE-FOREIGN' not found"):
            await service.create_shift(
                ShiftCreate(
                    device_id="DEVICE-FOREIGN",
                    tenant_id="TENANT-B",
                    shift_name="Tenant Mismatch",
                    shift_start=time(9, 0),
                    shift_end=time(17, 0),
                    maintenance_break_minutes=0,
                    day_of_week=None,
                    is_active=True,
                )
            )


@pytest.mark.asyncio
async def test_calculate_uptime_for_window_handles_overnight_shift_correctly(session_factory, monkeypatch: pytest.MonkeyPatch):
    window_start = datetime(2026, 4, 4, 19, 0, tzinfo=timezone.utc)
    window_end = datetime(2026, 4, 4, 20, 0, tzinfo=timezone.utc)

    async with session_factory() as session:
        await _seed_device(session, device_id="DEVICE-OVERNIGHT")
        service = ShiftService(session)
        await service.create_shift(
            ShiftCreate(
                device_id="DEVICE-OVERNIGHT",
                tenant_id="TENANT-A",
                shift_name="Night Shift",
                shift_start=time(22, 0),
                shift_end=time(2, 0),
                maintenance_break_minutes=0,
                day_of_week=None,
                is_active=True,
            )
        )

        async def fake_fetch(*_args, **_kwargs):
            return [
                {"timestamp": "2026-04-04T19:00:00+00:00", "active_power_kw": 6.0},
                {"timestamp": "2026-04-04T19:30:00+00:00", "active_power_kw": 6.0},
                {"timestamp": "2026-04-04T20:00:00+00:00", "active_power_kw": 6.0},
            ]

        monkeypatch.setattr(service, "_fetch_telemetry_window", fake_fetch)

        result = await service.calculate_uptime_for_window(
            "DEVICE-OVERNIGHT",
            "TENANT-A",
            window_start,
            window_end,
        )

    assert result["uptime_percentage"] == 100.0
    assert result["total_planned_minutes"] == 60
    assert result["actual_running_minutes"] == 60
    assert "Night Shift" in result["message"]


@pytest.mark.asyncio
async def test_calculate_uptime_for_window_uses_recent_projection_samples_without_data_service(session_factory, monkeypatch: pytest.MonkeyPatch):
    window_start = datetime(2026, 4, 4, 4, 10, tzinfo=timezone.utc)
    window_end = datetime(2026, 4, 4, 4, 40, tzinfo=timezone.utc)

    async with session_factory() as session:
        await _seed_device(session, device_id="DEVICE-RECENT-UPTIME")
        service = ShiftService(session)
        await service.create_shift(
            ShiftCreate(
                device_id="DEVICE-RECENT-UPTIME",
                tenant_id="TENANT-A",
                shift_name="Day Shift",
                shift_start=time(9, 0),
                shift_end=time(10, 0),
                maintenance_break_minutes=0,
                day_of_week=None,
                is_active=True,
            )
        )
        session.add_all(
            [
                DeviceRecentTelemetrySample(
                    device_id="DEVICE-RECENT-UPTIME",
                    tenant_id="TENANT-A",
                    sample_ts=datetime(2026, 4, 4, 4, 10, tzinfo=timezone.utc),
                    projection_version=1,
                    runtime_status="running",
                    load_state="running",
                    current_band="normal",
                    telemetry_json=json.dumps({"timestamp": "2026-04-04T04:10:00+00:00", "power": 10.0}),
                ),
                DeviceRecentTelemetrySample(
                    device_id="DEVICE-RECENT-UPTIME",
                    tenant_id="TENANT-A",
                    sample_ts=datetime(2026, 4, 4, 4, 20, tzinfo=timezone.utc),
                    projection_version=2,
                    runtime_status="running",
                    load_state="running",
                    current_band="normal",
                    telemetry_json=json.dumps({"timestamp": "2026-04-04T04:20:00+00:00", "power": 10.0}),
                ),
                DeviceRecentTelemetrySample(
                    device_id="DEVICE-RECENT-UPTIME",
                    tenant_id="TENANT-A",
                    sample_ts=datetime(2026, 4, 4, 4, 30, tzinfo=timezone.utc),
                    projection_version=3,
                    runtime_status="running",
                    load_state="running",
                    current_band="normal",
                    telemetry_json=json.dumps({"timestamp": "2026-04-04T04:30:00+00:00", "power": 10.0}),
                ),
            ]
        )
        await session.commit()

        monkeypatch.setattr(
            service,
            "_fetch_telemetry_window",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("data-service must not be called")),
        )

        result = await service.calculate_uptime_for_window(
            "DEVICE-RECENT-UPTIME",
            "TENANT-A",
            window_start,
            window_end,
        )

    assert result["uptime_percentage"] == 100.0
    assert result["actual_running_minutes"] == 20


@pytest.mark.asyncio
async def test_calculate_uptime_current_window_uses_runtime_seconds_without_rounding_drift(session_factory, monkeypatch: pytest.MonkeyPatch):
    fixed_now = datetime(2026, 4, 4, 4, 15, tzinfo=timezone.utc)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return fixed_now.replace(tzinfo=None)
            return fixed_now.astimezone(tz)

    async with session_factory() as session:
        await _seed_device(session, device_id="DEVICE-UPTIME")
        service = ShiftService(session)
        await service.create_shift(
            ShiftCreate(
                device_id="DEVICE-UPTIME",
                tenant_id="TENANT-A",
                shift_name="Day Shift",
                shift_start=time(9, 0),
                shift_end=time(10, 0),
                maintenance_break_minutes=0,
                day_of_week=None,
                is_active=True,
            )
        )

        async def fake_fetch(*_args, **_kwargs):
            return [
                {"timestamp": "2026-04-04T03:30:00+00:00", "active_power_kw": 6.0},
                {"timestamp": "2026-04-04T03:59:31+00:00", "active_power_kw": 0.0},
                {"timestamp": "2026-04-04T04:30:00+00:00", "active_power_kw": 0.0},
            ]

        monkeypatch.setattr(service, "_fetch_telemetry_window", fake_fetch)
        monkeypatch.setattr(shift_module, "datetime", FixedDateTime)

        result = await service.calculate_uptime("DEVICE-UPTIME", "TENANT-A")

    assert result["actual_running_minutes"] == 30
    assert result["uptime_percentage"] == 49.19


@pytest.mark.asyncio
async def test_dashboard_loss_stats_ignore_previous_day_live_state(session_factory, monkeypatch: pytest.MonkeyPatch):
    local_day = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata")).date()

    async with session_factory() as session:
        await _seed_device(session, device_id="LOSS-DEVICE")
        session.add(
            DeviceLiveState(
                device_id="LOSS-DEVICE",
                tenant_id="TENANT-A",
                runtime_status="running",
                load_state="idle",
                day_bucket=local_day - timedelta(days=1),
                today_idle_kwh=9.5,
                today_offhours_kwh=2.0,
                today_overconsumption_kwh=1.0,
                today_loss_kwh=12.5,
                today_energy_kwh=40.0,
                version=1,
            )
        )
        await session.commit()

        async def fake_get_tariff(*_args, **_kwargs):
            return 5.0, "INR"

        monkeypatch.setattr(DashboardService, "_get_tariff", fake_get_tariff)
        payload = await DashboardService(session, _tenant_ctx()).get_device_loss_stats("LOSS-DEVICE", "TENANT-A")

    assert payload["today"]["idle_kwh"] == 0.0
    assert payload["today"]["total_loss_kwh"] == 0.0
    assert payload["today"]["today_energy_kwh"] == 0.0


@pytest.mark.asyncio
async def test_monthly_energy_snapshot_respects_ist_day_boundary_and_month_rollover(session_factory, monkeypatch: pytest.MonkeyPatch):
    telemetry_rows = [
        {"timestamp": "2026-04-30T18:20:00+00:00", "active_power_kw": 6.0},
        {"timestamp": "2026-04-30T18:30:00+00:00", "active_power_kw": 6.0},
        {"timestamp": "2026-04-30T18:40:00+00:00", "active_power_kw": 6.0},
    ]

    async with session_factory() as session:
        device = await _seed_device(session, device_id="CAL-DEVICE")
        service = DashboardService(session, _tenant_ctx())

        async def fake_get_devices():
            return [device]

        async def fake_get_tariff():
            return 10.0, "INR"

        async def fake_fetch(device_id: str, start_utc: datetime, end_utc: datetime):
            assert device_id == "CAL-DEVICE"
            return [
                row
                for row in telemetry_rows
                if start_utc <= datetime.fromisoformat(row["timestamp"]) <= end_utc
            ]

        monkeypatch.setattr(service, "_get_all_devices_with_shifts", fake_get_devices)
        monkeypatch.setattr(service, "_get_tariff", fake_get_tariff)
        monkeypatch.setattr(service, "_fetch_telemetry_window", fake_fetch)

        april = await service.materialize_monthly_energy_snapshot(2026, 4)
        may = await service.materialize_monthly_energy_snapshot(2026, 5)

    april_day = next(day for day in april["days"] if day["date"] == "2026-04-30")
    may_day = next(day for day in may["days"] if day["date"] == "2026-05-01")

    assert april["summary"]["total_energy_kwh"] == 1.0
    assert april_day["energy_kwh"] == 1.0
    assert may["summary"]["total_energy_kwh"] == 1.0
    assert may_day["energy_kwh"] == 1.0


@pytest.mark.asyncio
async def test_shift_service_delete_returns_false_for_missing_shift(session_factory):
    async with session_factory() as session:
        await _seed_device(session, device_id="DEVICE-NO-SHIFT")
        service = ShiftService(session)

        deleted = await service.delete_shift(999, "DEVICE-NO-SHIFT", "TENANT-A")

    assert deleted is False


@pytest.mark.asyncio
async def test_calculate_uptime_reports_no_active_shifts_when_all_shifts_disabled(session_factory):
    async with session_factory() as session:
        await _seed_device(session, device_id="DEVICE-INACTIVE-SHIFTS")
        service = ShiftService(session)
        await service.create_shift(
            ShiftCreate(
                device_id="DEVICE-INACTIVE-SHIFTS",
                tenant_id="TENANT-A",
                shift_name="Disabled Shift",
                shift_start=time(9, 0),
                shift_end=time(17, 0),
                maintenance_break_minutes=0,
                day_of_week=None,
                is_active=False,
            )
        )

        result = await service.calculate_uptime("DEVICE-INACTIVE-SHIFTS", "TENANT-A")

    assert result["uptime_percentage"] is None
    assert result["shifts_configured"] == 1
    assert result["total_planned_minutes"] == 0
    assert "No active shifts configured" in result["message"]


@pytest.mark.asyncio
async def test_calculate_uptime_reports_no_current_shift_window_when_outside_active_shift(session_factory, monkeypatch: pytest.MonkeyPatch):
    fixed_now = datetime(2026, 4, 4, 15, 30, tzinfo=timezone.utc)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return fixed_now.replace(tzinfo=None)
            return fixed_now.astimezone(tz)

    async with session_factory() as session:
        await _seed_device(session, device_id="DEVICE-NO-CURRENT")
        service = ShiftService(session)
        await service.create_shift(
            ShiftCreate(
                device_id="DEVICE-NO-CURRENT",
                tenant_id="TENANT-A",
                shift_name="Morning Only",
                shift_start=time(9, 0),
                shift_end=time(10, 0),
                maintenance_break_minutes=0,
                day_of_week=None,
                is_active=True,
            )
        )

        monkeypatch.setattr(shift_module, "datetime", FixedDateTime)
        result = await service.calculate_uptime("DEVICE-NO-CURRENT", "TENANT-A")

    assert result["uptime_percentage"] is None
    assert result["shifts_configured"] == 1
    assert result["total_planned_minutes"] == 0
    assert "No currently active shift window at this time." in result["message"]
