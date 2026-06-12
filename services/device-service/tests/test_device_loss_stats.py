from __future__ import annotations

import os
import sys
from datetime import date, datetime, timezone
from datetime import timedelta
from pathlib import Path
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

os.environ["DATABASE_URL"] = "mysql+aiomysql://test:test@127.0.0.1:3306/test_db"
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

from app.api.v1 import devices as devices_api
from app.api.v1.devices import _refresh_loss_views_after_waste_config_change
from app.database import Base
from app.models.device import Device, DeviceLiveState
from app.services import dashboard as dashboard_module
from app.services import idle_running as idle_running_module
from app.services import live_projection as live_projection_module
from app.services.dashboard import DashboardService
from app.services.idle_running import IdleRunningService, ThresholdConfigurationError
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


def _tenant_ctx(tenant_id: str) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        user_id="tester",
        role="org_admin",
        plant_ids=[],
        is_super_admin=False,
    )


@pytest.mark.asyncio
async def test_get_device_loss_stats_reads_tenant_scoped_live_state(session_factory, monkeypatch: pytest.MonkeyPatch):
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
                ),
                Device(
                    device_id="SHARED-DEVICE-B",
                    tenant_id="TENANT-B",
                    plant_id="PLANT-1",
                    device_name="Shared B",
                    device_type="compressor",
                ),
                DeviceLiveState(
                    device_id="SHARED-DEVICE-A",
                    tenant_id="TENANT-A",
                    runtime_status="running",
                    load_state="running",
                    day_bucket=local_day,
                    today_idle_kwh=1.2,
                    today_offhours_kwh=0.3,
                    today_overconsumption_kwh=0.8,
                    today_loss_kwh=2.3,
                    today_energy_kwh=12.0,
                    version=1,
                ),
                DeviceLiveState(
                    device_id="SHARED-DEVICE-B",
                    tenant_id="TENANT-B",
                    runtime_status="running",
                    load_state="running",
                    day_bucket=local_day,
                    today_idle_kwh=0.1,
                    today_offhours_kwh=0.0,
                    today_overconsumption_kwh=0.0,
                    today_loss_kwh=0.1,
                    today_energy_kwh=3.0,
                    version=1,
                ),
            ]
        )
        await session.commit()

        monkeypatch.setattr(DashboardService, "_get_tariff", AsyncMock(return_value=(5.0, "INR")))

        payload_a = await DashboardService(session, _tenant_ctx("TENANT-A")).get_device_loss_stats(
            "SHARED-DEVICE-A",
            "TENANT-A",
        )
        payload_b = await DashboardService(session, _tenant_ctx("TENANT-B")).get_device_loss_stats(
            "SHARED-DEVICE-B",
            "TENANT-B",
        )

    assert payload_a["today"]["overconsumption_kwh"] == 0.8
    assert payload_a["today"]["total_loss_kwh"] == 2.3
    assert payload_a["today"]["total_loss_cost_inr"] == 11.5
    assert payload_b["today"]["overconsumption_kwh"] == 0.0
    assert payload_b["today"]["total_loss_kwh"] == 0.1


@pytest.mark.asyncio
async def test_get_device_loss_stats_ignores_previous_day_live_state(session_factory, monkeypatch: pytest.MonkeyPatch):
    stale_day = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata")).date() - timedelta(days=1)

    async with session_factory() as session:
        session.add_all(
            [
                Device(
                    device_id="STALE-LOSS-DEVICE",
                    tenant_id="TENANT-A",
                    plant_id="PLANT-1",
                    device_name="Stale Loss Device",
                    device_type="compressor",
                ),
                DeviceLiveState(
                    device_id="STALE-LOSS-DEVICE",
                    tenant_id="TENANT-A",
                    runtime_status="running",
                    load_state="running",
                    day_bucket=stale_day,
                    today_idle_kwh=1.0,
                    today_offhours_kwh=0.5,
                    today_overconsumption_kwh=0.7,
                    today_loss_kwh=2.2,
                    today_energy_kwh=11.0,
                    version=1,
                ),
            ]
        )
        await session.commit()

        monkeypatch.setattr(DashboardService, "_get_tariff", AsyncMock(return_value=(5.0, "INR")))

        payload = await DashboardService(session, _tenant_ctx("TENANT-A")).get_device_loss_stats(
            "STALE-LOSS-DEVICE",
            "TENANT-A",
        )

    assert payload["today"]["idle_kwh"] == 0.0
    assert payload["today"]["off_hours_kwh"] == 0.0
    assert payload["today"]["overconsumption_kwh"] == 0.0
    assert payload["today"]["total_loss_kwh"] == 0.0
    assert payload["today"]["today_energy_kwh"] == 0.0
    assert payload["today"]["total_loss_cost_inr"] == 0.0


@pytest.mark.asyncio
async def test_refresh_loss_views_after_waste_config_change_runs_recompute_and_snapshot_refresh(monkeypatch: pytest.MonkeyPatch):
    recompute = AsyncMock(return_value={"device_id": "DEVICE-1"})
    sync_energy = AsyncMock(return_value={"attempted": True, "updated": 1})
    materialize_loss = AsyncMock(return_value=({}, {}))
    materialize_summary = AsyncMock(return_value={"success": True})

    monkeypatch.setattr(
        live_projection_module.LiveProjectionService,
        "recompute_today_loss_projection",
        recompute,
    )
    monkeypatch.setattr(
        dashboard_module.DashboardService,
        "materialize_energy_and_loss_snapshots",
        materialize_loss,
    )
    monkeypatch.setattr(devices_api, "sync_energy_device_days", sync_energy)
    monkeypatch.setattr(
        dashboard_module.DashboardService,
        "materialize_dashboard_summary_snapshot",
        materialize_summary,
    )

    db = SimpleNamespace()
    await _refresh_loss_views_after_waste_config_change(
        db,
        tenant_id="TENANT-A",
        device_id="DEVICE-1",
    )

    recompute.assert_awaited_once_with("DEVICE-1", "TENANT-A")
    sync_energy.assert_awaited_once()
    materialize_loss.assert_awaited_once()
    materialize_summary.assert_awaited_once()


@pytest.mark.asyncio
async def test_idle_stats_remain_idle_only_and_are_not_polluted_by_loss_fields(session_factory, monkeypatch: pytest.MonkeyPatch):
    async with session_factory() as session:
        session.add(
            Device(
                device_id="IDLE-ONLY",
                tenant_id="TENANT-A",
                plant_id="PLANT-1",
                device_name="Idle Only",
                device_type="compressor",
                data_source_type="metered",
                full_load_current_a=4.0,
            )
        )
        await session.commit()

        async def fake_tariff_get(cls, tenant_id):
            return {"configured": True, "rate": 2.5, "currency": "INR", "stale": False, "cache": "miss"}

        monkeypatch.setattr(idle_running_module.TariffCache, "get", classmethod(fake_tariff_get))
        monkeypatch.setattr(IdleRunningService, "aggregate_device_idle", AsyncMock(return_value=None))

        service = IdleRunningService(session, _tenant_ctx("TENANT-A"))
        result = await service.get_idle_stats("IDLE-ONLY", "TENANT-A")

    assert result["device_id"] == "IDLE-ONLY"
    assert "overconsumption_kwh" not in (result["today"] or {})
    assert "total_loss_kwh" not in (result["today"] or {})


@pytest.mark.asyncio
async def test_idle_config_rejects_invalid_idle_threshold_pct(session_factory):
    async with session_factory() as session:
        session.add(
            Device(
                device_id="GAP-DEVICE",
                tenant_id="TENANT-A",
                plant_id="PLANT-1",
                device_name="Gap Device",
                device_type="compressor",
                full_load_current_a=4.0,
            )
        )
        await session.commit()

        service = IdleRunningService(session, _tenant_ctx("TENANT-A"))

        with pytest.raises(ThresholdConfigurationError):
            await service.set_idle_config(
                "GAP-DEVICE",
                "TENANT-A",
                full_load_current_a=None,
                idle_threshold_pct_of_fla=1.0,
            )


@pytest.mark.asyncio
async def test_idle_stats_today_energy_prefers_live_state_for_current_day(session_factory, monkeypatch: pytest.MonkeyPatch):
    local_day = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata")).date()

    async with session_factory() as session:
        session.add_all(
            [
                Device(
                    device_id="IDLE-LIVE",
                    tenant_id="TENANT-A",
                    plant_id="PLANT-1",
                    device_name="Idle Live",
                    device_type="compressor",
                    data_source_type="metered",
                    full_load_current_a=4.0,
                ),
                DeviceLiveState(
                    device_id="IDLE-LIVE",
                    tenant_id="TENANT-A",
                    runtime_status="running",
                    load_state="idle",
                    day_bucket=local_day,
                    today_idle_kwh=0.4321,
                    version=1,
                ),
            ]
        )
        await session.commit()

        async def fake_tariff_get(cls, tenant_id):
            return {"configured": True, "rate": 2.5, "currency": "INR", "stale": False, "cache": "miss"}

        monkeypatch.setattr(idle_running_module.TariffCache, "get", classmethod(fake_tariff_get))
        monkeypatch.setattr(IdleRunningService, "aggregate_device_idle", AsyncMock(return_value=None))

        service = IdleRunningService(session, _tenant_ctx("TENANT-A"))
        result = await service.get_idle_stats("IDLE-LIVE", "TENANT-A")

    assert result["today"]["idle_energy_kwh"] == pytest.approx(0.4321, abs=1e-4)
    assert result["today"]["idle_cost"] == pytest.approx(1.08, abs=1e-2)


@pytest.mark.asyncio
async def test_get_device_loss_stats_is_not_equal_to_tenant_dashboard_loss_when_multiple_devices(
    session_factory, monkeypatch: pytest.MonkeyPatch
):
    from app.services import live_dashboard as _ld_module
    from app.services import dashboard as _dash_module

    class _FrozenDatetime:
        @staticmethod
        def now(tz=None):
            import datetime as _dt
            return _dt.datetime(2026, 4, 5, 14, 0, 0, tzinfo=tz)

    local_day = date(2026, 4, 5)

    async with session_factory() as session:
        session.add_all(
            [
                Device(device_id="SCOPE-D1", tenant_id="TENANT-A", plant_id="PLANT-1", device_name="Scope D1", device_type="compressor"),
                Device(device_id="SCOPE-D2", tenant_id="TENANT-A", plant_id="PLANT-1", device_name="Scope D2", device_type="compressor"),
                DeviceLiveState(
                    device_id="SCOPE-D1",
                    tenant_id="TENANT-A",
                    runtime_status="running",
                    load_state="running",
                    day_bucket=local_day,
                    today_idle_kwh=1.2,
                    today_offhours_kwh=0.3,
                    today_overconsumption_kwh=0.8,
                    today_loss_kwh=2.3,
                    today_energy_kwh=12.0,
                    version=1,
                ),
                DeviceLiveState(
                    device_id="SCOPE-D2",
                    tenant_id="TENANT-A",
                    runtime_status="running",
                    load_state="running",
                    day_bucket=local_day,
                    today_idle_kwh=0.2,
                    today_offhours_kwh=0.1,
                    today_overconsumption_kwh=0.2,
                    today_loss_kwh=0.5,
                    today_energy_kwh=6.0,
                    version=1,
                ),
            ]
        )
        await session.commit()

        monkeypatch.setattr(_dash_module, "datetime", _FrozenDatetime)
        monkeypatch.setattr(DashboardService, "_get_tariff", AsyncMock(return_value=(10.0, "INR")))

        device_loss = await DashboardService(session, _tenant_ctx("TENANT-A")).get_device_loss_stats(
            "SCOPE-D1", "TENANT-A"
        )

        monkeypatch.setattr(_ld_module, "datetime", _FrozenDatetime)
        monkeypatch.setattr(LiveDashboardService, "_fetch_energy_json", AsyncMock(return_value=None))
        monkeypatch.setattr(TariffCache, "get", AsyncMock(return_value={"configured": True, "rate": 10.0, "currency": "INR"}))

        dashboard_payload = await LiveDashboardService(session).get_dashboard_summary(tenant_id="TENANT-A")

    single_device_loss = device_loss["today"]["total_loss_kwh"]
    tenant_total_loss = dashboard_payload["energy_widgets"]["today_loss_kwh"]

    assert single_device_loss != tenant_total_loss
    assert single_device_loss == 2.3
    assert tenant_total_loss == pytest.approx(2.8, abs=1e-4)
