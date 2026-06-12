from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "services" / "energy-service"))
sys.path.insert(1, str(ROOT))

from app.models import Base, EnergyDeviceDay, EnergyDeviceMonth, EnergyFleetDay, EnergyFleetMonth  # noqa: E402
from app.services import reconciliation_preview as preview_module  # noqa: E402
from app.services.energy_engine import EnergyEngine  # noqa: E402
from app.services.reconciliation_preview import ReconciliationPreviewRequest, ReconciliationPreviewService  # noqa: E402


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_live_update_persists_tenant_id_on_energy_aggregate_rows(session_factory, monkeypatch):
    async def fake_meta(_device_id: str, tenant_id: str | None = None):
        return {
            "device_name": "Machine A",
            "idle_threshold": None,
            "over_threshold": None,
            "shifts": [{"shift_start": "00:00", "shift_end": "23:59", "is_active": True}],
            "energy_flow_mode": "consumption_only",
            "polarity_mode": "normal",
            "tenant_id": tenant_id,
        }

    async def fake_tariff(_tenant_id: str | None):
        return {"rate": 6.5, "currency": "INR"}

    monkeypatch.setattr("app.services.energy_engine.meta_cache.get", fake_meta)
    monkeypatch.setattr("app.services.energy_engine.tariff_cache.get", fake_tariff)

    async with session_factory() as session:
        engine = EnergyEngine(session)
        await engine.apply_live_update(
            device_id="DEVICE-1",
            telemetry={"device_id": "DEVICE-1", "timestamp": "2026-04-20T00:00:00+00:00", "energy_kwh": 0.0, "power": 1200.0},
            tenant_id="SH00000001",
        )
        await engine.apply_live_update(
            device_id="DEVICE-1",
            telemetry={"device_id": "DEVICE-1", "timestamp": "2026-04-20T00:05:00+00:00", "energy_kwh": 0.1, "power": 1200.0},
            tenant_id="SH00000001",
        )

        day_row = (
            await session.execute(
                select(EnergyDeviceDay).where(
                    EnergyDeviceDay.tenant_id == "SH00000001",
                    EnergyDeviceDay.device_id == "DEVICE-1",
                    EnergyDeviceDay.day == date(2026, 4, 20),
                )
            )
        ).scalar_one()
        month_row = (
            await session.execute(
                select(EnergyDeviceMonth).where(
                    EnergyDeviceMonth.tenant_id == "SH00000001",
                    EnergyDeviceMonth.device_id == "DEVICE-1",
                    EnergyDeviceMonth.month == date(2026, 4, 1),
                )
            )
        ).scalar_one()
        fleet_day_row = (
            await session.execute(
                select(EnergyFleetDay).where(
                    EnergyFleetDay.tenant_id == "SH00000001",
                    EnergyFleetDay.day == date(2026, 4, 20),
                )
            )
        ).scalar_one()
        fleet_month_row = (
            await session.execute(
                select(EnergyFleetMonth).where(
                    EnergyFleetMonth.tenant_id == "SH00000001",
                    EnergyFleetMonth.month == date(2026, 4, 1),
                )
            )
        ).scalar_one()

    assert day_row.tenant_id == "SH00000001"
    assert month_row.tenant_id == "SH00000001"
    assert fleet_day_row.tenant_id == "SH00000001"
    assert fleet_month_row.tenant_id == "SH00000001"
    assert day_row.energy_kwh > 0
    assert month_row.energy_kwh == day_row.energy_kwh
    assert fleet_day_row.energy_kwh == day_row.energy_kwh
    assert fleet_month_row.energy_kwh == month_row.energy_kwh


@pytest.mark.asyncio
async def test_summary_filters_directly_by_tenant_id(session_factory, monkeypatch):
    async def fake_tariff(_tenant_id: str | None):
        return {"rate": 10.0, "currency": "INR"}

    async def fail_live_widgets(_tenant_id: str | None):
        return None

    async def fail_allowed(_tenant_id: str | None):
        raise AssertionError("get_summary should not rely on allowed device joins for persisted tenant scope")

    monkeypatch.setattr("app.services.energy_engine.tariff_cache.get", fake_tariff)

    async with session_factory() as session:
        session.add_all(
            [
                EnergyFleetDay(
                    tenant_id="tenant-a",
                    day=date(2026, 4, 20),
                    energy_kwh=2.0,
                    energy_cost_inr=20.0,
                    loss_kwh=0.5,
                    loss_cost_inr=5.0,
                    version=1,
                ),
                EnergyFleetDay(
                    tenant_id="tenant-b",
                    day=date(2026, 4, 20),
                    energy_kwh=9.0,
                    energy_cost_inr=90.0,
                    loss_kwh=4.0,
                    loss_cost_inr=40.0,
                    version=1,
                ),
                EnergyFleetMonth(
                    tenant_id="tenant-a",
                    month=date(2026, 4, 1),
                    energy_kwh=5.0,
                    energy_cost_inr=50.0,
                    version=2,
                ),
                EnergyFleetMonth(
                    tenant_id="tenant-b",
                    month=date(2026, 4, 1),
                    energy_kwh=19.0,
                    energy_cost_inr=190.0,
                    version=2,
                ),
            ]
        )
        await session.commit()

        class _FrozenDatetime:
            @staticmethod
            def now(tz):
                import datetime as _dt

                return _dt.datetime(2026, 4, 20, 12, 0, 0, tzinfo=tz)

        engine = EnergyEngine(session)
        monkeypatch.setattr(engine, "_get_allowed_device_ids", fail_allowed)
        monkeypatch.setattr(engine, "_fetch_live_dashboard_energy_widgets", fail_live_widgets)
        monkeypatch.setattr("app.services.energy_engine.datetime", _FrozenDatetime)
        result = await engine.get_summary(tenant_id="tenant-a")

    assert result["energy_widgets"]["today_energy_kwh"] == 2.0
    assert result["energy_widgets"]["today_loss_kwh"] == 0.5
    assert result["energy_widgets"]["month_energy_kwh"] == 5.0
    assert result["energy_widgets"]["month_energy_cost_inr"] == 50.0


@pytest.mark.asyncio
async def test_summary_returns_safe_zero_shape_without_tenant_scope(session_factory, monkeypatch):
    async def fake_tariff(_tenant_id: str | None):
        return {"rate": 10.0, "currency": "INR"}

    monkeypatch.setattr("app.services.energy_engine.tariff_cache.get", fake_tariff)

    async with session_factory() as session:
        session.add_all(
            [
                EnergyFleetDay(
                    tenant_id="tenant-a",
                    day=date(2026, 4, 20),
                    energy_kwh=2.0,
                    energy_cost_inr=20.0,
                    loss_kwh=0.5,
                    loss_cost_inr=5.0,
                    version=1,
                ),
                EnergyFleetMonth(
                    tenant_id="tenant-a",
                    month=date(2026, 4, 1),
                    energy_kwh=5.0,
                    energy_cost_inr=50.0,
                    version=2,
                ),
            ]
        )
        await session.commit()

        class _FrozenDatetime:
            @staticmethod
            def now(tz):
                import datetime as _dt

                return _dt.datetime(2026, 4, 20, 12, 0, 0, tzinfo=tz)

        monkeypatch.setattr("app.services.energy_engine.datetime", _FrozenDatetime)
        result = await EnergyEngine(session).get_summary()

    assert result["version"] == 0
    assert result["energy_widgets"]["today_energy_kwh"] == 0.0
    assert result["energy_widgets"]["today_loss_kwh"] == 0.0
    assert result["energy_widgets"]["month_energy_kwh"] == 0.0


@pytest.mark.asyncio
async def test_today_loss_breakdown_filters_by_tenant_when_live_overlay_scope_unavailable(session_factory, monkeypatch):
    async def fake_tariff(_tenant_id: str | None):
        return {"rate": 10.0, "currency": "INR"}

    async def no_allowed(_tenant_id: str | None):
        return None

    async def fake_meta(device_id: str, tenant_id: str | None):
        return {"device_name": f"{tenant_id}:{device_id}"}

    monkeypatch.setattr("app.services.energy_engine.tariff_cache.get", fake_tariff)

    async with session_factory() as session:
        session.add_all(
            [
                EnergyDeviceDay(
                    tenant_id="tenant-a",
                    device_id="DEVICE-A1",
                    day=date(2026, 4, 20),
                    energy_kwh=1.0,
                    idle_kwh=0.2,
                    offhours_kwh=0.1,
                    overconsumption_kwh=0.1,
                    loss_kwh=0.4,
                    version=1,
                ),
                EnergyDeviceDay(
                    tenant_id="tenant-b",
                    device_id="DEVICE-B1",
                    day=date(2026, 4, 20),
                    energy_kwh=8.0,
                    idle_kwh=1.0,
                    offhours_kwh=1.0,
                    overconsumption_kwh=1.0,
                    loss_kwh=3.0,
                    version=1,
                ),
            ]
        )
        await session.commit()

        class _FrozenDatetime:
            @staticmethod
            def now(tz):
                import datetime as _dt

                return _dt.datetime(2026, 4, 20, 12, 0, 0, tzinfo=tz)

        engine = EnergyEngine(session)
        monkeypatch.setattr(engine, "_get_allowed_device_ids", no_allowed)
        monkeypatch.setattr("app.services.energy_engine.meta_cache.get", fake_meta)
        monkeypatch.setattr("app.services.energy_engine.datetime", _FrozenDatetime)
        result = await engine.get_today_loss_breakdown(tenant_id="tenant-a")

    assert result["totals"]["today_energy_kwh"] == 1.0
    assert result["totals"]["total_loss_kwh"] == 0.4
    assert [row["device_id"] for row in result["rows"]] == ["DEVICE-A1"]


@pytest.mark.asyncio
async def test_today_loss_breakdown_returns_safe_zero_shape_without_tenant_scope(session_factory, monkeypatch):
    async def fake_tariff(_tenant_id: str | None):
        return {"rate": 10.0, "currency": "INR"}

    monkeypatch.setattr("app.services.energy_engine.tariff_cache.get", fake_tariff)

    async with session_factory() as session:
        session.add(
            EnergyDeviceDay(
                tenant_id="tenant-a",
                device_id="DEVICE-A1",
                day=date(2026, 4, 20),
                energy_kwh=1.0,
                idle_kwh=0.2,
                offhours_kwh=0.1,
                overconsumption_kwh=0.1,
                loss_kwh=0.4,
                version=1,
            )
        )
        await session.commit()

        class _FrozenDatetime:
            @staticmethod
            def now(tz):
                import datetime as _dt

                return _dt.datetime(2026, 4, 20, 12, 0, 0, tzinfo=tz)

        monkeypatch.setattr("app.services.energy_engine.datetime", _FrozenDatetime)
        result = await EnergyEngine(session).get_today_loss_breakdown()

    assert result["version"] == 0
    assert result["rows"] == []
    assert result["totals"]["today_energy_kwh"] == 0.0
    assert result["totals"]["total_loss_kwh"] == 0.0


@pytest.mark.asyncio
async def test_monthly_calendar_filters_directly_by_fleet_tenant_id(session_factory, monkeypatch):
    async def fake_tariff(_tenant_id: str | None):
        return {"rate": 10.0, "currency": "INR"}

    async def fail_live_totals(_tenant_id: str | None, plant_id=None):
        return None

    monkeypatch.setattr("app.services.energy_engine.tariff_cache.get", fake_tariff)

    async with session_factory() as session:
        session.add_all(
            [
                EnergyFleetDay(
                    tenant_id="tenant-a",
                    day=date(2026, 4, 20),
                    energy_kwh=2.0,
                    energy_cost_inr=20.0,
                    version=3,
                ),
                EnergyFleetDay(
                    tenant_id="tenant-a",
                    day=date(2026, 4, 21),
                    energy_kwh=1.5,
                    energy_cost_inr=15.0,
                    version=4,
                ),
                EnergyFleetDay(
                    tenant_id="tenant-b",
                    day=date(2026, 4, 20),
                    energy_kwh=8.0,
                    energy_cost_inr=80.0,
                    version=5,
                ),
            ]
        )
        await session.commit()

        class _FrozenDatetime:
            @staticmethod
            def now(tz):
                import datetime as _dt

                return _dt.datetime(2026, 4, 25, 12, 0, 0, tzinfo=tz)

        engine = EnergyEngine(session)
        monkeypatch.setattr(engine, "_fetch_live_dashboard_today_totals", fail_live_totals)
        monkeypatch.setattr("app.services.energy_engine.datetime", _FrozenDatetime)
        result = await engine.get_monthly_calendar(2026, 4, tenant_id="tenant-a")

    assert result["summary"]["total_energy_kwh"] == 3.5
    assert result["summary"]["total_energy_cost_inr"] == 35.0
    assert result["days"][19] == {"date": "2026-04-20", "energy_kwh": 2.0, "energy_cost_inr": 20.0, "loss_kwh": 0.0, "loss_cost_inr": 0.0}
    assert result["days"][20] == {"date": "2026-04-21", "energy_kwh": 1.5, "energy_cost_inr": 15.0, "loss_kwh": 0.0, "loss_cost_inr": 0.0}


@pytest.mark.asyncio
async def test_monthly_calendar_returns_safe_zero_shape_without_tenant_scope(session_factory, monkeypatch):
    async def fake_tariff(_tenant_id: str | None):
        return {"rate": 10.0, "currency": "INR"}

    monkeypatch.setattr("app.services.energy_engine.tariff_cache.get", fake_tariff)

    async with session_factory() as session:
        session.add(
            EnergyFleetDay(
                tenant_id="tenant-a",
                day=date(2026, 4, 20),
                energy_kwh=2.0,
                energy_cost_inr=20.0,
                version=3,
            )
        )
        await session.commit()

        result = await EnergyEngine(session).get_monthly_calendar(2026, 4)

    assert result["version"] == 0
    assert result["summary"]["total_energy_kwh"] == 0.0
    assert len(result["days"]) == 30
    assert result["days"][19] == {"date": "2026-04-20", "energy_kwh": 0.0, "energy_cost_inr": 0.0, "loss_kwh": 0.0, "loss_cost_inr": 0.0}


@pytest.mark.asyncio
async def test_device_range_returns_safe_zero_shape_without_tenant_scope(session_factory):
    async with session_factory() as session:
        session.add(
            EnergyDeviceDay(
                tenant_id="tenant-a",
                device_id="DEVICE-A1",
                day=date(2026, 4, 20),
                energy_kwh=3.0,
                loss_kwh=0.6,
                idle_kwh=0.2,
                offhours_kwh=0.1,
                overconsumption_kwh=0.3,
                version=5,
            )
        )
        await session.commit()

        result = await EnergyEngine(session).get_device_range("DEVICE-A1", date(2026, 4, 20), date(2026, 4, 20))

    assert result["version"] == 0
    assert result["days"] == []
    assert result["totals"]["energy_kwh"] == 0.0
    assert result["totals"]["loss_kwh"] == 0.0


@pytest.mark.asyncio
async def test_apply_device_lifecycle_requires_tenant_ownership(session_factory):
    async with session_factory() as session:
        session.add(
            EnergyDeviceDay(
                tenant_id="tenant-a",
                device_id="DEVICE-A1",
                day=date(2026, 4, 20),
                energy_kwh=1.0,
                version=1,
            )
        )
        await session.commit()

        result = await EnergyEngine(session).apply_device_lifecycle(
            device_id="DEVICE-A1",
            status="stopped",
            tenant_id="tenant-b",
        )

    assert result == {
        "device_id": "DEVICE-A1",
        "error": "device_not_found_for_tenant",
        "session_state": None,
        "version": 0,
    }


@pytest.mark.asyncio
async def test_reconciliation_preview_fallback_scope_uses_tenant_column(session_factory, monkeypatch):
    async def fake_meta(_device_id: str, tenant_id: str | None = None):
        return {
            "shifts": [],
            "idle_threshold": None,
            "over_threshold": None,
            "energy_flow_mode": "consumption_only",
            "polarity_mode": "normal",
        }

    async def no_devices(_tenant_id: str | None):
        return set()

    monkeypatch.setattr(preview_module.meta_cache, "get", fake_meta)

    async with session_factory() as session:
        session.add_all(
            [
                EnergyDeviceDay(
                    tenant_id="tenant-a",
                    device_id="DEVICE-A1",
                    day=date(2026, 4, 20),
                    energy_kwh=5.0,
                    quality_flags="[]",
                    version=1,
                ),
                EnergyDeviceDay(
                    tenant_id="tenant-b",
                    device_id="DEVICE-B1",
                    day=date(2026, 4, 20),
                    energy_kwh=7.0,
                    quality_flags="[]",
                    version=1,
                ),
            ]
        )
        await session.commit()

        service = ReconciliationPreviewService(session)
        monkeypatch.setattr(service._engine, "_get_allowed_device_ids", no_devices)

        scoped = await service._resolve_device_scope(
            ReconciliationPreviewRequest(
                start_date=date(2026, 4, 20),
                end_date=date(2026, 4, 20),
                tenant_id="tenant-a",
            )
        )

    assert scoped == ["DEVICE-A1"]
