from __future__ import annotations

import asyncio
import sys
from datetime import date
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

SERVICE_ROOT = Path(__file__).resolve().parents[1] / "services" / "energy-service"
SERVICES_ROOT = SERVICE_ROOT.parent
REPO_ROOT = SERVICE_ROOT.parent.parent
for existing in list(sys.path):
    try:
        existing_path = Path(existing).resolve()
    except Exception:
        continue
    if existing_path.parent == SERVICES_ROOT.resolve() and existing_path != SERVICE_ROOT.resolve():
        sys.path.remove(existing)
for module_name, module in list(sys.modules.items()):
    if module_name == "app" or module_name.startswith("app."):
        module_file = Path(getattr(module, "__file__", "") or "")
        if str(module_file) and SERVICE_ROOT.resolve() not in module_file.resolve().parents:
            sys.modules.pop(module_name, None)
for path in (REPO_ROOT, SERVICES_ROOT, SERVICE_ROOT):
    path_str = str(path)
    if path_str in sys.path:
        sys.path.remove(path_str)
    sys.path.insert(0, path_str)

from app.services.energy_engine import EnergyEngine
from app.services import energy_engine as energy_engine_module
from app.services import tariff_cache as energy_tariff_cache
from services.shared.telemetry_normalization import normalize_telemetry_sample


class _RowsResult:
    def __init__(self, rows: list[object]):
        self._rows = rows

    def scalars(self) -> "_RowsResult":
        return self

    def all(self) -> list[object]:
        return list(self._rows)


class _QueuedSession:
    def __init__(self, results: list[list[object]]):
        self._results = results

    async def execute(self, _query):  # noqa: ANN001
        if not self._results:
            raise AssertionError("Unexpected extra query")
        return _RowsResult(self._results.pop(0))


@pytest.fixture(autouse=True)
def clear_tariff_cache():
    energy_tariff_cache.tariff_cache._snapshot = {None: {"rate": 0.0, "currency": "INR", "configured": False}}
    energy_tariff_cache.tariff_cache._expires_at.clear()
    yield


def _row(**values: object) -> object:
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_energy_summary_recomputes_costs_from_tenant_tariff(monkeypatch):
    session = _QueuedSession(
        [
            [
                _row(device_id="DEV-1", energy_kwh=4.0, loss_kwh=1.0, version=2),
            ],
            [
                _row(device_id="DEV-1", energy_kwh=10.0, loss_kwh=2.0, version=3),
            ],
        ]
    )

    async def fake_allowed_ids(self, tenant_id):  # noqa: ANN001
        return {"DEV-1"}

    async def fake_tariff_get(tenant_id=None):  # noqa: ANN001
        return {"rate": 3.0, "currency": "INR", "configured": True, "source": "tenant_tariffs"}

    monkeypatch.setattr(EnergyEngine, "_get_allowed_device_ids", fake_allowed_ids)
    monkeypatch.setattr(energy_tariff_cache.tariff_cache, "get", fake_tariff_get)

    payload = await EnergyEngine(session).get_summary(tenant_id="TENANT-1")

    widgets = payload["energy_widgets"]
    assert widgets["today_energy_kwh"] == 4.0
    assert widgets["today_energy_cost_inr"] == 12.0
    assert widgets["today_loss_kwh"] == 1.0
    assert widgets["today_loss_cost_inr"] == 3.0
    assert widgets["month_energy_kwh"] == 10.0
    assert widgets["month_energy_cost_inr"] == 30.0
    assert widgets["currency"] == "INR"


@pytest.mark.asyncio
async def test_today_loss_breakdown_recomputes_costs_from_tenant_tariff(monkeypatch):
    session = _QueuedSession(
        [
            [
                _row(
                    device_id="DEV-1",
                    idle_kwh=0.5,
                    offhours_kwh=0.2,
                    overconsumption_kwh=0.3,
                    loss_kwh=1.0,
                    energy_kwh=2.0,
                    energy_cost_inr=0.25,
                    loss_cost_inr=0.25,
                    version=5,
                ),
            ]
        ]
    )

    async def fake_allowed_ids(self, tenant_id):  # noqa: ANN001
        return {"DEV-1"}

    async def fake_meta_get(device_id, tenant_id=None):  # noqa: ANN001
        return {"device_name": "COMPRESSOR"}

    async def fake_tariff_get(tenant_id=None):  # noqa: ANN001
        return {"rate": 5.0, "currency": "INR", "configured": True, "source": "tenant_tariffs"}

    monkeypatch.setattr(EnergyEngine, "_get_allowed_device_ids", fake_allowed_ids)
    monkeypatch.setattr(energy_tariff_cache.tariff_cache, "get", fake_tariff_get)
    monkeypatch.setattr(energy_engine_module.meta_cache, "get", fake_meta_get)

    payload = await EnergyEngine(session).get_today_loss_breakdown(tenant_id="TENANT-1")

    totals = payload["totals"]
    assert totals["idle_kwh"] == 0.5
    assert abs(totals["idle_cost_inr"] - 0.125) < 0.001
    assert abs(totals["off_hours_cost_inr"] - 0.05) < 0.001
    assert abs(totals["overconsumption_cost_inr"] - 0.075) < 0.001
    assert totals["total_loss_cost_inr"] == 0.25
    assert totals["today_energy_cost_inr"] == 0.25
    assert abs(payload["rows"][0]["idle_cost_inr"] - 0.125) < 0.001
    assert payload["rows"][0]["total_loss_cost_inr"] == 0.25


@pytest.mark.asyncio
async def test_compute_delta_counts_outside_shift_running_as_offhours(monkeypatch):
    engine = EnergyEngine(_QueuedSession([]))

    monkeypatch.setattr(energy_engine_module, "_get_platform_tz", lambda: ZoneInfo("Asia/Kolkata"))

    previous_sample = normalize_telemetry_sample(
        {
            "timestamp": "2026-04-01T14:29:00Z",
            "energy_kwh": 10.0,
            "current": 0.7,
            "voltage": 230.0,
            "active_power_kw": 1.0,
        },
        {},
    )
    current_sample = normalize_telemetry_sample(
        {
            "timestamp": "2026-04-01T14:30:00Z",
            "energy_kwh": 10.02,
            "current": 0.7,
            "voltage": 230.0,
            "active_power_kw": 1.0,
        },
        {},
    )

    delta = engine._compute_delta(
        ts=datetime(2026, 4, 1, 14, 30, tzinfo=timezone.utc),
        state=SimpleNamespace(version=1),
        previous_sample=previous_sample,
        current_sample=current_sample,
        idle_threshold=1.0,
        over_threshold=None,
        shifts=[{"day_of_week": 2, "shift_start": "09:00", "shift_end": "17:00", "is_active": True}],
    )

    assert delta.idle_kwh == 0.0
    assert delta.offhours_kwh > 0
    assert delta.loss_kwh == delta.offhours_kwh
    assert delta.energy_kwh == pytest.approx(0.02, abs=1e-6)


@pytest.mark.asyncio
async def test_monthly_calendar_recomputes_costs_from_tenant_tariff(monkeypatch):
    session = _QueuedSession(
        [
            [
                _row(device_id="DEV-1", day=date(2026, 3, 1), energy_kwh=1.5, energy_cost_inr=6.0, loss_kwh=0.0, loss_cost_inr=0.0, version=1),
                _row(device_id="DEV-1", day=date(2026, 3, 2), energy_kwh=2.0, energy_cost_inr=8.0, loss_kwh=0.0, loss_cost_inr=0.0, version=2),
            ]
        ]
    )

    async def fake_allowed_ids(self, tenant_id):  # noqa: ANN001
        return {"DEV-1"}

    async def fake_tariff_get(tenant_id=None):  # noqa: ANN001
        return {"rate": 4.0, "currency": "INR", "configured": True, "source": "tenant_tariffs"}

    async def fake_live_totals(self, tenant_id, plant_id=None):
        return None

    monkeypatch.setattr(EnergyEngine, "_get_allowed_device_ids", fake_allowed_ids)
    monkeypatch.setattr(energy_tariff_cache.tariff_cache, "get", fake_tariff_get)
    monkeypatch.setattr(EnergyEngine, "_fetch_live_dashboard_today_totals", fake_live_totals)

    payload = await EnergyEngine(session).get_monthly_calendar(2026, 3, tenant_id="TENANT-1")

    assert payload["summary"]["total_energy_kwh"] == 3.5
    assert payload["summary"]["total_energy_cost_inr"] == 14.0
    assert payload["days"][0]["energy_cost_inr"] == 6.0
    assert payload["days"][0]["loss_kwh"] == 0.0
    assert payload["days"][0]["loss_cost_inr"] == 0.0
    assert payload["days"][1]["energy_cost_inr"] == 8.0


@pytest.mark.asyncio
async def test_loss_breakdown_prefers_persisted_energy_cost_over_current_tariff(monkeypatch):
    session = _QueuedSession(
        [
            [
                _row(
                    device_id="DEV-1",
                    idle_kwh=0.5,
                    offhours_kwh=0.2,
                    overconsumption_kwh=0.3,
                    loss_kwh=1.0,
                    energy_kwh=2.0,
                    energy_cost_inr=10.0,
                    loss_cost_inr=3.0,
                    version=5,
                ),
            ]
        ]
    )

    async def fake_allowed_ids(self, tenant_id):
        return {"DEV-1"}

    async def fake_meta_get(device_id, tenant_id=None):
        return {"device_name": "COMPRESSOR"}

    async def fake_tariff_get(tenant_id=None):
        return {"rate": 9.0, "currency": "INR", "configured": True, "source": "tenant_tariffs"}

    monkeypatch.setattr(EnergyEngine, "_get_allowed_device_ids", fake_allowed_ids)
    monkeypatch.setattr(energy_tariff_cache.tariff_cache, "get", fake_tariff_get)
    monkeypatch.setattr(energy_engine_module.meta_cache, "get", fake_meta_get)

    payload = await EnergyEngine(session).get_today_loss_breakdown(tenant_id="TENANT-1")

    totals = payload["totals"]
    assert totals["today_energy_cost_inr"] == 10.0
    assert totals["today_energy_kwh"] == 2.0


@pytest.mark.asyncio
async def test_loss_breakdown_total_loss_cost_uses_persisted_loss_cost(monkeypatch):
    session = _QueuedSession(
        [
            [
                _row(
                    device_id="DEV-1",
                    idle_kwh=0.5,
                    offhours_kwh=0.2,
                    overconsumption_kwh=0.3,
                    loss_kwh=1.0,
                    energy_kwh=2.0,
                    energy_cost_inr=10.0,
                    loss_cost_inr=3.5,
                    version=5,
                ),
            ]
        ]
    )

    async def fake_allowed_ids(self, tenant_id):
        return {"DEV-1"}

    async def fake_meta_get(device_id, tenant_id=None):
        return {"device_name": "COMPRESSOR"}

    async def fake_tariff_get(tenant_id=None):
        return {"rate": 9.0, "currency": "INR", "configured": True, "source": "tenant_tariffs"}

    monkeypatch.setattr(EnergyEngine, "_get_allowed_device_ids", fake_allowed_ids)
    monkeypatch.setattr(energy_tariff_cache.tariff_cache, "get", fake_tariff_get)
    monkeypatch.setattr(energy_engine_module.meta_cache, "get", fake_meta_get)

    payload = await EnergyEngine(session).get_today_loss_breakdown(tenant_id="TENANT-1")

    totals = payload["totals"]
    assert totals["total_loss_cost_inr"] == 3.5
    assert payload["rows"][0]["total_loss_cost_inr"] == 3.5


@pytest.mark.asyncio
async def test_device_range_live_today_preserves_persisted_cost(monkeypatch):
    _today = datetime.now(timezone.utc).date()
    persisted_row = _row(
        device_id="DEV-1",
        day=_today,
        energy_kwh=4.0,
        energy_cost_inr=26.0,
        loss_kwh=1.0,
        loss_cost_inr=6.5,
        idle_kwh=0.5,
        offhours_kwh=0.3,
        overconsumption_kwh=0.2,
        quality_flags="[]",
        version=3,
    )
    session = _QueuedSession(
        [
            [persisted_row],
        ]
    )

    async def fake_allowed_ids(self, tenant_id):
        return {"DEV-1"}

    async def fake_tariff_get(tenant_id=None):
        return {"rate": 7.25, "currency": "INR", "configured": True, "source": "tenant_tariffs"}

    async def fake_live_today(self, device_id, tenant_id=None):
        return {"date": _today.isoformat(), "energy_kwh": 8.0, "loss_kwh": 2.0, "idle_kwh": 0.8, "offhours_kwh": 0.5, "overconsumption_kwh": 0.7}

    monkeypatch.setattr(EnergyEngine, "_get_allowed_device_ids", fake_allowed_ids)
    monkeypatch.setattr(energy_tariff_cache.tariff_cache, "get", fake_tariff_get)
    monkeypatch.setattr(EnergyEngine, "_fetch_live_current_day_totals", fake_live_today)
    monkeypatch.setattr(energy_engine_module, "_get_platform_tz", lambda: ZoneInfo("UTC"))

    payload = await EnergyEngine(session).get_device_range(
        device_id="DEV-1",
        start=_today,
        end=_today,
        tenant_id="TENANT-1",
    )

    today = payload["days"][0]
    expected_cost = 26.0 + (8.0 - 4.0) * 7.25
    assert abs(today["energy_cost_inr"] - expected_cost) < 0.01
    assert today["energy_cost_inr"] != 8.0 * 7.25


@pytest.mark.asyncio
async def test_summary_live_widget_preserves_persisted_today_cost(monkeypatch):
    session = _QueuedSession(
        [
            [
                _row(device_id="DEV-1", energy_kwh=4.0, loss_kwh=1.0, energy_cost_inr=26.0, loss_cost_inr=6.5, version=2),
            ],
            [
                _row(device_id="DEV-1", energy_kwh=10.0, loss_kwh=2.0, energy_cost_inr=65.0, loss_cost_inr=13.0, version=3),
            ],
        ]
    )

    async def fake_allowed_ids(self, tenant_id):
        return {"DEV-1"}

    async def fake_tariff_get(tenant_id=None):
        return {"rate": 7.25, "currency": "INR", "configured": True, "source": "tenant_tariffs"}

    async def fake_widgets(self, tenant_id):
        return {"today_energy_kwh": 8.0, "today_loss_kwh": 2.0, "month_energy_kwh": 14.0}

    monkeypatch.setattr(EnergyEngine, "_get_allowed_device_ids", fake_allowed_ids)
    monkeypatch.setattr(energy_tariff_cache.tariff_cache, "get", fake_tariff_get)
    monkeypatch.setattr(EnergyEngine, "_fetch_live_dashboard_energy_widgets", fake_widgets)

    payload = await EnergyEngine(session).get_summary(tenant_id="TENANT-1")

    widgets = payload["energy_widgets"]
    expected_cost = 26.0 + (8.0 - 4.0) * 7.25
    assert abs(widgets["today_energy_cost_inr"] - expected_cost) < 0.01
    assert widgets["today_energy_cost_inr"] != 8.0 * 7.25


@pytest.mark.asyncio
async def test_monthly_calendar_live_today_preserves_persisted_cost(monkeypatch):
    _today = datetime.now(timezone.utc).date()
    session = _QueuedSession(
        [
            [
                _row(day=_today, energy_kwh=4.0, energy_cost_inr=20.0, loss_kwh=0.0, loss_cost_inr=0.0, version=2),
            ],
        ]
    )

    async def fake_tariff_get(tenant_id=None):
        return {"rate": 10.0, "currency": "INR", "configured": True, "source": "tenant_tariffs"}

    async def fake_live_totals(self, tenant_id, plant_id=None):
        return {"today_energy_kwh": 7.0, "today_loss_kwh": 1.0}

    monkeypatch.setattr(energy_tariff_cache.tariff_cache, "get", fake_tariff_get)
    monkeypatch.setattr(EnergyEngine, "_fetch_live_dashboard_today_totals", fake_live_totals)
    monkeypatch.setattr(energy_engine_module, "_get_platform_tz", lambda: ZoneInfo("UTC"))

    payload = await EnergyEngine(session).get_monthly_calendar(_today.year, _today.month, tenant_id="TENANT-1")

    today_day = next(d for d in payload["days"] if d["date"] == _today.isoformat())
    expected_cost = 20.0 + (7.0 - 4.0) * 10.0
    assert abs(today_day["energy_cost_inr"] - expected_cost) < 0.01
    assert today_day["energy_cost_inr"] != 7.0 * 10.0


@pytest.mark.asyncio
async def test_loss_breakdown_bucket_costs_sum_to_total_loss_cost(monkeypatch):
    session = _QueuedSession(
        [
            [
                _row(
                    device_id="DEV-1",
                    idle_kwh=0.6,
                    offhours_kwh=0.3,
                    overconsumption_kwh=0.1,
                    loss_kwh=1.0,
                    energy_kwh=5.0,
                    energy_cost_inr=25.0,
                    loss_cost_inr=7.0,
                    version=5,
                ),
            ]
        ]
    )

    async def fake_allowed_ids(self, tenant_id):
        return {"DEV-1"}

    async def fake_meta_get(device_id, tenant_id=None):
        return {"device_name": "COMPRESSOR"}

    async def fake_tariff_get(tenant_id=None):
        return {"rate": 10.0, "currency": "INR", "configured": True, "source": "tenant_tariffs"}

    monkeypatch.setattr(EnergyEngine, "_get_allowed_device_ids", fake_allowed_ids)
    monkeypatch.setattr(energy_tariff_cache.tariff_cache, "get", fake_tariff_get)
    monkeypatch.setattr(energy_engine_module.meta_cache, "get", fake_meta_get)

    payload = await EnergyEngine(session).get_today_loss_breakdown(tenant_id="TENANT-1")

    totals = payload["totals"]
    assert totals["total_loss_cost_inr"] == 7.0
    bucket_sum = totals["idle_cost_inr"] + totals["off_hours_cost_inr"] + totals["overconsumption_cost_inr"]
    assert abs(bucket_sum - totals["total_loss_cost_inr"]) < 0.001

    row = payload["rows"][0]
    row_bucket_sum = row["idle_cost_inr"] + row["off_hours_cost_inr"] + row["overconsumption_cost_inr"]
    assert abs(row_bucket_sum - row["total_loss_cost_inr"]) < 0.001


@pytest.mark.asyncio
async def test_device_range_live_today_energy_kwh_uses_persisted_when_live_lags(monkeypatch):
    _today = datetime.now(timezone.utc).date()
    persisted_row = _row(
        device_id="DEV-1",
        day=_today,
        energy_kwh=192.0,
        energy_cost_inr=1920.0,
        loss_kwh=20.0,
        loss_cost_inr=200.0,
        idle_kwh=5.0,
        offhours_kwh=10.0,
        overconsumption_kwh=5.0,
        quality_flags="[]",
        version=3,
    )
    session = _QueuedSession([[persisted_row]])

    async def fake_allowed_ids(self, tenant_id):
        return {"DEV-1"}

    async def fake_tariff_get(tenant_id=None):
        return {"rate": 10.0, "currency": "INR", "configured": True}

    async def fake_live_today(self, device_id, tenant_id=None):
        return {"date": _today.isoformat(), "energy_kwh": 170.0, "loss_kwh": 15.0, "idle_kwh": 3.0, "offhours_kwh": 8.0, "overconsumption_kwh": 4.0}

    monkeypatch.setattr(EnergyEngine, "_get_allowed_device_ids", fake_allowed_ids)
    monkeypatch.setattr(energy_tariff_cache.tariff_cache, "get", fake_tariff_get)
    monkeypatch.setattr(EnergyEngine, "_fetch_live_current_day_totals", fake_live_today)
    monkeypatch.setattr(energy_engine_module, "_get_platform_tz", lambda: ZoneInfo("UTC"))

    payload = await EnergyEngine(session).get_device_range(
        device_id="DEV-1",
        start=_today,
        end=_today,
        tenant_id="TENANT-1",
    )

    today = payload["days"][0]
    assert today["energy_kwh"] == 192.0
    assert today["loss_kwh"] == 20.0


@pytest.mark.asyncio
async def test_device_range_live_today_energy_kwh_uses_live_when_live_ahead(monkeypatch):
    _today = datetime.now(timezone.utc).date()
    persisted_row = _row(
        device_id="DEV-1",
        day=_today,
        energy_kwh=180.0,
        energy_cost_inr=1800.0,
        loss_kwh=18.0,
        loss_cost_inr=180.0,
        idle_kwh=5.0,
        offhours_kwh=8.0,
        overconsumption_kwh=5.0,
        quality_flags="[]",
        version=3,
    )
    session = _QueuedSession([[persisted_row]])

    async def fake_allowed_ids(self, tenant_id):
        return {"DEV-1"}

    async def fake_tariff_get(tenant_id=None):
        return {"rate": 10.0, "currency": "INR", "configured": True}

    async def fake_live_today(self, device_id, tenant_id=None):
        return {"date": _today.isoformat(), "energy_kwh": 192.0, "loss_kwh": 22.0, "idle_kwh": 6.0, "offhours_kwh": 10.0, "overconsumption_kwh": 6.0}

    monkeypatch.setattr(EnergyEngine, "_get_allowed_device_ids", fake_allowed_ids)
    monkeypatch.setattr(energy_tariff_cache.tariff_cache, "get", fake_tariff_get)
    monkeypatch.setattr(EnergyEngine, "_fetch_live_current_day_totals", fake_live_today)
    monkeypatch.setattr(energy_engine_module, "_get_platform_tz", lambda: ZoneInfo("UTC"))

    payload = await EnergyEngine(session).get_device_range(
        device_id="DEV-1",
        start=_today,
        end=_today,
        tenant_id="TENANT-1",
    )

    today = payload["days"][0]
    assert today["energy_kwh"] == 192.0
    assert today["loss_kwh"] == 22.0


@pytest.mark.asyncio
async def test_device_range_live_today_energy_kwh_uses_live_when_no_persisted_row(monkeypatch):
    _today = datetime.now(timezone.utc).date()
    session = _QueuedSession([[]])

    async def fake_allowed_ids(self, tenant_id):
        return {"DEV-1"}

    async def fake_tariff_get(tenant_id=None):
        return {"rate": 10.0, "currency": "INR", "configured": True}

    async def fake_live_today(self, device_id, tenant_id=None):
        return {"date": _today.isoformat(), "energy_kwh": 50.0, "loss_kwh": 5.0, "idle_kwh": 1.0, "offhours_kwh": 2.0, "overconsumption_kwh": 2.0}

    monkeypatch.setattr(EnergyEngine, "_get_allowed_device_ids", fake_allowed_ids)
    monkeypatch.setattr(energy_tariff_cache.tariff_cache, "get", fake_tariff_get)
    monkeypatch.setattr(EnergyEngine, "_fetch_live_current_day_totals", fake_live_today)
    monkeypatch.setattr(energy_engine_module, "_get_platform_tz", lambda: ZoneInfo("UTC"))

    payload = await EnergyEngine(session).get_device_range(
        device_id="DEV-1",
        start=_today,
        end=_today,
        tenant_id="TENANT-1",
    )

    today = payload["days"][0]
    assert today["energy_kwh"] == 50.0
    assert today["loss_kwh"] == 5.0


@pytest.mark.asyncio
async def test_device_range_live_today_loss_kwh_uses_persisted_when_live_lags(monkeypatch):
    _today = datetime.now(timezone.utc).date()
    persisted_row = _row(
        device_id="DEV-1",
        day=_today,
        energy_kwh=100.0,
        energy_cost_inr=1000.0,
        loss_kwh=30.0,
        loss_cost_inr=300.0,
        idle_kwh=10.0,
        offhours_kwh=12.0,
        overconsumption_kwh=8.0,
        quality_flags="[]",
        version=3,
    )
    session = _QueuedSession([[persisted_row]])

    async def fake_allowed_ids(self, tenant_id):
        return {"DEV-1"}

    async def fake_tariff_get(tenant_id=None):
        return {"rate": 10.0, "currency": "INR", "configured": True}

    async def fake_live_today(self, device_id, tenant_id=None):
        return {"date": _today.isoformat(), "energy_kwh": 105.0, "loss_kwh": 25.0, "idle_kwh": 8.0, "offhours_kwh": 10.0, "overconsumption_kwh": 7.0}

    monkeypatch.setattr(EnergyEngine, "_get_allowed_device_ids", fake_allowed_ids)
    monkeypatch.setattr(energy_tariff_cache.tariff_cache, "get", fake_tariff_get)
    monkeypatch.setattr(EnergyEngine, "_fetch_live_current_day_totals", fake_live_today)
    monkeypatch.setattr(energy_engine_module, "_get_platform_tz", lambda: ZoneInfo("UTC"))

    payload = await EnergyEngine(session).get_device_range(
        device_id="DEV-1",
        start=_today,
        end=_today,
        tenant_id="TENANT-1",
    )

    today = payload["days"][0]
    assert today["energy_kwh"] == 105.0
    assert today["loss_kwh"] == 30.0


@pytest.mark.asyncio
async def test_device_range_category_kwh_uses_persisted_when_live_lags(monkeypatch):
    _today = datetime.now(timezone.utc).date()
    persisted_row = _row(
        device_id="DEV-1",
        day=_today,
        energy_kwh=100.0,
        energy_cost_inr=1000.0,
        loss_kwh=30.0,
        loss_cost_inr=300.0,
        idle_kwh=10.0,
        offhours_kwh=12.0,
        overconsumption_kwh=8.0,
        quality_flags="[]",
        version=3,
    )
    session = _QueuedSession([[persisted_row]])

    async def fake_allowed_ids(self, tenant_id):
        return {"DEV-1"}

    async def fake_tariff_get(tenant_id=None):
        return {"rate": 10.0, "currency": "INR", "configured": True}

    async def fake_live_today(self, device_id, tenant_id=None):
        return {"date": _today.isoformat(), "energy_kwh": 105.0, "loss_kwh": 25.0, "idle_kwh": 8.0, "offhours_kwh": 10.0, "overconsumption_kwh": 7.0}

    monkeypatch.setattr(EnergyEngine, "_get_allowed_device_ids", fake_allowed_ids)
    monkeypatch.setattr(energy_tariff_cache.tariff_cache, "get", fake_tariff_get)
    monkeypatch.setattr(EnergyEngine, "_fetch_live_current_day_totals", fake_live_today)
    monkeypatch.setattr(energy_engine_module, "_get_platform_tz", lambda: ZoneInfo("UTC"))

    payload = await EnergyEngine(session).get_device_range(
        device_id="DEV-1",
        start=_today,
        end=_today,
        tenant_id="TENANT-1",
    )

    today = payload["days"][0]
    assert today["idle_kwh"] == 10.0
    assert today["offhours_kwh"] == 12.0
    assert today["overconsumption_kwh"] == 8.0


@pytest.mark.asyncio
async def test_device_range_category_kwh_uses_live_delta_when_live_ahead(monkeypatch):
    _today = datetime.now(timezone.utc).date()
    persisted_row = _row(
        device_id="DEV-1",
        day=_today,
        energy_kwh=100.0,
        energy_cost_inr=1000.0,
        loss_kwh=20.0,
        loss_cost_inr=200.0,
        idle_kwh=5.0,
        offhours_kwh=8.0,
        overconsumption_kwh=7.0,
        quality_flags="[]",
        version=3,
    )
    session = _QueuedSession([[persisted_row]])

    async def fake_allowed_ids(self, tenant_id):
        return {"DEV-1"}

    async def fake_tariff_get(tenant_id=None):
        return {"rate": 10.0, "currency": "INR", "configured": True}

    async def fake_live_today(self, device_id, tenant_id=None):
        return {"date": _today.isoformat(), "energy_kwh": 110.0, "loss_kwh": 25.0, "idle_kwh": 7.0, "offhours_kwh": 10.0, "overconsumption_kwh": 8.0}

    monkeypatch.setattr(EnergyEngine, "_get_allowed_device_ids", fake_allowed_ids)
    monkeypatch.setattr(energy_tariff_cache.tariff_cache, "get", fake_tariff_get)
    monkeypatch.setattr(EnergyEngine, "_fetch_live_current_day_totals", fake_live_today)
    monkeypatch.setattr(energy_engine_module, "_get_platform_tz", lambda: ZoneInfo("UTC"))

    payload = await EnergyEngine(session).get_device_range(
        device_id="DEV-1",
        start=_today,
        end=_today,
        tenant_id="TENANT-1",
    )

    today = payload["days"][0]
    assert today["idle_kwh"] == round(5.0 + max(0.0, 7.0 - 5.0), 4)
    assert today["offhours_kwh"] == round(8.0 + max(0.0, 10.0 - 8.0), 4)
    assert today["overconsumption_kwh"] == round(7.0 + max(0.0, 8.0 - 7.0), 4)


@pytest.mark.asyncio
async def test_energy_summary_today_loss_does_not_exceed_today_energy(monkeypatch):
    session = _QueuedSession(
        [
            [
                _row(device_id="DEV-1", energy_kwh=4.0, loss_kwh=1.0, energy_cost_inr=12.0, loss_cost_inr=3.0, version=2),
            ],
            [
                _row(device_id="DEV-1", energy_kwh=10.0, loss_kwh=2.0, energy_cost_inr=30.0, loss_cost_inr=6.0, version=3),
            ],
        ]
    )

    async def fake_allowed_ids(self, tenant_id):
        return {"DEV-1"}

    async def fake_tariff_get(tenant_id=None):
        return {"rate": 3.0, "currency": "INR", "configured": True, "source": "tenant_tariffs"}

    async def fake_live_totals(self, tenant_id, plant_id=None):
        return None

    monkeypatch.setattr(EnergyEngine, "_get_allowed_device_ids", fake_allowed_ids)
    monkeypatch.setattr(energy_tariff_cache.tariff_cache, "get", fake_tariff_get)
    monkeypatch.setattr(EnergyEngine, "_fetch_live_dashboard_today_totals", fake_live_totals)

    payload = await EnergyEngine(session).get_summary(tenant_id="TENANT-1")

    widgets = payload["energy_widgets"]
    assert widgets["today_loss_kwh"] <= widgets["today_energy_kwh"]
    assert widgets["today_loss_kwh"] <= widgets["month_energy_kwh"]

    session2 = _QueuedSession(
        [
            [
                _row(device_id="DEV-1", energy_kwh=2.0, loss_kwh=2.0, energy_cost_inr=6.0, loss_cost_inr=6.0, version=2),
            ],
            [
                _row(device_id="DEV-1", energy_kwh=8.0, loss_kwh=8.0, energy_cost_inr=24.0, loss_cost_inr=24.0, version=3),
            ],
        ]
    )

    monkeypatch.setattr(EnergyEngine, "_get_allowed_device_ids", fake_allowed_ids)
    monkeypatch.setattr(energy_tariff_cache.tariff_cache, "get", fake_tariff_get)
    monkeypatch.setattr(EnergyEngine, "_fetch_live_dashboard_today_totals", fake_live_totals)

    payload2 = await EnergyEngine(session2).get_summary(tenant_id="TENANT-1")

    widgets2 = payload2["energy_widgets"]
    assert widgets2["today_loss_kwh"] == widgets2["today_energy_kwh"]
