from __future__ import annotations

import asyncio
from datetime import datetime, time as datetime_time, timezone
from pathlib import Path
import os
import sys
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
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
from app.models.device import Device, DeviceLiveState, DeviceRecentTelemetrySample, DeviceShift
from app.services.dashboard import DashboardService
from app.services.live_dashboard import LiveDashboardService
from app.services.shift import ShiftService
from services.shared.tenant_context import TenantContext


def _tenant_ctx() -> TenantContext:
    return TenantContext(
        tenant_id="TENANT-A",
        user_id="tester",
        role="system",
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


@pytest.mark.asyncio
async def test_calculate_uptime_uses_preloaded_shifts_without_refetch(monkeypatch: pytest.MonkeyPatch):
    service = ShiftService(session=None)  # type: ignore[arg-type]
    get_shifts = AsyncMock(side_effect=AssertionError("calculate_uptime should not refetch shifts when preloaded shifts are provided"))
    monkeypatch.setattr(service, "get_shifts_by_device", get_shifts)

    result = await service.calculate_uptime(
        "DEVICE-1",
        "TENANT-A",
        shifts=[],
    )

    assert result["device_id"] == "DEVICE-1"
    assert result["shifts_configured"] == 0
    assert "No shifts configured" in result["message"]
    get_shifts.assert_not_awaited()


@pytest.mark.asyncio
async def test_dashboard_bootstrap_reuses_loaded_shifts_for_uptime(monkeypatch: pytest.MonkeyPatch, session_factory):
    async with session_factory() as session:
        now = datetime.now(timezone.utc)
        session.add(
            Device(
                device_id="DEVICE-1",
                tenant_id="TENANT-A",
                plant_id="PLANT-1",
                device_name="Machine 1",
                device_type="compressor",
                location="Plant 1",
                first_telemetry_timestamp=now,
                last_seen_timestamp=now,
            )
        )
        await session.commit()

        fake_shifts = [
            SimpleNamespace(
                id=1,
                device_id="DEVICE-1",
                tenant_id="TENANT-A",
                shift_name="Day",
                shift_start=None,
                shift_end=None,
                maintenance_break_minutes=0,
                day_of_week=None,
                is_active=True,
                created_at=now,
                updated_at=now,
            )
        ]

        monkeypatch.setattr(
            DashboardService,
            "_http_get_json",
            AsyncMock(return_value=({"data": {"items": []}}, None)),
        )

        get_shifts = AsyncMock(return_value=fake_shifts)
        captured: dict[str, object] = {}

        async def fake_calculate_uptime(self, device_id: str, tenant_id: str | None = None, shifts=None):
            captured["device_id"] = device_id
            captured["tenant_id"] = tenant_id
            captured["shifts"] = shifts
            return {
                "device_id": device_id,
                "uptime_percentage": 91.0,
                "total_planned_minutes": 60,
                "total_effective_minutes": 60,
                "actual_running_minutes": 55,
                "shifts_configured": len(shifts or []),
                "window_start": None,
                "window_end": None,
                "window_timezone": "Asia/Kolkata",
                "data_coverage_pct": 100.0,
                "data_quality": "high",
                "calculation_mode": "runtime_telemetry_shift_window",
                "message": "ok",
            }

        monkeypatch.setattr("app.services.shift.ShiftService.get_shifts_by_device", get_shifts)
        monkeypatch.setattr("app.services.shift.ShiftService.calculate_uptime", fake_calculate_uptime)
        monkeypatch.setattr(
            "app.services.health_config.HealthConfigService.get_health_configs_by_device",
            AsyncMock(return_value=[]),
        )
        monkeypatch.setattr(
            "app.services.device_property.DevicePropertyService.get_dashboard_widget_config",
            AsyncMock(return_value={"selected_fields": []}),
        )
        monkeypatch.setattr(
            "app.services.idle_running.IdleRunningService.get_idle_config",
            AsyncMock(return_value=None),
        )
        get_current_state = AsyncMock(return_value={"state": "idle"})
        get_idle_stats = AsyncMock(return_value={"today": None, "month": None})
        monkeypatch.setattr("app.services.idle_running.IdleRunningService.get_current_state", get_current_state)
        monkeypatch.setattr("app.services.idle_running.IdleRunningService.get_idle_stats", get_idle_stats)
        monkeypatch.setattr(
            "app.services.idle_running.IdleRunningService.get_waste_config",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(
            "app.services.dashboard.DashboardService.get_device_loss_stats_from_device",
            AsyncMock(return_value={"today": {"total_loss_kwh": 0.0}}),
        )

        payload = await DashboardService(session, _tenant_ctx()).get_dashboard_bootstrap("DEVICE-1", "TENANT-A")

    assert payload["uptime"]["uptime_percentage"] == 91.0
    assert payload["shifts"][0]["shift_name"] == "Day"
    assert captured["device_id"] == "DEVICE-1"
    assert captured["tenant_id"] == "TENANT-A"
    assert captured["shifts"] is fake_shifts
    get_shifts.assert_awaited_once()
    get_current_state.assert_not_awaited()
    get_idle_stats.assert_not_awaited()


@pytest.mark.asyncio
async def test_dashboard_bootstrap_reads_recent_telemetry_from_mysql_without_data_service(
    monkeypatch: pytest.MonkeyPatch,
    session_factory,
):
    async with session_factory() as session:
        now = datetime.now(timezone.utc)
        session.add(
            Device(
                device_id="DEVICE-1",
                tenant_id="TENANT-A",
                plant_id="PLANT-1",
                device_name="Machine 1",
                device_type="compressor",
                location="Plant 1",
                first_telemetry_timestamp=now,
                last_seen_timestamp=now,
            )
        )
        session.add(
            DeviceLiveState(
                device_id="DEVICE-1",
                tenant_id="TENANT-A",
                last_telemetry_ts=now,
                runtime_status="running",
                load_state="running",
                last_current_a=12.0,
                last_voltage_v=230.0,
                version=4,
                updated_at=now,
            )
        )
        session.add(
            DeviceRecentTelemetrySample(
                device_id="DEVICE-1",
                tenant_id="TENANT-A",
                sample_ts=now,
                projection_version=4,
                runtime_status="running",
                load_state="running",
                current_band="in_load",
                telemetry_json='{"timestamp":"2026-05-01T10:00:00Z","device_id":"DEVICE-1","power":4200,"current":12.0,"voltage":230.0}',
                created_at=now,
            )
        )
        await session.commit()

        async def fail_data_service_call(*args, **kwargs):
            raise AssertionError("machine bootstrap live path must not call data-service")

        monkeypatch.setattr(DashboardService, "_http_get_json", fail_data_service_call)
        monkeypatch.setattr(
            "app.services.shift.ShiftService.get_shifts_by_device",
            AsyncMock(return_value=[]),
        )
        monkeypatch.setattr(
            "app.services.health_config.HealthConfigService.get_health_configs_by_device",
            AsyncMock(return_value=[]),
        )
        monkeypatch.setattr(
            "app.services.device_property.DevicePropertyService.get_dashboard_widget_config",
            AsyncMock(return_value={"selected_fields": []}),
        )
        monkeypatch.setattr(
            "app.services.idle_running.IdleRunningService.get_idle_config",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(
            "app.services.idle_running.IdleRunningService.get_waste_config",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(
            "app.services.dashboard.DashboardService.get_device_loss_stats_from_device",
            AsyncMock(return_value={"today": {"total_loss_kwh": 0.0}}),
        )

        payload = await DashboardService(session, _tenant_ctx()).get_dashboard_bootstrap("DEVICE-1", "TENANT-A")

    assert payload["success"] is True
    assert payload["telemetry"][0]["device_id"] == "DEVICE-1"
    assert payload["telemetry"][0]["power"] == 4200
    assert payload["telemetry_business"]["business_power_w"] == 4200
    assert payload["current_state"]["state"] == "running"


@pytest.mark.asyncio
async def test_dashboard_bootstrap_degrades_loss_stats_without_failing_hydration(
    monkeypatch: pytest.MonkeyPatch,
    session_factory,
):
    async with session_factory() as session:
        now = datetime.now(timezone.utc)
        session.add(
            Device(
                device_id="DEVICE-1",
                tenant_id="TENANT-A",
                plant_id="PLANT-1",
                device_name="Machine 1",
                device_type="compressor",
                location="Plant 1",
                first_telemetry_timestamp=now,
                last_seen_timestamp=now,
            )
        )
        await session.commit()

        async def slow_telemetry(*args, **kwargs):
            await asyncio.sleep(0.15)
            return {"data": {"items": []}}, None

        async def slow_shifts(*args, **kwargs):
            await asyncio.sleep(0.15)
            return []

        async def slow_health(*args, **kwargs):
            await asyncio.sleep(0.15)
            return []

        async def slow_uptime(self, device_id: str, tenant_id: str | None = None, shifts=None):
            return {
                "device_id": device_id,
                "uptime_percentage": None,
                "total_planned_minutes": 0,
                "total_effective_minutes": 0,
                "actual_running_minutes": 0,
                "shifts_configured": len(shifts or []),
                "window_start": None,
                "window_end": None,
                "window_timezone": "Asia/Kolkata",
                "data_coverage_pct": 0.0,
                "data_quality": "low",
                "calculation_mode": "runtime_telemetry_shift_window",
                "message": "No shifts configured",
            }

        monkeypatch.setattr(DashboardService, "_http_get_json", slow_telemetry)
        monkeypatch.setattr("app.services.shift.ShiftService.get_shifts_by_device", slow_shifts)
        monkeypatch.setattr("app.services.shift.ShiftService.calculate_uptime", slow_uptime)
        monkeypatch.setattr("app.services.health_config.HealthConfigService.get_health_configs_by_device", slow_health)
        monkeypatch.setattr(
            "app.services.device_property.DevicePropertyService.get_dashboard_widget_config",
            AsyncMock(return_value={"selected_fields": []}),
        )
        monkeypatch.setattr("app.services.idle_running.IdleRunningService.get_idle_config", AsyncMock(return_value=None))
        get_current_state = AsyncMock(return_value=None)
        get_idle_stats = AsyncMock(return_value={"today": None, "month": None})
        monkeypatch.setattr("app.services.idle_running.IdleRunningService.get_current_state", get_current_state)
        monkeypatch.setattr("app.services.idle_running.IdleRunningService.get_idle_stats", get_idle_stats)
        monkeypatch.setattr("app.services.idle_running.IdleRunningService.get_waste_config", AsyncMock(return_value=None))

        async def fail_loss(*args, **kwargs):
            raise TimeoutError("loss stats cold path timeout")

        monkeypatch.setattr(DashboardService, "get_device_loss_stats_from_device", fail_loss)

        start = time.perf_counter()
        payload = await DashboardService(session, _tenant_ctx()).get_dashboard_bootstrap("DEVICE-1", "TENANT-A")
        elapsed = time.perf_counter() - start

    assert payload["success"] is True
    assert payload["loss_stats"] is None
    assert payload["telemetry"] == []
    assert elapsed < 0.45
    get_current_state.assert_not_awaited()
    get_idle_stats.assert_not_awaited()


@pytest.mark.asyncio
async def test_dashboard_bootstrap_summary_is_projection_backed_and_does_not_touch_heavy_bootstrap_paths(
    monkeypatch: pytest.MonkeyPatch,
    session_factory,
):
    async with session_factory() as session:
        now = datetime.now(timezone.utc)
        local_day = now.astimezone(ZoneInfo("Asia/Kolkata")).date()
        session.add(
            Device(
                device_id="DEVICE-FAST",
                tenant_id="TENANT-A",
                plant_id="PLANT-1",
                device_name="Fast Machine",
                device_type="compressor",
                location="Plant 1",
                first_telemetry_timestamp=now,
                last_seen_timestamp=now,
            )
        )
        session.add(
            DeviceLiveState(
                device_id="DEVICE-FAST",
                tenant_id="TENANT-A",
                last_telemetry_ts=now,
                runtime_status="running",
                load_state="running",
                health_score=91.5,
                uptime_percentage=87.3,
                today_uptime_percentage=87.3,
                current_shift_uptime_percentage=22.4,
                today_idle_kwh=0.2,
                today_offhours_kwh=0.1,
                today_overconsumption_kwh=0.3,
                today_loss_kwh=0.6,
                today_loss_cost_inr=12.0,
                today_energy_kwh=5.5,
                day_bucket=local_day,
                last_current_a=3.4,
                last_voltage_v=231.0,
                version=14,
                updated_at=now,
            )
        )
        session.add(
            DeviceShift(
                device_id="DEVICE-FAST",
                tenant_id="TENANT-A",
                shift_name="All Day",
                shift_start=datetime_time(0, 0),
                shift_end=datetime_time(0, 0),
                maintenance_break_minutes=0,
                day_of_week=None,
                is_active=True,
            )
        )
        await session.commit()

        async def fail_if_called(*args, **kwargs):
            raise AssertionError("heavy bootstrap path should not be touched by summary reads")

        monkeypatch.setattr(DashboardService, "get_dashboard_bootstrap", fail_if_called)
        monkeypatch.setattr("app.services.idle_running.IdleRunningService.get_current_state", fail_if_called)
        monkeypatch.setattr("app.services.idle_running.IdleRunningService.get_idle_stats", fail_if_called)
        monkeypatch.setattr(DashboardService, "get_device_loss_stats_from_device", fail_if_called)

        payload = await LiveDashboardService(session, _tenant_ctx()).get_dashboard_bootstrap_summary(
            "DEVICE-FAST",
            "TENANT-A",
        )

    assert payload["device_id"] == "DEVICE-FAST"
    assert payload["device_name"] == "Fast Machine"
    assert payload["runtime_status"] == "running"
    assert payload["load_state"] == "running"
    assert payload["operational_status"] == "running"
    assert payload["health_score"] == 91.5
    assert payload["uptime_percentage"] == 22.4
    assert payload["current_shift_uptime_percentage"] == 22.4
    assert payload["daily_uptime_percentage"] == 87.3
    assert payload["version"] == 14
    assert payload["live_updated_at"] == now.isoformat()
    assert payload["loss_overview"]["total_loss_kwh"] == 0.6
    assert payload["overview_readiness"]["loss_ready"] is True
