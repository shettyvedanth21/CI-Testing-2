from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[3]
SERVICES_ROOT = ROOT / "services"
ENERGY_ROOT = SERVICES_ROOT / "energy-service"
sys.path = [p for p in sys.path if p not in {str(ENERGY_ROOT), str(SERVICES_ROOT), str(ROOT)}]
sys.path.insert(0, str(ENERGY_ROOT))
sys.path.insert(1, str(SERVICES_ROOT))
sys.path.insert(2, str(ROOT))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from app.services.energy_engine import EnergyEngine
from app.services import energy_engine as energy_engine_module
from app.services import tariff_cache as energy_tariff_cache


class _ScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _QueuedSession:
    def __init__(self, queue):
        self._queue = list(queue)

    async def execute(self, _query):
        if self._queue:
            return _ScalarResult(self._queue.pop(0))
        return _ScalarResult([])


def _fleet_row(*, day, energy_kwh=0.0, energy_cost_inr=0.0, loss_kwh=0.0, loss_cost_inr=0.0, version=0):
    return SimpleNamespace(
        day=day,
        energy_kwh=energy_kwh,
        energy_cost_inr=energy_cost_inr,
        loss_kwh=loss_kwh,
        loss_cost_inr=loss_cost_inr,
        version=version,
    )


def _device_row(*, device_id="DEV-1", day, energy_kwh=0.0, energy_cost_inr=0.0, loss_kwh=0.0, loss_cost_inr=0.0, idle_kwh=0.0, offhours_kwh=0.0, overconsumption_kwh=0.0, version=0):
    return SimpleNamespace(
        device_id=device_id,
        day=day,
        energy_kwh=energy_kwh,
        energy_cost_inr=energy_cost_inr,
        loss_kwh=loss_kwh,
        loss_cost_inr=loss_cost_inr,
        idle_kwh=idle_kwh,
        offhours_kwh=offhours_kwh,
        overconsumption_kwh=overconsumption_kwh,
        version=version,
    )


@pytest.mark.asyncio
async def test_calendar_today_energy_uses_persisted_plus_delta_not_raw_live(monkeypatch):
    _today = datetime.now(timezone.utc).date()
    persisted_row = _fleet_row(day=_today, energy_kwh=100.0, energy_cost_inr=1000.0, loss_kwh=20.0, loss_cost_inr=200.0, version=3)
    session = _QueuedSession([[persisted_row]])

    async def fake_allowed_ids(self, tenant_id):
        return set()

    async def fake_tariff_get(tenant_id=None):
        return {"rate": 10.0, "currency": "INR", "configured": True}

    async def fake_live_totals(self, tenant_id, plant_id=None):
        return {"today_energy_kwh": 95.0, "today_loss_kwh": 15.0}

    monkeypatch.setattr(EnergyEngine, "_get_allowed_device_ids", fake_allowed_ids)
    monkeypatch.setattr(energy_tariff_cache.tariff_cache, "get", fake_tariff_get)
    monkeypatch.setattr(EnergyEngine, "_fetch_live_dashboard_today_totals", fake_live_totals)
    monkeypatch.setattr(energy_engine_module, "_get_platform_tz", lambda: ZoneInfo("UTC"))

    payload = await EnergyEngine(session).get_monthly_calendar(
        year=_today.year,
        month=_today.month,
        tenant_id="TENANT-1",
    )

    today_day = next(d for d in payload["days"] if d["date"] == _today.isoformat())
    assert today_day["energy_kwh"] == 100.0


@pytest.mark.asyncio
async def test_calendar_today_loss_uses_persisted_plus_delta(monkeypatch):
    _today = datetime.now(timezone.utc).date()
    persisted_row = _fleet_row(day=_today, energy_kwh=100.0, energy_cost_inr=1000.0, loss_kwh=20.0, loss_cost_inr=200.0, version=3)
    session = _QueuedSession([[persisted_row]])

    async def fake_allowed_ids(self, tenant_id):
        return set()

    async def fake_tariff_get(tenant_id=None):
        return {"rate": 10.0, "currency": "INR", "configured": True}

    async def fake_live_totals(self, tenant_id, plant_id=None):
        return {"today_energy_kwh": 95.0, "today_loss_kwh": 25.0}

    monkeypatch.setattr(EnergyEngine, "_get_allowed_device_ids", fake_allowed_ids)
    monkeypatch.setattr(energy_tariff_cache.tariff_cache, "get", fake_tariff_get)
    monkeypatch.setattr(EnergyEngine, "_fetch_live_dashboard_today_totals", fake_live_totals)
    monkeypatch.setattr(energy_engine_module, "_get_platform_tz", lambda: ZoneInfo("UTC"))

    payload = await EnergyEngine(session).get_monthly_calendar(
        year=_today.year,
        month=_today.month,
        tenant_id="TENANT-1",
    )

    today_day = next(d for d in payload["days"] if d["date"] == _today.isoformat())
    assert today_day["loss_kwh"] == 25.0
    assert today_day["loss_cost_inr"] == 200.0 + (5.0 * 10.0)


@pytest.mark.asyncio
async def test_calendar_includes_loss_fields_in_days_and_summary(monkeypatch):
    _today = datetime.now(timezone.utc).date()
    persisted_row = _fleet_row(day=_today, energy_kwh=100.0, energy_cost_inr=1000.0, loss_kwh=20.0, loss_cost_inr=200.0, version=1)
    session = _QueuedSession([[persisted_row]])

    async def fake_allowed_ids(self, tenant_id):
        return set()

    async def fake_tariff_get(tenant_id=None):
        return {"rate": 10.0, "currency": "INR", "configured": True}

    async def fake_live_totals(self, tenant_id, plant_id=None):
        return None

    monkeypatch.setattr(EnergyEngine, "_get_allowed_device_ids", fake_allowed_ids)
    monkeypatch.setattr(energy_tariff_cache.tariff_cache, "get", fake_tariff_get)
    monkeypatch.setattr(EnergyEngine, "_fetch_live_dashboard_today_totals", fake_live_totals)
    monkeypatch.setattr(energy_engine_module, "_get_platform_tz", lambda: ZoneInfo("UTC"))

    payload = await EnergyEngine(session).get_monthly_calendar(
        year=_today.year,
        month=_today.month,
        tenant_id="TENANT-1",
    )

    today_day = next(d for d in payload["days"] if d["date"] == _today.isoformat())
    assert "loss_kwh" in today_day
    assert "loss_cost_inr" in today_day
    assert today_day["loss_kwh"] == 20.0
    assert today_day["loss_cost_inr"] == 200.0
    assert payload["summary"]["total_loss_kwh"] == 20.0
    assert payload["summary"]["total_loss_cost_inr"] == 200.0


@pytest.mark.asyncio
async def test_calendar_zero_loss_propagates_not_fallback(monkeypatch):
    _today = datetime.now(timezone.utc).date()
    persisted_row = _fleet_row(day=_today, energy_kwh=50.0, energy_cost_inr=500.0, loss_kwh=0.0, loss_cost_inr=0.0, version=1)
    session = _QueuedSession([[persisted_row]])

    async def fake_allowed_ids(self, tenant_id):
        return set()

    async def fake_tariff_get(tenant_id=None):
        return {"rate": 10.0, "currency": "INR", "configured": True}

    async def fake_live_totals(self, tenant_id, plant_id=None):
        return {"today_energy_kwh": 55.0, "today_loss_kwh": 5.0}

    monkeypatch.setattr(EnergyEngine, "_get_allowed_device_ids", fake_allowed_ids)
    monkeypatch.setattr(energy_tariff_cache.tariff_cache, "get", fake_tariff_get)
    monkeypatch.setattr(EnergyEngine, "_fetch_live_dashboard_today_totals", fake_live_totals)
    monkeypatch.setattr(energy_engine_module, "_get_platform_tz", lambda: ZoneInfo("UTC"))

    payload = await EnergyEngine(session).get_monthly_calendar(
        year=_today.year,
        month=_today.month,
        tenant_id="TENANT-1",
    )

    today_day = next(d for d in payload["days"] if d["date"] == _today.isoformat())
    assert today_day["loss_kwh"] == 5.0


@pytest.mark.asyncio
async def test_calendar_with_device_ids_queries_energy_device_day(monkeypatch):
    _today = datetime.now(timezone.utc).date()
    row1 = _device_row(device_id="D1", day=_today, energy_kwh=50.0, energy_cost_inr=500.0, loss_kwh=10.0, loss_cost_inr=100.0)
    row2 = _device_row(device_id="D2", day=_today, energy_kwh=30.0, energy_cost_inr=300.0, loss_kwh=5.0, loss_cost_inr=50.0)
    session = _QueuedSession([[row1, row2]])

    async def fake_allowed_ids(self, tenant_id):
        return set()

    async def fake_tariff_get(tenant_id=None):
        return {"rate": 10.0, "currency": "INR", "configured": True}

    async def fake_live_totals(self, tenant_id, plant_id=None):
        return {"today_energy_kwh": 85.0, "today_loss_kwh": 17.0}

    monkeypatch.setattr(EnergyEngine, "_get_allowed_device_ids", fake_allowed_ids)
    monkeypatch.setattr(energy_tariff_cache.tariff_cache, "get", fake_tariff_get)
    monkeypatch.setattr(EnergyEngine, "_fetch_live_dashboard_today_totals", fake_live_totals)
    monkeypatch.setattr(energy_engine_module, "_get_platform_tz", lambda: ZoneInfo("UTC"))

    payload = await EnergyEngine(session).get_monthly_calendar(
        year=_today.year,
        month=_today.month,
        tenant_id="TENANT-1",
        device_ids=["D1", "D2"],
        plant_id="PLANT-1",
    )

    today_day = next(d for d in payload["days"] if d["date"] == _today.isoformat())
    assert today_day["energy_kwh"] == 85.0
    assert today_day["loss_kwh"] == 17.0


@pytest.mark.asyncio
async def test_calendar_with_device_ids_today_uses_persisted_plus_delta(monkeypatch):
    _today = datetime.now(timezone.utc).date()
    row1 = _device_row(device_id="D1", day=_today, energy_kwh=50.0, energy_cost_inr=500.0, loss_kwh=10.0, loss_cost_inr=100.0)
    session = _QueuedSession([[row1]])

    async def fake_allowed_ids(self, tenant_id):
        return set()

    async def fake_tariff_get(tenant_id=None):
        return {"rate": 10.0, "currency": "INR", "configured": True}

    async def fake_live_totals(self, tenant_id, plant_id=None):
        return {"today_energy_kwh": 45.0, "today_loss_kwh": 8.0}

    monkeypatch.setattr(EnergyEngine, "_get_allowed_device_ids", fake_allowed_ids)
    monkeypatch.setattr(energy_tariff_cache.tariff_cache, "get", fake_tariff_get)
    monkeypatch.setattr(EnergyEngine, "_fetch_live_dashboard_today_totals", fake_live_totals)
    monkeypatch.setattr(energy_engine_module, "_get_platform_tz", lambda: ZoneInfo("UTC"))

    payload = await EnergyEngine(session).get_monthly_calendar(
        year=_today.year,
        month=_today.month,
        tenant_id="TENANT-1",
        device_ids=["D1"],
    )

    today_day = next(d for d in payload["days"] if d["date"] == _today.isoformat())
    assert today_day["energy_kwh"] == 50.0
    assert today_day["loss_kwh"] == 10.0


@pytest.mark.asyncio
async def test_calendar_with_device_ids_includes_loss_fields(monkeypatch):
    _today = datetime.now(timezone.utc).date()
    row1 = _device_row(device_id="D1", day=_today, energy_kwh=50.0, energy_cost_inr=500.0, loss_kwh=10.0, loss_cost_inr=100.0)
    session = _QueuedSession([[row1]])

    async def fake_allowed_ids(self, tenant_id):
        return set()

    async def fake_tariff_get(tenant_id=None):
        return {"rate": 10.0, "currency": "INR", "configured": True}

    async def fake_live_totals(self, tenant_id, plant_id=None):
        return None

    monkeypatch.setattr(EnergyEngine, "_get_allowed_device_ids", fake_allowed_ids)
    monkeypatch.setattr(energy_tariff_cache.tariff_cache, "get", fake_tariff_get)
    monkeypatch.setattr(EnergyEngine, "_fetch_live_dashboard_today_totals", fake_live_totals)
    monkeypatch.setattr(energy_engine_module, "_get_platform_tz", lambda: ZoneInfo("UTC"))

    payload = await EnergyEngine(session).get_monthly_calendar(
        year=_today.year,
        month=_today.month,
        tenant_id="TENANT-1",
        device_ids=["D1"],
    )

    today_day = next(d for d in payload["days"] if d["date"] == _today.isoformat())
    assert "loss_kwh" in today_day
    assert "loss_cost_inr" in today_day
    assert today_day["loss_kwh"] == 10.0
    assert payload["summary"]["total_loss_kwh"] == 10.0


@pytest.mark.asyncio
async def test_calendar_without_device_ids_queries_energy_fleet_day(monkeypatch):
    _today = datetime.now(timezone.utc).date()
    persisted_row = _fleet_row(day=_today, energy_kwh=200.0, energy_cost_inr=2000.0, loss_kwh=40.0, loss_cost_inr=400.0, version=5)
    session = _QueuedSession([[persisted_row]])

    async def fake_allowed_ids(self, tenant_id):
        return set()

    async def fake_tariff_get(tenant_id=None):
        return {"rate": 10.0, "currency": "INR", "configured": True}

    async def fake_live_totals(self, tenant_id, plant_id=None):
        return {"today_energy_kwh": 210.0, "today_loss_kwh": 45.0}

    monkeypatch.setattr(EnergyEngine, "_get_allowed_device_ids", fake_allowed_ids)
    monkeypatch.setattr(energy_tariff_cache.tariff_cache, "get", fake_tariff_get)
    monkeypatch.setattr(EnergyEngine, "_fetch_live_dashboard_today_totals", fake_live_totals)
    monkeypatch.setattr(energy_engine_module, "_get_platform_tz", lambda: ZoneInfo("UTC"))

    payload = await EnergyEngine(session).get_monthly_calendar(
        year=_today.year,
        month=_today.month,
        tenant_id="TENANT-1",
    )

    today_day = next(d for d in payload["days"] if d["date"] == _today.isoformat())
    assert today_day["energy_kwh"] == 210.0
    assert today_day["loss_kwh"] == 45.0
