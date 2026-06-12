from __future__ import annotations

import os
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
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

os.environ["DATABASE_URL"] = "mysql+aiomysql://test:test@127.0.0.1:3306/test_db"

from app.database import Base
from app.models.device import Device, DeviceLiveState, DeviceShift, ParameterHealthConfig
from app.services.idle_running import TariffCache
from app.services.live_dashboard import LiveDashboardService
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
async def test_live_dashboard_summary_is_tenant_scoped_without_loading_full_fleet_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    session_factory,
):
    local_day = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata")).date()
    async with session_factory() as session:
        session.add_all(
            [
                Device(
                    device_id="SHARED-DEVICE-A",
                    tenant_id="TENANT-A",
                    plant_id="PLANT-1",
                    device_name="Shared A",
                    device_type="compressor",
                    data_source_type="metered",
                ),
                Device(
                    device_id="SHARED-DEVICE-B",
                    tenant_id="TENANT-B",
                    plant_id="PLANT-1",
                    device_name="Shared B",
                    device_type="compressor",
                    data_source_type="metered",
                ),
                DeviceLiveState(
                    device_id="SHARED-DEVICE-A",
                    tenant_id="TENANT-A",
                    runtime_status="running",
                    load_state="running",
                    health_score=82.5,
                    uptime_percentage=94.0,
                    day_bucket=local_day,
                    today_energy_kwh=14.5,
                    today_loss_kwh=1.5,
                    month_energy_kwh=70.0,
                    last_telemetry_ts=datetime.now(timezone.utc),
                ),
                DeviceLiveState(
                    device_id="SHARED-DEVICE-B",
                    tenant_id="TENANT-B",
                    runtime_status="stopped",
                    health_score=41.0,
                    uptime_percentage=52.0,
                    day_bucket=local_day,
                    today_energy_kwh=9.0,
                    today_loss_kwh=0.5,
                    month_energy_kwh=33.0,
                ),
                DeviceShift(
                    device_id="SHARED-DEVICE-A",
                    tenant_id="TENANT-A",
                    shift_name="A",
                    shift_start=time(8, 0),
                    shift_end=time(16, 0),
                    maintenance_break_minutes=30,
                    is_active=True,
                ),
                ParameterHealthConfig(
                    device_id="SHARED-DEVICE-A",
                    tenant_id="TENANT-A",
                    parameter_name="current",
                    canonical_parameter_name="current",
                    normal_min=10,
                    normal_max=20,
                    weight=100,
                    ignore_zero_value=False,
                    is_active=True,
                ),
            ]
        )
        await session.commit()

        monkeypatch.setattr(
            LiveDashboardService,
            "get_fleet_snapshot",
            AsyncMock(side_effect=AssertionError("summary should not load full fleet snapshot")),
        )
        monkeypatch.setattr(
            LiveDashboardService,
            "_fetch_energy_json",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(
            TariffCache,
            "get",
            AsyncMock(return_value={"configured": True, "rate": 10.0, "currency": "INR"}),
        )

        payload = await LiveDashboardService(session).get_dashboard_summary(tenant_id="TENANT-A")

    assert payload["summary"]["total_devices"] == 1
    assert payload["summary"]["running_devices"] == 1
    assert payload["summary"]["stopped_devices"] == 0
    assert payload["summary"]["idle_devices"] == 0
    assert payload["summary"]["in_load_devices"] == 1
    assert payload["summary"]["overconsumption_devices"] == 0
    assert payload["summary"]["unknown_devices"] == 0
    assert payload["summary"]["status_counts"] == {
        "unknown": 0,
        "stopped": 0,
        "idle": 0,
        "running": 1,
        "overconsumption": 0,
    }
    assert payload["summary"]["devices_with_health_data"] == 1
    assert payload["summary"]["devices_with_health_configured"] == 1
    assert payload["summary"]["devices_missing_health_config"] == 0
    assert payload["summary"]["devices_with_uptime_configured"] == 1
    assert payload["summary"]["devices_missing_uptime_config"] == 0
    assert payload["summary"]["system_health"] == 82.5
    assert payload["summary"]["average_efficiency"] == 94.0
    assert payload["devices"] == []
    assert payload["energy_widgets"]["today_energy_kwh"] == 14.5
    assert payload["energy_widgets"]["today_loss_kwh"] == 1.5
    assert payload["energy_widgets"]["month_energy_kwh"] == 70.0
    assert payload["energy_widgets"]["today_loss_cost_inr"] == 15.0


@pytest.mark.asyncio
async def test_live_dashboard_summary_returns_zeroed_metrics_for_empty_tenant_scope(
    monkeypatch: pytest.MonkeyPatch,
    session_factory,
):
    async with session_factory() as session:
        monkeypatch.setattr(LiveDashboardService, "_fetch_energy_json", AsyncMock(return_value=None))
        monkeypatch.setattr(TariffCache, "get", AsyncMock(return_value=None))

        payload = await LiveDashboardService(session).get_dashboard_summary(tenant_id="TENANT-EMPTY")

    assert payload["summary"]["total_devices"] == 0
    assert payload["summary"]["running_devices"] == 0
    assert payload["summary"]["stopped_devices"] == 0
    assert payload["summary"]["idle_devices"] == 0
    assert payload["summary"]["in_load_devices"] == 0
    assert payload["summary"]["overconsumption_devices"] == 0
    assert payload["summary"]["unknown_devices"] == 0
    assert payload["summary"]["devices_with_health_data"] == 0
    assert payload["summary"]["devices_with_health_configured"] == 0
    assert payload["summary"]["devices_missing_health_config"] == 0
    assert payload["summary"]["devices_with_uptime_configured"] == 0
    assert payload["summary"]["devices_missing_uptime_config"] == 0
    assert payload["summary"]["system_health"] is None
    assert payload["summary"]["average_efficiency"] is None
    assert payload["energy_widgets"]["today_energy_kwh"] == 0.0
    assert payload["energy_widgets"]["today_loss_kwh"] == 0.0
    assert payload["energy_widgets"]["month_energy_kwh"] == 0.0


@pytest.mark.asyncio
async def test_dashboard_summary_uses_live_loss_totals_even_when_energy_service_loss_lags(
    monkeypatch: pytest.MonkeyPatch,
    session_factory,
):
    local_day = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata")).date()
    async with session_factory() as session:
        session.add_all(
            [
                Device(
                    device_id="LOSS-DEVICE",
                    tenant_id="TENANT-A",
                    plant_id="PLANT-1",
                    device_name="Loss A",
                    device_type="compressor",
                    data_source_type="metered",
                ),
                DeviceLiveState(
                    device_id="LOSS-DEVICE",
                    tenant_id="TENANT-A",
                    runtime_status="running",
                    load_state="overconsumption",
                    health_score=80.0,
                    uptime_percentage=90.0,
                    day_bucket=local_day,
                    month_bucket=local_day.replace(day=1),
                    today_energy_kwh=12.0,
                    month_energy_kwh=80.0,
                    today_idle_kwh=0.4,
                    today_offhours_kwh=0.3,
                    today_overconsumption_kwh=0.8,
                    today_loss_kwh=1.5,
                    last_telemetry_ts=datetime.now(timezone.utc),
                ),
            ]
        )
        await session.commit()

        monkeypatch.setattr(
            LiveDashboardService,
            "_fetch_energy_json",
            AsyncMock(
                return_value={
                    "success": True,
                    "energy_widgets": {
                        "month_energy_kwh": 70.0,
                        "today_energy_kwh": 9.0,
                        "today_loss_kwh": 0.1,
                        "month_energy_cost_inr": 700.0,
                        "today_energy_cost_inr": 90.0,
                        "today_loss_cost_inr": 1.0,
                        "currency": "INR",
                    },
                }
            ),
        )
        monkeypatch.setattr(
            TariffCache,
            "get",
            AsyncMock(return_value={"configured": True, "rate": 10.0, "currency": "INR"}),
        )

        payload = await LiveDashboardService(session).get_dashboard_summary(tenant_id="TENANT-A")

    assert payload["energy_widgets"]["today_energy_kwh"] == 12.0
    assert payload["energy_widgets"]["today_energy_cost_inr"] == 120.0
    assert payload["energy_widgets"]["today_loss_kwh"] == 1.5
    assert payload["energy_widgets"]["today_loss_cost_inr"] == 15.0
    assert payload["energy_widgets"]["month_energy_kwh"] == 80.0
    assert payload["energy_widgets"]["month_energy_cost_inr"] == 800.0
    assert payload["summary"]["status_counts"]["overconsumption"] == 1
    assert payload["summary"]["overconsumption_devices"] == 1


@pytest.mark.asyncio
async def test_dashboard_summary_uses_live_month_energy_when_energy_service_month_lags(
    monkeypatch: pytest.MonkeyPatch,
    session_factory,
):
    local_day = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata")).date()
    month_bucket = local_day.replace(day=1)
    async with session_factory() as session:
        session.add_all(
            [
                Device(
                    device_id="MONTH-DEVICE",
                    tenant_id="TENANT-A",
                    plant_id="PLANT-1",
                    device_name="Month Device",
                    device_type="compressor",
                    data_source_type="metered",
                ),
                DeviceLiveState(
                    device_id="MONTH-DEVICE",
                    tenant_id="TENANT-A",
                    runtime_status="running",
                    day_bucket=local_day,
                    month_bucket=month_bucket,
                    today_energy_kwh=0.57,
                    month_energy_kwh=0.93,
                    today_idle_kwh=0.23,
                    today_offhours_kwh=0.40,
                    today_loss_kwh=0.63,
                ),
            ]
        )
        await session.commit()

        monkeypatch.setattr(
            LiveDashboardService,
            "_fetch_energy_json",
            AsyncMock(
                return_value={
                    "success": True,
                    "energy_widgets": {
                        "month_energy_kwh": 0.57,
                        "today_energy_kwh": 0.57,
                        "today_loss_kwh": 0.10,
                        "month_energy_cost_inr": 5.70,
                        "today_energy_cost_inr": 5.70,
                        "today_loss_cost_inr": 1.0,
                        "currency": "INR",
                    },
                }
            ),
        )
        monkeypatch.setattr(
            TariffCache,
            "get",
            AsyncMock(return_value={"configured": True, "rate": 10.0, "currency": "INR"}),
        )

        payload = await LiveDashboardService(session).get_dashboard_summary(tenant_id="TENANT-A")

    assert payload["energy_widgets"]["month_energy_kwh"] == 0.93
    assert payload["energy_widgets"]["month_energy_cost_inr"] == 9.3
    assert payload["energy_widgets"]["today_loss_kwh"] == 0.63
    assert payload["energy_widgets"]["month_energy_kwh"] >= payload["energy_widgets"]["today_loss_kwh"]


@pytest.mark.asyncio
async def test_dashboard_summary_prefers_canonical_month_total_when_unfiltered(
    monkeypatch: pytest.MonkeyPatch,
    session_factory,
):
    local_day = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata")).date()
    month_bucket = local_day.replace(day=1)
    async with session_factory() as session:
        session.add_all(
            [
                Device(
                    device_id="MONTH-CANONICAL",
                    tenant_id="TENANT-A",
                    plant_id="PLANT-1",
                    device_name="Month Canonical",
                    device_type="compressor",
                    data_source_type="metered",
                ),
                DeviceLiveState(
                    device_id="MONTH-CANONICAL",
                    tenant_id="TENANT-A",
                    runtime_status="running",
                    day_bucket=local_day,
                    month_bucket=month_bucket,
                    today_energy_kwh=5.31,
                    month_energy_kwh=9.33,
                    today_idle_kwh=0.25,
                    today_offhours_kwh=0.10,
                    today_loss_kwh=0.35,
                ),
            ]
        )
        await session.commit()

        async def fake_fetch(path, params=None):
            if path == "/api/v1/energy/calendar/monthly":
                return {
                    "success": True,
                    "summary": {
                        "total_energy_kwh": 9.77,
                        "total_energy_cost_inr": 67.41,
                    },
                }
            return None

        monkeypatch.setattr(
            LiveDashboardService,
            "_fetch_energy_json",
            AsyncMock(side_effect=fake_fetch),
        )
        monkeypatch.setattr(
            TariffCache,
            "get",
            AsyncMock(return_value={"configured": True, "rate": 6.9, "currency": "INR"}),
        )

        payload = await LiveDashboardService(session).get_dashboard_summary(tenant_id="TENANT-A")

    assert payload["energy_widgets"]["today_energy_kwh"] == 5.31
    assert payload["energy_widgets"]["month_energy_kwh"] == 9.77
    assert payload["energy_widgets"]["month_energy_cost_inr"] == 67.41


@pytest.mark.asyncio
async def test_dashboard_summary_today_loss_matches_breakdown_and_live_device_totals(
    monkeypatch: pytest.MonkeyPatch,
    session_factory,
):
    local_day = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata")).date()
    async with session_factory() as session:
        session.add_all(
            [
                Device(device_id="D1", tenant_id="TENANT-A", plant_id="PLANT-1", device_name="Device 1", device_type="compressor"),
                Device(device_id="D2", tenant_id="TENANT-A", plant_id="PLANT-1", device_name="Device 2", device_type="compressor"),
                DeviceLiveState(
                    device_id="D1",
                    tenant_id="TENANT-A",
                    runtime_status="running",
                    day_bucket=local_day,
                    today_energy_kwh=10.0,
                    today_idle_kwh=0.5,
                    today_offhours_kwh=0.0,
                    today_overconsumption_kwh=0.5,
                    today_loss_kwh=1.0,
                ),
                DeviceLiveState(
                    device_id="D2",
                    tenant_id="TENANT-A",
                    runtime_status="running",
                    day_bucket=local_day,
                    today_energy_kwh=5.0,
                    today_idle_kwh=0.1,
                    today_offhours_kwh=0.2,
                    today_overconsumption_kwh=0.0,
                    today_loss_kwh=0.3,
                ),
            ]
        )
        await session.commit()

        monkeypatch.setattr(LiveDashboardService, "_fetch_energy_json", AsyncMock(return_value=None))
        monkeypatch.setattr(
            TariffCache,
            "get",
            AsyncMock(return_value={"configured": True, "rate": 10.0, "currency": "INR"}),
        )

        service = LiveDashboardService(session)
        summary = await service.get_dashboard_summary(tenant_id="TENANT-A")
        breakdown = await service.get_today_loss_breakdown(tenant_id="TENANT-A")

    assert summary["energy_widgets"]["today_loss_kwh"] == 1.3
    assert summary["energy_widgets"]["today_loss_kwh"] == breakdown["totals"]["total_loss_kwh"]
    assert summary["energy_widgets"]["today_loss_cost_inr"] == breakdown["totals"]["total_loss_cost_inr"]
    assert breakdown["totals"]["total_loss_kwh"] == 1.3
    assert breakdown["totals"]["total_loss_cost_inr"] == 13.0
    assert sum(row["total_loss_kwh"] for row in breakdown["rows"]) == pytest.approx(1.3, abs=1e-4)


@pytest.mark.asyncio
async def test_today_loss_breakdown_is_tenant_scoped_and_uses_current_day_live_state(
    monkeypatch: pytest.MonkeyPatch,
    session_factory,
):
    local_day = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata")).date()
    async with session_factory() as session:
        session.add_all(
            [
                Device(device_id="SHARED-DEVICE-A", tenant_id="TENANT-A", plant_id="PLANT-1", device_name="A", device_type="compressor"),
                Device(device_id="SHARED-DEVICE-B", tenant_id="TENANT-B", plant_id="PLANT-1", device_name="B", device_type="compressor"),
                DeviceLiveState(
                    device_id="SHARED-DEVICE-A",
                    tenant_id="TENANT-A",
                    runtime_status="running",
                    day_bucket=local_day,
                    today_energy_kwh=3.0,
                    today_idle_kwh=0.2,
                    today_offhours_kwh=0.1,
                    today_overconsumption_kwh=0.3,
                    today_loss_kwh=0.6,
                ),
                DeviceLiveState(
                    device_id="SHARED-DEVICE-B",
                    tenant_id="TENANT-B",
                    runtime_status="running",
                    day_bucket=local_day,
                    today_energy_kwh=8.0,
                    today_idle_kwh=1.0,
                    today_offhours_kwh=1.0,
                    today_overconsumption_kwh=1.0,
                    today_loss_kwh=3.0,
                ),
            ]
        )
        await session.commit()

        monkeypatch.setattr(
            TariffCache,
            "get",
            AsyncMock(return_value={"configured": True, "rate": 5.0, "currency": "INR"}),
        )

        payload = await LiveDashboardService(session).get_today_loss_breakdown(tenant_id="TENANT-A")

    assert payload["totals"]["total_loss_kwh"] == 0.6
    assert payload["totals"]["total_loss_cost_inr"] == 3.0
    assert len(payload["rows"]) == 1
    assert payload["rows"][0]["device_id"] == "SHARED-DEVICE-A"
    assert payload["rows"][0]["total_loss_kwh"] == 0.6


@pytest.mark.asyncio
async def test_dashboard_summary_scopes_plant_roles_to_assigned_plants(
    monkeypatch: pytest.MonkeyPatch,
    session_factory,
):
    local_day = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata")).date()
    async with session_factory() as session:
        session.add_all(
            [
                Device(device_id="P1", tenant_id="TENANT-A", plant_id="PLANT-1", device_name="Plant 1", device_type="compressor"),
                Device(device_id="P2", tenant_id="TENANT-A", plant_id="PLANT-2", device_name="Plant 2", device_type="compressor"),
                DeviceLiveState(
                    device_id="P1",
                    tenant_id="TENANT-A",
                    runtime_status="running",
                    health_score=80.0,
                    uptime_percentage=90.0,
                    day_bucket=local_day,
                    today_energy_kwh=10.0,
                    today_loss_kwh=1.0,
                    month_energy_kwh=100.0,
                ),
                DeviceLiveState(
                    device_id="P2",
                    tenant_id="TENANT-A",
                    runtime_status="running",
                    health_score=40.0,
                    uptime_percentage=50.0,
                    day_bucket=local_day,
                    today_energy_kwh=5.0,
                    today_loss_kwh=0.5,
                    month_energy_kwh=50.0,
                ),
            ]
        )
        await session.commit()

        monkeypatch.setattr(LiveDashboardService, "_fetch_energy_json", AsyncMock(return_value=None))
        monkeypatch.setattr(
            TariffCache,
            "get",
            AsyncMock(return_value={"configured": True, "rate": 10.0, "currency": "INR"}),
        )

        service = LiveDashboardService(
            session,
            TenantContext(
                tenant_id="TENANT-A",
                user_id="operator-1",
                role="operator",
                plant_ids=["PLANT-1"],
                is_super_admin=False,
            ),
        )
        payload = await service.get_dashboard_summary(tenant_id="TENANT-A")
        breakdown = await service.get_today_loss_breakdown(tenant_id="TENANT-A")

    assert payload["summary"]["total_devices"] == 1
    assert payload["summary"]["system_health"] == 80.0
    assert payload["energy_widgets"]["today_energy_kwh"] == 10.0
    assert payload["energy_widgets"]["today_loss_kwh"] == 1.0
    assert payload["energy_widgets"]["month_energy_kwh"] == 100.0
    assert breakdown["totals"]["total_loss_kwh"] == 1.0
    assert [row["device_id"] for row in breakdown["rows"]] == ["P1"]


@pytest.mark.asyncio
async def test_dashboard_summary_scopes_to_selected_plant_within_assigned_scope(
    monkeypatch: pytest.MonkeyPatch,
    session_factory,
):
    local_day = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata")).date()
    async with session_factory() as session:
        session.add_all(
            [
                Device(device_id="P1", tenant_id="TENANT-A", plant_id="PLANT-1", device_name="Plant 1", device_type="compressor"),
                Device(device_id="P2", tenant_id="TENANT-A", plant_id="PLANT-2", device_name="Plant 2", device_type="compressor"),
                DeviceLiveState(
                    device_id="P1",
                    tenant_id="TENANT-A",
                    runtime_status="running",
                    health_score=80.0,
                    uptime_percentage=90.0,
                    day_bucket=local_day,
                    today_energy_kwh=10.0,
                    today_loss_kwh=1.0,
                    month_energy_kwh=100.0,
                ),
                DeviceLiveState(
                    device_id="P2",
                    tenant_id="TENANT-A",
                    runtime_status="running",
                    health_score=60.0,
                    uptime_percentage=70.0,
                    day_bucket=local_day,
                    today_energy_kwh=6.0,
                    today_loss_kwh=0.6,
                    month_energy_kwh=60.0,
                ),
            ]
        )
        await session.commit()

        monkeypatch.setattr(LiveDashboardService, "_fetch_energy_json", AsyncMock(return_value=None))
        monkeypatch.setattr(
            TariffCache,
            "get",
            AsyncMock(return_value={"configured": True, "rate": 10.0, "currency": "INR"}),
        )

        service = LiveDashboardService(
            session,
            TenantContext(
                tenant_id="TENANT-A",
                user_id="plant-manager-1",
                role="plant_manager",
                plant_ids=["PLANT-1", "PLANT-2", "PLANT-3"],
                is_super_admin=False,
            ),
        )
        payload = await service.get_dashboard_summary(tenant_id="TENANT-A", plant_id="PLANT-2")
        breakdown = await service.get_today_loss_breakdown(tenant_id="TENANT-A", plant_id="PLANT-2")

    assert payload["summary"]["total_devices"] == 1
    assert payload["energy_widgets"]["today_energy_kwh"] == 6.0
    assert payload["energy_widgets"]["today_loss_kwh"] == 0.6
    assert payload["energy_widgets"]["month_energy_kwh"] == 60.0
    assert breakdown["totals"]["total_loss_kwh"] == 0.6
    assert [row["device_id"] for row in breakdown["rows"]] == ["P2"]


@pytest.mark.asyncio
async def test_dashboard_summary_plant_scope_calls_calendar_and_uses_canonical_month_totals(
    monkeypatch: pytest.MonkeyPatch,
    session_factory,
):
    local_day = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata")).date()
    async with session_factory() as session:
        session.add_all(
            [
                Device(device_id="PLANT-SCOPE-1", tenant_id="TENANT-A", plant_id="PLANT-2", device_name="Plant Scope", device_type="compressor"),
                DeviceLiveState(
                    device_id="PLANT-SCOPE-1",
                    tenant_id="TENANT-A",
                    runtime_status="running",
                    health_score=77.0,
                    uptime_percentage=88.0,
                    day_bucket=local_day,
                    today_energy_kwh=4.2,
                    today_loss_kwh=0.4,
                    month_energy_kwh=42.5,
                ),
            ]
        )
        await session.commit()

        async def fake_fetch_energy_json(self, path, params=None):
            if path == "/api/v1/energy/calendar/monthly":
                return {
                    "success": True,
                    "summary": {
                        "total_energy_kwh": 999.0,
                        "total_energy_cost_inr": 9990.0,
                    },
                    "days": [],
                }
            return None

        monkeypatch.setattr(LiveDashboardService, "_fetch_energy_json", fake_fetch_energy_json)
        monkeypatch.setattr(
            TariffCache,
            "get",
            AsyncMock(return_value={"configured": True, "rate": 10.0, "currency": "INR"}),
        )

        payload = await LiveDashboardService(session).get_dashboard_summary(
            tenant_id="TENANT-A",
            plant_id="PLANT-2",
        )

    assert payload["summary"]["total_devices"] == 1
    assert payload["energy_widgets"]["today_energy_kwh"] == 4.2
    assert payload["energy_widgets"]["month_energy_kwh"] == 999.0
    assert payload["energy_widgets"]["month_energy_cost_inr"] == 9990.0


@pytest.mark.asyncio
async def test_dashboard_summary_and_breakdown_use_explicit_accessible_plant_scope(
    monkeypatch: pytest.MonkeyPatch,
    session_factory,
):
    local_day = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata")).date()
    async with session_factory() as session:
        session.add_all(
            [
                Device(device_id="P1", tenant_id="TENANT-A", plant_id="PLANT-1", device_name="Plant 1", device_type="compressor"),
                Device(device_id="P2", tenant_id="TENANT-A", plant_id="PLANT-2", device_name="Plant 2", device_type="compressor"),
                DeviceLiveState(
                    device_id="P1",
                    tenant_id="TENANT-A",
                    runtime_status="running",
                    health_score=80.0,
                    uptime_percentage=90.0,
                    day_bucket=local_day,
                    today_energy_kwh=10.0,
                    today_idle_kwh=0.3,
                    today_offhours_kwh=0.2,
                    today_overconsumption_kwh=0.1,
                    today_loss_kwh=0.6,
                    month_energy_kwh=100.0,
                ),
                DeviceLiveState(
                    device_id="P2",
                    tenant_id="TENANT-A",
                    runtime_status="running",
                    health_score=60.0,
                    uptime_percentage=70.0,
                    day_bucket=local_day,
                    today_energy_kwh=6.0,
                    today_idle_kwh=0.0,
                    today_offhours_kwh=0.4,
                    today_overconsumption_kwh=0.2,
                    today_loss_kwh=0.6,
                    month_energy_kwh=60.0,
                ),
            ]
        )
        await session.commit()

        monkeypatch.setattr(LiveDashboardService, "_fetch_energy_json", AsyncMock(return_value=None))
        monkeypatch.setattr(
            TariffCache,
            "get",
            AsyncMock(return_value={"configured": True, "rate": 10.0, "currency": "INR"}),
        )

        service = LiveDashboardService(
            session,
            TenantContext(
                tenant_id="TENANT-A",
                user_id="org-admin-1",
                role="org_admin",
                plant_ids=[],
                is_super_admin=False,
            ),
        )
        summary = await service.get_dashboard_summary(
            tenant_id="TENANT-A",
            accessible_plant_ids=["PLANT-1"],
        )
        breakdown = await service.get_today_loss_breakdown(
            tenant_id="TENANT-A",
            accessible_plant_ids=["PLANT-1"],
        )

    assert summary["summary"]["total_devices"] == 1
    assert summary["energy_widgets"]["today_energy_kwh"] == 10.0
    assert summary["energy_widgets"]["today_loss_kwh"] == 0.6
    assert summary["energy_widgets"]["month_energy_kwh"] == 100.0
    assert breakdown["totals"]["total_loss_kwh"] == 0.6
    assert [row["device_id"] for row in breakdown["rows"]] == ["P1"]


@pytest.mark.asyncio
async def test_fleet_snapshot_uses_explicit_accessible_plant_scope(
    session_factory,
):
    async with session_factory() as session:
        session.add_all(
            [
                Device(device_id="P1", tenant_id="TENANT-A", plant_id="PLANT-1", device_name="Plant 1", device_type="compressor"),
                Device(device_id="P2", tenant_id="TENANT-A", plant_id="PLANT-2", device_name="Plant 2", device_type="compressor"),
                Device(device_id="P3", tenant_id="TENANT-A", plant_id="PLANT-3", device_name="Plant 3", device_type="compressor"),
            ]
        )
        await session.commit()

        payload = await LiveDashboardService(session).get_fleet_snapshot(
            tenant_id="TENANT-A",
            accessible_plant_ids=["PLANT-1", "PLANT-2"],
        )

    assert payload["total"] == 2
    assert [device["device_id"] for device in payload["devices"]] == ["P1", "P2"]


@pytest.mark.asyncio
async def test_fleet_snapshot_search_supports_partial_case_insensitive_and_scoped_filters(
    session_factory,
):
    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        session.add_all(
            [
                Device(
                    device_id="PRESS-1",
                    tenant_id="TENANT-A",
                    plant_id="PLANT-1",
                    device_name="Press Alpha",
                    device_type="press",
                    first_telemetry_timestamp=now,
                    last_seen_timestamp=now,
                ),
                Device(
                    device_id="PRESS-2",
                    tenant_id="TENANT-A",
                    plant_id="PLANT-2",
                    device_name="Press Bravo",
                    device_type="press",
                    first_telemetry_timestamp=now,
                    last_seen_timestamp=now,
                ),
                Device(
                    device_id="LATHE-1",
                    tenant_id="TENANT-A",
                    plant_id="PLANT-1",
                    device_name="Lathe One",
                    device_type="lathe",
                    first_telemetry_timestamp=now,
                    last_seen_timestamp=now,
                ),
                Device(
                    device_id="PRESS-FOREIGN",
                    tenant_id="TENANT-B",
                    plant_id="PLANT-9",
                    device_name="Press Hidden",
                    device_type="press",
                    first_telemetry_timestamp=now,
                    last_seen_timestamp=now,
                ),
                DeviceLiveState(
                    device_id="PRESS-1",
                    tenant_id="TENANT-A",
                    runtime_status="running",
                    load_state="running",
                    last_telemetry_ts=now,
                    last_sample_ts=now,
                    version=2,
                ),
                DeviceLiveState(
                    device_id="PRESS-2",
                    tenant_id="TENANT-A",
                    runtime_status="running",
                    load_state="overconsumption",
                    last_telemetry_ts=now,
                    last_sample_ts=now,
                    version=3,
                ),
            ]
        )
        await session.commit()

        service = LiveDashboardService(session)

        partial_match = await service.get_fleet_snapshot(
            tenant_id="TENANT-A",
            search="press",
        )
        case_insensitive = await service.get_fleet_snapshot(
            tenant_id="TENANT-A",
            search="ALPHA",
        )
        plant_filtered = await service.get_fleet_snapshot(
            tenant_id="TENANT-A",
            search="press",
            accessible_plant_ids=["PLANT-1"],
        )
        operational_filtered = await service.get_fleet_snapshot(
            tenant_id="TENANT-A",
            search="press",
            operational_status_filter="overconsumption",
        )
        no_match = await service.get_fleet_snapshot(
            tenant_id="TENANT-A",
            search="nonexistent",
        )

    assert [device["device_id"] for device in partial_match["devices"]] == ["PRESS-1", "PRESS-2"]
    assert partial_match["total"] == 2

    assert [device["device_id"] for device in case_insensitive["devices"]] == ["PRESS-1"]
    assert case_insensitive["total"] == 1

    assert [device["device_id"] for device in plant_filtered["devices"]] == ["PRESS-1"]
    assert plant_filtered["total"] == 1

    assert [device["device_id"] for device in operational_filtered["devices"]] == ["PRESS-2"]
    assert operational_filtered["devices"][0]["operational_status"] == "overconsumption"
    assert operational_filtered["total"] == 1

    assert no_match["devices"] == []
    assert no_match["total"] == 0
    assert no_match["page"] == 1
    assert no_match["total_pages"] == 1


@pytest.mark.asyncio
async def test_dashboard_summary_totals_match_sum_of_plant_filtered_views(
    monkeypatch: pytest.MonkeyPatch,
    session_factory,
):
    local_day = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata")).date()
    async with session_factory() as session:
        session.add_all(
            [
                Device(
                    device_id="PLANT-1-DEVICE",
                    tenant_id="TENANT-A",
                    plant_id="PLANT-1",
                    device_name="Plant 1 Device",
                    device_type="compressor",
                    data_source_type="metered",
                ),
                Device(
                    device_id="PLANT-2-DEVICE",
                    tenant_id="TENANT-A",
                    plant_id="PLANT-2",
                    device_name="Plant 2 Device",
                    device_type="compressor",
                    data_source_type="metered",
                ),
                DeviceLiveState(
                    device_id="PLANT-1-DEVICE",
                    tenant_id="TENANT-A",
                    runtime_status="running",
                    day_bucket=local_day,
                ),
                DeviceLiveState(
                    device_id="PLANT-2-DEVICE",
                    tenant_id="TENANT-A",
                    runtime_status="stopped",
                    day_bucket=local_day,
                ),
            ]
        )
        await session.commit()

        monkeypatch.setattr(LiveDashboardService, "_fetch_energy_json", AsyncMock(return_value=None))
        monkeypatch.setattr(TariffCache, "get", AsyncMock(return_value=None))

        service = LiveDashboardService(session)
        all_plants = await service.get_dashboard_summary(tenant_id="TENANT-A")
        plant_one = await service.get_dashboard_summary(tenant_id="TENANT-A", plant_id="PLANT-1")
        plant_two = await service.get_dashboard_summary(tenant_id="TENANT-A", plant_id="PLANT-2")

    assert all_plants["summary"]["total_devices"] == 2
    assert plant_one["summary"]["total_devices"] == 1
    assert plant_two["summary"]["total_devices"] == 1
    assert all_plants["summary"]["total_devices"] == (
        plant_one["summary"]["total_devices"] + plant_two["summary"]["total_devices"]
    )


@pytest.mark.asyncio
async def test_dashboard_summary_does_not_count_unknown_devices_as_stopped(
    monkeypatch: pytest.MonkeyPatch,
    session_factory,
):
    stale_day = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata")).date() - timedelta(days=2)
    async with session_factory() as session:
        session.add_all(
            [
                Device(
                    device_id="RUN-DEVICE",
                    tenant_id="TENANT-A",
                    plant_id="PLANT-1",
                    device_name="Running Device",
                    device_type="compressor",
                    data_source_type="metered",
                ),
                Device(
                    device_id="UNK-DEVICE",
                    tenant_id="TENANT-A",
                    plant_id="PLANT-1",
                    device_name="Unknown Device",
                    device_type="compressor",
                    data_source_type="metered",
                ),
                DeviceLiveState(
                    device_id="RUN-DEVICE",
                    tenant_id="TENANT-A",
                    runtime_status="running",
                    load_state="running",
                    day_bucket=stale_day,
                    month_bucket=stale_day.replace(day=1),
                    last_telemetry_ts=datetime.now(timezone.utc),
                    last_sample_ts=datetime.now(timezone.utc),
                ),
                DeviceLiveState(
                    device_id="UNK-DEVICE",
                    tenant_id="TENANT-A",
                    runtime_status="running",
                    load_state="unloaded",
                    day_bucket=stale_day,
                    month_bucket=stale_day.replace(day=1),
                    last_telemetry_ts=datetime.now(timezone.utc),
                    last_sample_ts=datetime.now(timezone.utc),
                ),
            ]
        )
        await session.commit()

        monkeypatch.setattr(LiveDashboardService, "_fetch_energy_json", AsyncMock(return_value=None))
        monkeypatch.setattr(TariffCache, "get", AsyncMock(return_value=None))

        payload = await LiveDashboardService(session).get_dashboard_summary(tenant_id="TENANT-A")

    assert payload["summary"]["total_devices"] == 2
    assert payload["summary"]["running_devices"] == 2
    assert payload["summary"]["stopped_devices"] == 0
    assert payload["summary"]["unknown_devices"] == 1
    assert payload["summary"]["status_counts"]["stopped"] == 0
    assert payload["summary"]["status_counts"]["unknown"] == 1


@pytest.mark.asyncio
async def test_dashboard_summary_today_cost_uses_monthly_calendar_when_available(
    monkeypatch: pytest.MonkeyPatch,
    session_factory,
):
    local_day = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata")).date()
    month_bucket = local_day.replace(day=1)
    async with session_factory() as session:
        session.add_all(
            [
                Device(
                    device_id="CAL-DEVICE",
                    tenant_id="TENANT-A",
                    plant_id="PLANT-1",
                    device_name="Cal Device",
                    device_type="compressor",
                ),
                DeviceLiveState(
                    device_id="CAL-DEVICE",
                    tenant_id="TENANT-A",
                    runtime_status="running",
                    day_bucket=local_day,
                    month_bucket=month_bucket,
                    today_energy_kwh=5.0,
                    month_energy_kwh=10.0,
                    today_idle_kwh=0.0,
                    today_offhours_kwh=0.0,
                    today_overconsumption_kwh=0.0,
                    today_loss_kwh=0.0,
                    today_loss_cost_inr=0.0,
                    last_telemetry_ts=datetime.now(timezone.utc),
                ),
            ]
        )
        await session.commit()

        days_list = []
        cur = month_bucket
        while cur <= local_day:
            days_list.append(
                {
                    "date": cur.isoformat(),
                    "energy_kwh": 5.0 if cur == local_day else 1.0,
                    "energy_cost_inr": 47.5 if cur == local_day else 10.0,
                }
            )
            cur = cur + timedelta(days=1)

        monkeypatch.setattr(
            LiveDashboardService,
            "_fetch_energy_json",
            AsyncMock(
                return_value={
                    "success": True,
                    "summary": {
                        "total_energy_kwh": 50.0,
                        "total_energy_cost_inr": 475.0,
                    },
                    "days": days_list,
                }
            ),
        )
        monkeypatch.setattr(
            TariffCache,
            "get",
            AsyncMock(return_value={"configured": True, "rate": 10.0, "currency": "INR"}),
        )

        payload = await LiveDashboardService(session).get_dashboard_summary(tenant_id="TENANT-A")

    assert payload["energy_widgets"]["today_energy_cost_inr"] == 47.5
    assert payload["energy_widgets"]["today_energy_cost_inr"] != 5.0 * 10.0


@pytest.mark.asyncio
async def test_fleet_loss_view_uses_persisted_loss_cost_and_proportional_allocation(
    monkeypatch: pytest.MonkeyPatch,
    session_factory,
):
    local_day = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata")).date()
    async with session_factory() as session:
        session.add_all(
            [
                Device(
                    device_id="LOSS-DEV-1",
                    tenant_id="TENANT-A",
                    plant_id="PLANT-1",
                    device_name="Loss Device 1",
                    device_type="compressor",
                ),
                DeviceLiveState(
                    device_id="LOSS-DEV-1",
                    tenant_id="TENANT-A",
                    runtime_status="running",
                    day_bucket=local_day,
                    today_energy_kwh=10.0,
                    today_idle_kwh=0.6,
                    today_offhours_kwh=0.3,
                    today_overconsumption_kwh=0.1,
                    today_loss_kwh=1.0,
                    today_loss_cost_inr=7.0,
                    last_telemetry_ts=datetime.now(timezone.utc),
                ),
            ]
        )
        await session.commit()

        monkeypatch.setattr(TariffCache, "get", AsyncMock(return_value={"configured": True, "rate": 10.0, "currency": "INR"}))

        service = LiveDashboardService(session)
        breakdown = await service.get_today_loss_breakdown(tenant_id="TENANT-A")

    row = breakdown["rows"][0]
    assert row["total_loss_cost_inr"] == 7.0
    assert row["total_loss_cost_inr"] != 1.0 * 10.0
    bucket_sum = row["idle_cost_inr"] + row["off_hours_cost_inr"] + row["overconsumption_cost_inr"]
    assert abs(bucket_sum - row["total_loss_cost_inr"]) < 0.001

    totals = breakdown["totals"]
    assert totals["total_loss_cost_inr"] == 7.0
    totals_bucket_sum = totals["idle_cost_inr"] + totals["off_hours_cost_inr"] + totals["overconsumption_cost_inr"]
    assert abs(totals_bucket_sum - totals["total_loss_cost_inr"]) < 0.001


@pytest.mark.asyncio
async def test_bootstrap_summary_prefers_canonical_today_energy_over_live_state(
    monkeypatch: pytest.MonkeyPatch,
    session_factory,
):
    local_day = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata")).date()
    async with session_factory() as session:
        session.add_all(
            [
                Device(
                    device_id="CANONICAL-DEV-1",
                    tenant_id="TENANT-A",
                    plant_id="PLANT-1",
                    device_name="Canonical Test",
                    device_type="compressor",
                    data_source_type="metered",
                ),
                DeviceLiveState(
                    device_id="CANONICAL-DEV-1",
                    tenant_id="TENANT-A",
                    runtime_status="running",
                    load_state="running",
                    health_score=90.0,
                    uptime_percentage=95.0,
                    day_bucket=local_day,
                    today_energy_kwh=170.0,
                    today_loss_kwh=5.0,
                    month_energy_kwh=500.0,
                    last_telemetry_ts=datetime.now(timezone.utc),
                ),
            ]
        )
        await session.commit()

        async def fake_fetch_energy_json(self, path, params=None):
            if "/api/v1/energy/device/CANONICAL-DEV-1/range" in path:
                return {
                    "success": True,
                    "days": [
                        {
                            "date": local_day.isoformat(),
                            "energy_kwh": 192.0,
                            "energy_cost_inr": 1920.0,
                            "loss_kwh": 20.0,
                            "loss_cost_inr": 200.0,
                        }
                    ],
                    "totals": {"energy_kwh": 192.0, "loss_kwh": 20.0},
                }
            return None

        monkeypatch.setattr(LiveDashboardService, "_fetch_energy_json", fake_fetch_energy_json)
        monkeypatch.setattr(TariffCache, "get", AsyncMock(return_value={"configured": True, "rate": 10.0, "currency": "INR"}))

        payload = await LiveDashboardService(session).get_dashboard_bootstrap_summary(
            device_id="CANONICAL-DEV-1",
            tenant_id="TENANT-A",
        )

    assert payload["loss_overview"]["today_energy_kwh"] == 192.0
    assert payload["loss_overview"]["today_energy_kwh"] != 170.0
    if payload["loss_overview"].get("co2_overview") and payload["loss_overview"]["co2_overview"].get("today"):
        assert payload["loss_overview"]["co2_overview"]["today"]["co2_kg"] > 0


@pytest.mark.asyncio
async def test_bootstrap_summary_falls_back_to_live_state_when_energy_service_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    session_factory,
):
    local_day = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata")).date()
    async with session_factory() as session:
        session.add_all(
            [
                Device(
                    device_id="FALLBACK-DEV-1",
                    tenant_id="TENANT-A",
                    plant_id="PLANT-1",
                    device_name="Fallback Test",
                    device_type="compressor",
                    data_source_type="metered",
                ),
                DeviceLiveState(
                    device_id="FALLBACK-DEV-1",
                    tenant_id="TENANT-A",
                    runtime_status="running",
                    load_state="running",
                    health_score=85.0,
                    uptime_percentage=90.0,
                    day_bucket=local_day,
                    today_energy_kwh=187.0,
                    today_loss_kwh=7.0,
                    month_energy_kwh=450.0,
                    last_telemetry_ts=datetime.now(timezone.utc),
                ),
            ]
        )
        await session.commit()

        monkeypatch.setattr(LiveDashboardService, "_fetch_energy_json", AsyncMock(return_value=None))
        monkeypatch.setattr(TariffCache, "get", AsyncMock(return_value={"configured": True, "rate": 10.0, "currency": "INR"}))

        payload = await LiveDashboardService(session).get_dashboard_bootstrap_summary(
            device_id="FALLBACK-DEV-1",
            tenant_id="TENANT-A",
        )

    assert payload["loss_overview"]["today_energy_kwh"] == 187.0


@pytest.mark.asyncio
async def test_bootstrap_summary_prefers_canonical_today_loss_over_live_state(
    monkeypatch: pytest.MonkeyPatch,
    session_factory,
):
    local_day = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata")).date()
    async with session_factory() as session:
        session.add_all(
            [
                Device(
                    device_id="CANONICAL-LOSS-DEV-1",
                    tenant_id="TENANT-A",
                    plant_id="PLANT-1",
                    device_name="Canonical Loss Test",
                    device_type="compressor",
                    data_source_type="metered",
                ),
                DeviceLiveState(
                    device_id="CANONICAL-LOSS-DEV-1",
                    tenant_id="TENANT-A",
                    runtime_status="running",
                    load_state="running",
                    health_score=90.0,
                    uptime_percentage=95.0,
                    day_bucket=local_day,
                    today_energy_kwh=170.0,
                    today_loss_kwh=5.0,
                    today_loss_cost_inr=50.0,
                    today_idle_kwh=2.0,
                    today_offhours_kwh=2.0,
                    today_overconsumption_kwh=1.0,
                    month_energy_kwh=500.0,
                    last_telemetry_ts=datetime.now(timezone.utc),
                ),
            ]
        )
        await session.commit()

        async def fake_fetch_energy_json(self, path, params=None):
            if "/api/v1/energy/device/CANONICAL-LOSS-DEV-1/range" in path:
                return {
                    "success": True,
                    "days": [
                        {
                            "date": local_day.isoformat(),
                            "energy_kwh": 192.0,
                            "energy_cost_inr": 1920.0,
                            "loss_kwh": 20.0,
                            "loss_cost_inr": 200.0,
                            "idle_kwh": 8.0,
                            "offhours_kwh": 7.0,
                            "overconsumption_kwh": 5.0,
                        }
                    ],
                    "totals": {"energy_kwh": 192.0, "loss_kwh": 20.0},
                }
            return None

        monkeypatch.setattr(LiveDashboardService, "_fetch_energy_json", fake_fetch_energy_json)
        monkeypatch.setattr(TariffCache, "get", AsyncMock(return_value={"configured": True, "rate": 10.0, "currency": "INR"}))

        payload = await LiveDashboardService(session).get_dashboard_bootstrap_summary(
            device_id="CANONICAL-LOSS-DEV-1",
            tenant_id="TENANT-A",
        )

    assert payload["loss_overview"]["total_loss_kwh"] == 20.0
    assert payload["loss_overview"]["total_loss_cost_inr"] == 200.0
    assert payload["loss_overview"]["idle_kwh"] == 8.0
    assert payload["loss_overview"]["off_hours_kwh"] == 7.0
    assert payload["loss_overview"]["overconsumption_kwh"] == 5.0


@pytest.mark.asyncio
async def test_bootstrap_summary_falls_back_to_live_loss_when_energy_service_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    session_factory,
):
    local_day = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata")).date()
    async with session_factory() as session:
        session.add_all(
            [
                Device(
                    device_id="FALLBACK-LOSS-DEV-1",
                    tenant_id="TENANT-A",
                    plant_id="PLANT-1",
                    device_name="Fallback Loss Test",
                    device_type="compressor",
                    data_source_type="metered",
                ),
                DeviceLiveState(
                    device_id="FALLBACK-LOSS-DEV-1",
                    tenant_id="TENANT-A",
                    runtime_status="running",
                    load_state="running",
                    health_score=85.0,
                    uptime_percentage=90.0,
                    day_bucket=local_day,
                    today_energy_kwh=187.0,
                    today_loss_kwh=7.0,
                    today_loss_cost_inr=70.0,
                    today_idle_kwh=3.0,
                    today_offhours_kwh=3.0,
                    today_overconsumption_kwh=1.0,
                    month_energy_kwh=450.0,
                    last_telemetry_ts=datetime.now(timezone.utc),
                ),
            ]
        )
        await session.commit()

        monkeypatch.setattr(LiveDashboardService, "_fetch_energy_json", AsyncMock(return_value=None))
        monkeypatch.setattr(TariffCache, "get", AsyncMock(return_value={"configured": True, "rate": 10.0, "currency": "INR"}))

        payload = await LiveDashboardService(session).get_dashboard_bootstrap_summary(
            device_id="FALLBACK-LOSS-DEV-1",
            tenant_id="TENANT-A",
        )

    assert payload["loss_overview"]["total_loss_kwh"] == 7.0
    assert payload["loss_overview"]["idle_kwh"] == 3.0
    assert payload["loss_overview"]["off_hours_kwh"] == 3.0
    assert payload["loss_overview"]["overconsumption_kwh"] == 1.0


@pytest.mark.asyncio
async def test_bootstrap_summary_uses_canonical_zero_loss_over_stale_live(
    monkeypatch: pytest.MonkeyPatch,
    session_factory,
):
    local_day = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata")).date()
    async with session_factory() as session:
        session.add_all(
            [
                Device(
                    device_id="ZERO-CANONICAL-DEV",
                    tenant_id="TENANT-A",
                    plant_id="PLANT-1",
                    device_name="Zero Canonical Test",
                    device_type="compressor",
                    data_source_type="metered",
                ),
                DeviceLiveState(
                    device_id="ZERO-CANONICAL-DEV",
                    tenant_id="TENANT-A",
                    runtime_status="running",
                    load_state="running",
                    health_score=90.0,
                    uptime_percentage=95.0,
                    day_bucket=local_day,
                    today_energy_kwh=150.0,
                    today_loss_kwh=5.0,
                    today_loss_cost_inr=50.0,
                    today_idle_kwh=2.0,
                    today_offhours_kwh=2.0,
                    today_overconsumption_kwh=1.0,
                    month_energy_kwh=500.0,
                    last_telemetry_ts=datetime.now(timezone.utc),
                ),
            ]
        )
        await session.commit()

        async def fake_fetch_energy_json(self, path, params=None):
            if "/api/v1/energy/device/ZERO-CANONICAL-DEV/range" in path:
                return {
                    "success": True,
                    "days": [
                        {
                            "date": local_day.isoformat(),
                            "energy_kwh": 150.0,
                            "energy_cost_inr": 1500.0,
                            "loss_kwh": 0.0,
                            "loss_cost_inr": 0.0,
                            "idle_kwh": 0.0,
                            "offhours_kwh": 0.0,
                            "overconsumption_kwh": 0.0,
                        }
                    ],
                    "totals": {"energy_kwh": 150.0, "loss_kwh": 0.0},
                }
            return None

        monkeypatch.setattr(LiveDashboardService, "_fetch_energy_json", fake_fetch_energy_json)
        monkeypatch.setattr(TariffCache, "get", AsyncMock(return_value={"configured": True, "rate": 10.0, "currency": "INR"}))

        payload = await LiveDashboardService(session).get_dashboard_bootstrap_summary(
            device_id="ZERO-CANONICAL-DEV",
            tenant_id="TENANT-A",
        )

    assert payload["loss_overview"]["total_loss_kwh"] == 0.0
    assert payload["loss_overview"]["total_loss_cost_inr"] == 0.0
    assert payload["loss_overview"]["idle_kwh"] == 0.0
    assert payload["loss_overview"]["off_hours_kwh"] == 0.0
    assert payload["loss_overview"]["overconsumption_kwh"] == 0.0


@pytest.mark.asyncio
async def test_bootstrap_summary_uses_canonical_mixed_zero_nonzero_categories(
    monkeypatch: pytest.MonkeyPatch,
    session_factory,
):
    local_day = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata")).date()
    async with session_factory() as session:
        session.add_all(
            [
                Device(
                    device_id="MIXED-CANONICAL-DEV",
                    tenant_id="TENANT-A",
                    plant_id="PLANT-1",
                    device_name="Mixed Canonical Test",
                    device_type="compressor",
                    data_source_type="metered",
                ),
                DeviceLiveState(
                    device_id="MIXED-CANONICAL-DEV",
                    tenant_id="TENANT-A",
                    runtime_status="running",
                    load_state="running",
                    health_score=90.0,
                    uptime_percentage=95.0,
                    day_bucket=local_day,
                    today_energy_kwh=200.0,
                    today_loss_kwh=15.0,
                    today_loss_cost_inr=150.0,
                    today_idle_kwh=5.0,
                    today_offhours_kwh=8.0,
                    today_overconsumption_kwh=2.0,
                    month_energy_kwh=600.0,
                    last_telemetry_ts=datetime.now(timezone.utc),
                ),
            ]
        )
        await session.commit()

        async def fake_fetch_energy_json(self, path, params=None):
            if "/api/v1/energy/device/MIXED-CANONICAL-DEV/range" in path:
                return {
                    "success": True,
                    "days": [
                        {
                            "date": local_day.isoformat(),
                            "energy_kwh": 200.0,
                            "energy_cost_inr": 2000.0,
                            "loss_kwh": 10.0,
                            "loss_cost_inr": 100.0,
                            "idle_kwh": 0.0,
                            "offhours_kwh": 7.0,
                            "overconsumption_kwh": 3.0,
                        }
                    ],
                    "totals": {"energy_kwh": 200.0, "loss_kwh": 10.0},
                }
            return None

        monkeypatch.setattr(LiveDashboardService, "_fetch_energy_json", fake_fetch_energy_json)
        monkeypatch.setattr(TariffCache, "get", AsyncMock(return_value={"configured": True, "rate": 10.0, "currency": "INR"}))

        payload = await LiveDashboardService(session).get_dashboard_bootstrap_summary(
            device_id="MIXED-CANONICAL-DEV",
            tenant_id="TENANT-A",
        )

    assert payload["loss_overview"]["total_loss_kwh"] == 10.0
    assert payload["loss_overview"]["total_loss_cost_inr"] == 100.0
    assert payload["loss_overview"]["idle_kwh"] == 0.0
    assert payload["loss_overview"]["off_hours_kwh"] == 7.0
    assert payload["loss_overview"]["overconsumption_kwh"] == 3.0


@pytest.mark.asyncio
async def test_bootstrap_summary_falls_back_to_live_only_when_canonical_missing(
    monkeypatch: pytest.MonkeyPatch,
    session_factory,
):
    local_day = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata")).date()
    async with session_factory() as session:
        session.add_all(
            [
                Device(
                    device_id="MISSING-CANONICAL-DEV",
                    tenant_id="TENANT-A",
                    plant_id="PLANT-1",
                    device_name="Missing Canonical Test",
                    device_type="compressor",
                    data_source_type="metered",
                ),
                DeviceLiveState(
                    device_id="MISSING-CANONICAL-DEV",
                    tenant_id="TENANT-A",
                    runtime_status="running",
                    load_state="running",
                    health_score=85.0,
                    uptime_percentage=90.0,
                    day_bucket=local_day,
                    today_energy_kwh=187.0,
                    today_loss_kwh=9.0,
                    today_loss_cost_inr=90.0,
                    today_idle_kwh=4.0,
                    today_offhours_kwh=3.0,
                    today_overconsumption_kwh=2.0,
                    month_energy_kwh=450.0,
                    last_telemetry_ts=datetime.now(timezone.utc),
                ),
            ]
        )
        await session.commit()

        async def fake_fetch_energy_json(self, path, params=None):
            if "/api/v1/energy/device/MISSING-CANONICAL-DEV/range" in path:
                return {
                    "success": True,
                    "days": [
                        {
                            "date": local_day.isoformat(),
                            "energy_kwh": 187.0,
                            "energy_cost_inr": 1870.0,
                        }
                    ],
                    "totals": {"energy_kwh": 187.0},
                }
            return None

        monkeypatch.setattr(LiveDashboardService, "_fetch_energy_json", fake_fetch_energy_json)
        monkeypatch.setattr(TariffCache, "get", AsyncMock(return_value={"configured": True, "rate": 10.0, "currency": "INR"}))

        payload = await LiveDashboardService(session).get_dashboard_bootstrap_summary(
            device_id="MISSING-CANONICAL-DEV",
            tenant_id="TENANT-A",
        )

    assert payload["loss_overview"]["total_loss_kwh"] == 9.0
    assert payload["loss_overview"]["idle_kwh"] == 4.0
    assert payload["loss_overview"]["off_hours_kwh"] == 3.0
    assert payload["loss_overview"]["overconsumption_kwh"] == 2.0


@pytest.mark.asyncio
async def test_fleet_dashboard_canonical_loss_override_from_calendar(
    monkeypatch: pytest.MonkeyPatch,
    session_factory,
):
    local_day = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata")).date()
    month_bucket = local_day.replace(day=1)
    async with session_factory() as session:
        session.add_all(
            [
                Device(
                    device_id="FLEET-CANONICAL-DEV",
                    tenant_id="TENANT-A",
                    plant_id="PLANT-1",
                    device_name="Fleet Canonical",
                    device_type="compressor",
                ),
                DeviceLiveState(
                    device_id="FLEET-CANONICAL-DEV",
                    tenant_id="TENANT-A",
                    runtime_status="running",
                    day_bucket=local_day,
                    month_bucket=month_bucket,
                    today_energy_kwh=10.0,
                    today_loss_kwh=1.0,
                    today_loss_cost_inr=10.0,
                    today_idle_kwh=0.4,
                    today_offhours_kwh=0.3,
                    today_overconsumption_kwh=0.3,
                    month_energy_kwh=50.0,
                    last_telemetry_ts=datetime.now(timezone.utc),
                ),
            ]
        )
        await session.commit()

        async def fake_fetch_energy_json(self, path, params=None):
            if path == "/api/v1/energy/calendar/monthly":
                return {
                    "success": True,
                    "summary": {
                        "total_energy_kwh": 50.0,
                        "total_energy_cost_inr": 500.0,
                        "total_loss_kwh": 2.0,
                        "total_loss_cost_inr": 20.0,
                    },
                    "days": [
                        {
                            "date": local_day.isoformat(),
                            "energy_kwh": 12.0,
                            "energy_cost_inr": 120.0,
                            "loss_kwh": 2.5,
                            "loss_cost_inr": 25.0,
                        }
                    ],
                }
            return None

        monkeypatch.setattr(LiveDashboardService, "_fetch_energy_json", fake_fetch_energy_json)
        monkeypatch.setattr(
            TariffCache,
            "get",
            AsyncMock(return_value={"configured": True, "rate": 10.0, "currency": "INR"}),
        )

        payload = await LiveDashboardService(session).get_dashboard_summary(tenant_id="TENANT-A")

    assert payload["energy_widgets"]["today_loss_kwh"] == 2.5
    assert payload["energy_widgets"]["today_loss_cost_inr"] == 25.0
    assert payload["energy_widgets"]["today_energy_kwh"] == 12.0
    assert payload["energy_widgets"]["today_energy_cost_inr"] == 120.0


@pytest.mark.asyncio
async def test_fleet_dashboard_zero_canonical_loss_overrides_stale_live(
    monkeypatch: pytest.MonkeyPatch,
    session_factory,
):
    local_day = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata")).date()
    month_bucket = local_day.replace(day=1)
    async with session_factory() as session:
        session.add_all(
            [
                Device(
                    device_id="ZERO-FLEET-DEV",
                    tenant_id="TENANT-A",
                    plant_id="PLANT-1",
                    device_name="Zero Fleet",
                    device_type="compressor",
                ),
                DeviceLiveState(
                    device_id="ZERO-FLEET-DEV",
                    tenant_id="TENANT-A",
                    runtime_status="running",
                    day_bucket=local_day,
                    month_bucket=month_bucket,
                    today_energy_kwh=10.0,
                    today_loss_kwh=5.0,
                    today_loss_cost_inr=50.0,
                    today_idle_kwh=2.0,
                    today_offhours_kwh=2.0,
                    today_overconsumption_kwh=1.0,
                    month_energy_kwh=50.0,
                    last_telemetry_ts=datetime.now(timezone.utc),
                ),
            ]
        )
        await session.commit()

        async def fake_fetch_energy_json(self, path, params=None):
            if path == "/api/v1/energy/calendar/monthly":
                return {
                    "success": True,
                    "summary": {
                        "total_energy_kwh": 50.0,
                        "total_energy_cost_inr": 500.0,
                    },
                    "days": [
                        {
                            "date": local_day.isoformat(),
                            "energy_kwh": 10.0,
                            "energy_cost_inr": 100.0,
                            "loss_kwh": 0.0,
                            "loss_cost_inr": 0.0,
                        }
                    ],
                }
            return None

        monkeypatch.setattr(LiveDashboardService, "_fetch_energy_json", fake_fetch_energy_json)
        monkeypatch.setattr(
            TariffCache,
            "get",
            AsyncMock(return_value={"configured": True, "rate": 10.0, "currency": "INR"}),
        )

        payload = await LiveDashboardService(session).get_dashboard_summary(tenant_id="TENANT-A")

    assert payload["energy_widgets"]["today_loss_kwh"] == 0.0
    assert payload["energy_widgets"]["today_loss_cost_inr"] == 0.0


@pytest.mark.asyncio
async def test_fleet_dashboard_plant_scoped_passes_device_ids_to_calendar(
    monkeypatch: pytest.MonkeyPatch,
    session_factory,
):
    local_day = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata")).date()
    month_bucket = local_day.replace(day=1)
    captured_params = {}

    async with session_factory() as session:
        session.add_all(
            [
                Device(
                    device_id="PLANT-DEV-1",
                    tenant_id="TENANT-A",
                    plant_id="PLANT-1",
                    device_name="Plant Dev 1",
                    device_type="compressor",
                ),
                DeviceLiveState(
                    device_id="PLANT-DEV-1",
                    tenant_id="TENANT-A",
                    runtime_status="running",
                    day_bucket=local_day,
                    month_bucket=month_bucket,
                    today_energy_kwh=10.0,
                    today_loss_kwh=1.0,
                    month_energy_kwh=50.0,
                    last_telemetry_ts=datetime.now(timezone.utc),
                ),
            ]
        )
        await session.commit()

        async def fake_fetch_energy_json(self, path, params=None):
            if path == "/api/v1/energy/calendar/monthly":
                nonlocal captured_params
                captured_params = dict(params or {})
                return {
                    "success": True,
                    "summary": {"total_energy_kwh": 50.0, "total_energy_cost_inr": 500.0},
                    "days": [],
                }
            return None

        monkeypatch.setattr(LiveDashboardService, "_fetch_energy_json", fake_fetch_energy_json)
        monkeypatch.setattr(
            TariffCache,
            "get",
            AsyncMock(return_value={"configured": True, "rate": 10.0, "currency": "INR"}),
        )

        await LiveDashboardService(session).get_dashboard_summary(
            tenant_id="TENANT-A",
            plant_id="PLANT-1",
        )

    assert "device_ids" in captured_params
    assert "PLANT-DEV-1" in captured_params["device_ids"]
    assert captured_params.get("plant_id") == "PLANT-1"


@pytest.mark.asyncio
async def test_fleet_dashboard_plant_scoped_uses_canonical_values(
    monkeypatch: pytest.MonkeyPatch,
    session_factory,
):
    local_day = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata")).date()
    month_bucket = local_day.replace(day=1)
    async with session_factory() as session:
        session.add_all(
            [
                Device(
                    device_id="PLANT-CANONICAL-DEV",
                    tenant_id="TENANT-A",
                    plant_id="PLANT-1",
                    device_name="Plant Canonical",
                    device_type="compressor",
                ),
                DeviceLiveState(
                    device_id="PLANT-CANONICAL-DEV",
                    tenant_id="TENANT-A",
                    runtime_status="running",
                    day_bucket=local_day,
                    month_bucket=month_bucket,
                    today_energy_kwh=8.0,
                    today_loss_kwh=0.5,
                    today_loss_cost_inr=5.0,
                    month_energy_kwh=40.0,
                    last_telemetry_ts=datetime.now(timezone.utc),
                ),
            ]
        )
        await session.commit()

        async def fake_fetch_energy_json(self, path, params=None):
            if path == "/api/v1/energy/calendar/monthly":
                return {
                    "success": True,
                    "summary": {
                        "total_energy_kwh": 42.0,
                        "total_energy_cost_inr": 420.0,
                    },
                    "days": [
                        {
                            "date": local_day.isoformat(),
                            "energy_kwh": 10.0,
                            "energy_cost_inr": 100.0,
                            "loss_kwh": 1.5,
                            "loss_cost_inr": 15.0,
                        }
                    ],
                }
            return None

        monkeypatch.setattr(LiveDashboardService, "_fetch_energy_json", fake_fetch_energy_json)
        monkeypatch.setattr(
            TariffCache,
            "get",
            AsyncMock(return_value={"configured": True, "rate": 10.0, "currency": "INR"}),
        )

        payload = await LiveDashboardService(session).get_dashboard_summary(
            tenant_id="TENANT-A",
            plant_id="PLANT-1",
        )

    assert payload["energy_widgets"]["today_energy_kwh"] == 10.0
    assert payload["energy_widgets"]["today_energy_cost_inr"] == 100.0
    assert payload["energy_widgets"]["today_loss_kwh"] == 1.5
    assert payload["energy_widgets"]["today_loss_cost_inr"] == 15.0
    assert payload["energy_widgets"]["month_energy_kwh"] == 42.0
    assert payload["energy_widgets"]["month_energy_cost_inr"] == 420.0


@pytest.mark.asyncio
async def test_fleet_dashboard_tenant_wide_does_not_pass_device_ids_to_calendar(
    monkeypatch: pytest.MonkeyPatch,
    session_factory,
):
    local_day = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata")).date()
    month_bucket = local_day.replace(day=1)
    captured_params = {}

    async with session_factory() as session:
        session.add_all(
            [
                Device(
                    device_id="TENANT-DEV-1",
                    tenant_id="TENANT-A",
                    plant_id="PLANT-1",
                    device_name="Tenant Dev 1",
                    device_type="compressor",
                ),
                DeviceLiveState(
                    device_id="TENANT-DEV-1",
                    tenant_id="TENANT-A",
                    runtime_status="running",
                    day_bucket=local_day,
                    month_bucket=month_bucket,
                    today_energy_kwh=10.0,
                    today_loss_kwh=1.0,
                    month_energy_kwh=50.0,
                    last_telemetry_ts=datetime.now(timezone.utc),
                ),
            ]
        )
        await session.commit()

        async def fake_fetch_energy_json(self, path, params=None):
            if path == "/api/v1/energy/calendar/monthly":
                nonlocal captured_params
                captured_params = dict(params or {})
                return {
                    "success": True,
                    "summary": {"total_energy_kwh": 50.0, "total_energy_cost_inr": 500.0},
                    "days": [],
                }
            return None

        monkeypatch.setattr(LiveDashboardService, "_fetch_energy_json", fake_fetch_energy_json)
        monkeypatch.setattr(
            TariffCache,
            "get",
            AsyncMock(return_value={"configured": True, "rate": 10.0, "currency": "INR"}),
        )

        await LiveDashboardService(session).get_dashboard_summary(tenant_id="TENANT-A")

    assert "device_ids" not in captured_params
    assert "plant_id" not in captured_params


@pytest.mark.asyncio
async def test_dashboard_summary_today_loss_not_greater_than_today_energy(
    monkeypatch: pytest.MonkeyPatch,
    session_factory,
):
    from app.services import live_dashboard as _ld_module

    class _FrozenDatetime:
        @staticmethod
        def now(tz=None):
            import datetime as _dt
            return _dt.datetime(2026, 4, 5, 14, 0, 0, tzinfo=tz)

    local_day = date(2026, 4, 5)
    month_bucket = date(2026, 4, 1)

    async with session_factory() as session:
        session.add_all(
            [
                Device(device_id="INV-D1", tenant_id="TENANT-A", plant_id="PLANT-1", device_name="D1", device_type="compressor"),
                Device(device_id="INV-D2", tenant_id="TENANT-A", plant_id="PLANT-1", device_name="D2", device_type="compressor"),
                DeviceLiveState(
                    device_id="INV-D1",
                    tenant_id="TENANT-A",
                    runtime_status="running",
                    day_bucket=local_day,
                    month_bucket=month_bucket,
                    today_energy_kwh=10.0,
                    today_loss_kwh=1.0,
                    today_idle_kwh=0.4,
                    today_offhours_kwh=0.3,
                    today_overconsumption_kwh=0.3,
                    month_energy_kwh=50.0,
                ),
                DeviceLiveState(
                    device_id="INV-D2",
                    tenant_id="TENANT-A",
                    runtime_status="running",
                    day_bucket=local_day,
                    month_bucket=month_bucket,
                    today_energy_kwh=5.0,
                    today_loss_kwh=0.6,
                    today_idle_kwh=0.2,
                    today_offhours_kwh=0.2,
                    today_overconsumption_kwh=0.2,
                    month_energy_kwh=30.0,
                ),
            ]
        )
        await session.commit()

        monkeypatch.setattr(_ld_module, "datetime", _FrozenDatetime)
        monkeypatch.setattr(LiveDashboardService, "_fetch_energy_json", AsyncMock(return_value=None))
        monkeypatch.setattr(TariffCache, "get", AsyncMock(return_value={"configured": True, "rate": 10.0, "currency": "INR"}))

        payload = await LiveDashboardService(session).get_dashboard_summary(tenant_id="TENANT-A")

    assert payload["energy_widgets"]["today_loss_kwh"] <= payload["energy_widgets"]["today_energy_kwh"]

    async with session_factory() as session:
        session.add_all(
            [
                Device(device_id="INV-FULL", tenant_id="TENANT-B", plant_id="PLANT-1", device_name="Full Loss", device_type="compressor"),
                DeviceLiveState(
                    device_id="INV-FULL",
                    tenant_id="TENANT-B",
                    runtime_status="running",
                    day_bucket=local_day,
                    month_bucket=month_bucket,
                    today_energy_kwh=5.0,
                    today_loss_kwh=5.0,
                    today_idle_kwh=2.0,
                    today_offhours_kwh=2.0,
                    today_overconsumption_kwh=1.0,
                    month_energy_kwh=25.0,
                ),
            ]
        )
        await session.commit()

        monkeypatch.setattr(_ld_module, "datetime", _FrozenDatetime)
        monkeypatch.setattr(LiveDashboardService, "_fetch_energy_json", AsyncMock(return_value=None))
        monkeypatch.setattr(TariffCache, "get", AsyncMock(return_value={"configured": True, "rate": 10.0, "currency": "INR"}))

        payload = await LiveDashboardService(session).get_dashboard_summary(tenant_id="TENANT-B")

    assert payload["energy_widgets"]["today_loss_kwh"] == payload["energy_widgets"]["today_energy_kwh"]


@pytest.mark.asyncio
async def test_dashboard_summary_today_energy_and_loss_match_calendar_today_cell(
    monkeypatch: pytest.MonkeyPatch,
    session_factory,
):
    from app.services import live_dashboard as _ld_module

    class _FrozenDatetime:
        @staticmethod
        def now(tz=None):
            import datetime as _dt
            return _dt.datetime(2026, 4, 5, 14, 0, 0, tzinfo=tz)

    local_day = date(2026, 4, 5)
    month_bucket = date(2026, 4, 1)

    async with session_factory() as session:
        session.add_all(
            [
                Device(device_id="CAL-PARITY", tenant_id="TENANT-A", plant_id="PLANT-1", device_name="Cal Parity", device_type="compressor"),
                DeviceLiveState(
                    device_id="CAL-PARITY",
                    tenant_id="TENANT-A",
                    runtime_status="running",
                    day_bucket=local_day,
                    month_bucket=month_bucket,
                    today_energy_kwh=5.0,
                    today_loss_kwh=0.5,
                    today_idle_kwh=0.2,
                    today_offhours_kwh=0.2,
                    today_overconsumption_kwh=0.1,
                    today_loss_cost_inr=5.0,
                    month_energy_kwh=50.0,
                ),
            ]
        )
        await session.commit()

        calendar_payload = {
            "success": True,
            "summary": {
                "total_energy_kwh": 50.0,
                "total_energy_cost_inr": 500.0,
            },
            "days": [
                {
                    "date": "2026-04-05",
                    "energy_kwh": 12.0,
                    "energy_cost_inr": 120.0,
                    "loss_kwh": 2.5,
                    "loss_cost_inr": 25.0,
                },
            ],
        }

        async def fake_fetch_energy_json(self, path, params=None):
            if path == "/api/v1/energy/calendar/monthly":
                return calendar_payload
            return None

        monkeypatch.setattr(_ld_module, "datetime", _FrozenDatetime)
        monkeypatch.setattr(LiveDashboardService, "_fetch_energy_json", fake_fetch_energy_json)
        monkeypatch.setattr(TariffCache, "get", AsyncMock(return_value={"configured": True, "rate": 10.0, "currency": "INR"}))

        service = LiveDashboardService(session)
        summary = await service.get_dashboard_summary(tenant_id="TENANT-A")
        calendar = await service.get_monthly_energy_calendar(year=2026, month=4, tenant_id="TENANT-A")

    today_cell = next(d for d in calendar["days"] if d["date"] == "2026-04-05")

    assert summary["energy_widgets"]["today_energy_kwh"] == today_cell["energy_kwh"]
    assert summary["energy_widgets"]["today_loss_kwh"] == today_cell["loss_kwh"]
    assert summary["energy_widgets"]["today_loss_kwh"] <= summary["energy_widgets"]["today_energy_kwh"]
