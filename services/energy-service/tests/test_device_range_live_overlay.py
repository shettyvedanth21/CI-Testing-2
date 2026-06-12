from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVICES_ROOT = ROOT.parent
sys.path = [p for p in sys.path if p not in {str(ROOT), str(SERVICES_ROOT)}]
sys.path.insert(0, str(SERVICES_ROOT))
sys.path.insert(0, str(ROOT))

from app.services.energy_engine import EnergyEngine
from services.shared.telemetry_normalization import NormalizedTelemetrySample, normalize_telemetry_sample


class _ScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _SessionStub:
    def __init__(self, rows):
        self.rows = rows

    async def execute(self, _query):
        return _ScalarResult(self.rows)


def _normalized_sample(*, ts: str, current: float, voltage: float, energy_kwh: float) -> NormalizedTelemetrySample:
    from datetime import datetime, timezone

    return NormalizedTelemetrySample(
        timestamp=datetime.fromisoformat(ts).replace(tzinfo=timezone.utc),
        raw_power_w=None,
        raw_active_power_w=None,
        raw_power_factor=None,
        raw_current_a=current,
        raw_voltage_v=voltage,
        raw_energy_kwh=energy_kwh,
        raw_source_power_field=None,
        raw_source_pf_field=None,
        raw_source_energy_field="energy_kwh",
        net_power_w=None,
        import_power_w=0.0,
        export_power_w=0.0,
        business_power_w=0.0,
        pf_signed=None,
        pf_business=None,
        current_a=current,
        voltage_v=voltage,
        energy_counter_kwh=energy_kwh,
        power_direction="unknown",
        quality_flags=(),
    )


def test_compute_delta_treats_overconsumption_as_full_interval_loss():
    engine = EnergyEngine(_SessionStub([]))

    delta = engine._compute_delta(
        state=SimpleNamespace(),
        ts=_normalized_sample(ts="2026-04-14T00:01:00", current=25.0, voltage=230.0, energy_kwh=100.2).timestamp,
        previous_sample=_normalized_sample(ts="2026-04-14T00:00:00", current=25.0, voltage=230.0, energy_kwh=100.0),
        current_sample=_normalized_sample(ts="2026-04-14T00:01:00", current=25.0, voltage=230.0, energy_kwh=100.2),
        idle_threshold=5.0,
        over_threshold=20.0,
        shifts=[{"shift_start": "00:00", "shift_end": "23:59", "is_active": True}],
    )

    assert delta.energy_kwh == pytest.approx(0.2)
    assert delta.idle_kwh == 0.0
    assert delta.offhours_kwh == 0.0
    assert delta.overconsumption_kwh == pytest.approx(0.2)
    assert delta.loss_kwh == pytest.approx(0.2)
    assert delta.energy_method == "counter"
    assert delta.quality_class == "counter_only"
    assert delta.reason_code == "counter_accepted"


def test_compute_delta_rejects_implausible_counter_jump_and_uses_power_fallback():
    engine = EnergyEngine(_SessionStub([]))
    previous = normalize_telemetry_sample(
        {"timestamp": "2026-04-14T00:00:00+00:00", "power": 8744.0, "energy_kwh": 0.0},
        {},
    )
    current = normalize_telemetry_sample(
        {"timestamp": "2026-04-14T00:00:20+00:00", "power": 8744.0, "energy_kwh": 8.9},
        {},
    )

    delta = engine._compute_delta(
        state=SimpleNamespace(),
        ts=current.timestamp,
        previous_sample=previous,
        current_sample=current,
        idle_threshold=5.0,
        over_threshold=20.0,
        shifts=[{"shift_start": "00:00", "shift_end": "23:59", "is_active": True}],
    )

    assert delta.energy_kwh == pytest.approx(8744.0 * 20.0 / 3600.0 / 1000.0, abs=1e-6)
    assert delta.energy_method == "power_integration"
    assert delta.quality_class == "estimated"
    assert delta.reason_code == "fallback_measured_power"
    assert "counter_implausible_vs_power" in delta.flags


def test_compute_delta_rejects_long_gap_consistently():
    engine = EnergyEngine(_SessionStub([]))
    previous = normalize_telemetry_sample(
        {"timestamp": "2026-04-14T00:00:00+00:00", "power": 1000.0, "energy_kwh": 1.0},
        {},
    )
    current = normalize_telemetry_sample(
        {"timestamp": "2026-04-14T01:00:00+00:00", "power": 1000.0, "energy_kwh": 2.0},
        {},
    )

    delta = engine._compute_delta(
        state=SimpleNamespace(),
        ts=current.timestamp,
        previous_sample=previous,
        current_sample=current,
        idle_threshold=5.0,
        over_threshold=20.0,
        shifts=[{"shift_start": "00:00", "shift_end": "23:59", "is_active": True}],
    )

    assert delta.energy_kwh == 0.0
    assert delta.loss_kwh == 0.0
    assert delta.energy_method == "none"
    assert delta.quality_class == "gap_exceeded"
    assert delta.reason_code == "fallback_gap_exceeded"


def test_compute_delta_signed_power_inverted_polarity_uses_fallback_without_inflation():
    engine = EnergyEngine(_SessionStub([]))
    config = {"energy_flow_mode": "consumption_only", "polarity_mode": "inverted"}
    previous = normalize_telemetry_sample(
        {
            "timestamp": "2026-04-14T00:00:00+00:00",
            "power": -1500.0,
            "power_factor": -0.9,
            "energy_kwh": 2.0,
        },
        config,
    )
    current = normalize_telemetry_sample(
        {
            "timestamp": "2026-04-14T00:05:00+00:00",
            "power": -1500.0,
            "power_factor": -0.9,
            "energy_kwh": 2.25,
        },
        config,
    )

    delta = engine._compute_delta(
        state=SimpleNamespace(),
        ts=current.timestamp,
        previous_sample=previous,
        current_sample=current,
        idle_threshold=5.0,
        over_threshold=20.0,
        shifts=[{"shift_start": "00:00", "shift_end": "23:59", "is_active": True}],
    )

    assert delta.energy_kwh == pytest.approx(0.125)
    assert delta.energy_method == "power_integration"
    assert delta.reason_code == "fallback_measured_power"


@pytest.mark.asyncio
async def test_get_device_range_overlays_current_day_with_live_state(monkeypatch):
    engine = EnergyEngine(_SessionStub([
        SimpleNamespace(
            day=date(2026, 4, 5),
            energy_kwh=0.1661,
            loss_kwh=0.1661,
            idle_kwh=0.0,
            offhours_kwh=0.1661,
            overconsumption_kwh=0.0,
            quality_flags='["counter_missing","fallback_integration"]',
            version=11,
        )
    ]))

    async def fake_allowed(_tenant_id):
        return None

    async def fake_tariff(_tenant_id):
        return {"rate": 0.0, "currency": "INR"}

    async def fake_live(_device_id, _tenant_id):
        return {
            "date": "2026-04-05",
            "energy_kwh": 0.2095,
            "loss_kwh": 0.2095,
            "idle_kwh": 0.0,
            "offhours_kwh": 0.2095,
            "overconsumption_kwh": 0.0,
        }

    class _FrozenDatetime:
        @staticmethod
        def now(tz):
            import datetime as _dt

            return _dt.datetime(2026, 4, 5, 21, 0, 0, tzinfo=tz)

    monkeypatch.setattr(engine, "_get_allowed_device_ids", fake_allowed)
    monkeypatch.setattr("app.services.energy_engine.tariff_cache.get", fake_tariff)
    monkeypatch.setattr(engine, "_fetch_live_current_day_totals", fake_live)
    monkeypatch.setattr("app.services.energy_engine.datetime", _FrozenDatetime)

    result = await engine.get_device_range("SMOKE_A", date(2026, 4, 4), date(2026, 4, 5), tenant_id="tenant-1")

    assert result["totals"]["energy_kwh"] == 0.2095
    assert result["totals"]["loss_kwh"] == 0.2095
    assert result["days"] == [
        {
            "date": "2026-04-05",
            "energy_kwh": 0.2095,
            "energy_cost_inr": 0.0,
            "idle_kwh": 0.0,
            "offhours_kwh": 0.2095,
            "overconsumption_kwh": 0.0,
            "loss_kwh": 0.2095,
            "loss_cost_inr": 0.0,
            "quality_flags": ["live_projection_overlay"],
            "version": 11,
        }
    ]


@pytest.mark.asyncio
async def test_get_device_range_keeps_historical_day_without_live_overlay(monkeypatch):
    engine = EnergyEngine(_SessionStub([
        SimpleNamespace(
            day=date(2026, 4, 4),
            energy_kwh=0.1111,
            loss_kwh=0.1111,
            idle_kwh=0.0,
            offhours_kwh=0.1111,
            overconsumption_kwh=0.0,
            quality_flags='["fallback_integration"]',
            version=7,
        )
    ]))

    async def fake_allowed(_tenant_id):
        return None

    async def fake_tariff(_tenant_id):
        return {"rate": 0.0, "currency": "INR"}

    async def fail_live(*_args, **_kwargs):
        raise AssertionError("live overlay should not be called for historical-only range")

    class _FrozenDatetime:
        @staticmethod
        def now(tz):
            import datetime as _dt

            return _dt.datetime(2026, 4, 5, 21, 0, 0, tzinfo=tz)

    monkeypatch.setattr(engine, "_get_allowed_device_ids", fake_allowed)
    monkeypatch.setattr("app.services.energy_engine.tariff_cache.get", fake_tariff)
    monkeypatch.setattr(engine, "_fetch_live_current_day_totals", fail_live)
    monkeypatch.setattr("app.services.energy_engine.datetime", _FrozenDatetime)

    result = await engine.get_device_range("SMOKE_A", date(2026, 4, 4), date(2026, 4, 4), tenant_id="tenant-1")

    assert result["totals"]["energy_kwh"] == 0.1111
    assert result["totals"]["loss_kwh"] == 0.1111
    assert result["days"][0]["quality_flags"] == ["fallback_integration"]


@pytest.mark.asyncio
async def test_get_monthly_calendar_overlays_current_day_with_live_dashboard_totals(monkeypatch):
    engine = EnergyEngine(_SessionStub([
        SimpleNamespace(day=date(2026, 4, 4), energy_kwh=0.2000, energy_cost_inr=1.2, loss_kwh=0.0, loss_cost_inr=0.0, version=2),
        SimpleNamespace(day=date(2026, 4, 5), energy_kwh=0.1400, energy_cost_inr=0.84, loss_kwh=0.0, loss_cost_inr=0.0, version=7),
    ]))

    async def fake_allowed(_tenant_id):
        return {"SMOKE_A"}

    async def fake_tariff(_tenant_id):
        return {"rate": 6.0, "currency": "INR"}

    async def fake_totals(_tenant_id, plant_id=None):
        return {
            "today_energy_kwh": 0.15,
            "today_loss_kwh": 0.0,
        }

    class _FrozenDatetime:
        @staticmethod
        def now(tz):
            import datetime as _dt

            return _dt.datetime(2026, 4, 5, 23, 6, 0, tzinfo=tz)

    monkeypatch.setattr(engine, "_get_allowed_device_ids", fake_allowed)
    monkeypatch.setattr("app.services.energy_engine.tariff_cache.get", fake_tariff)
    monkeypatch.setattr(engine, "_fetch_live_dashboard_today_totals", fake_totals)
    monkeypatch.setattr("app.services.energy_engine.datetime", _FrozenDatetime)

    result = await engine.get_monthly_calendar(2026, 4, tenant_id="tenant-1")

    assert result["summary"]["total_energy_kwh"] == 0.35
    assert result["summary"]["total_energy_cost_inr"] == 2.1
    assert result["days"][3] == {
        "date": "2026-04-04",
        "energy_kwh": 0.2,
        "energy_cost_inr": 1.2,
        "loss_kwh": 0.0,
        "loss_cost_inr": 0.0,
    }
    assert result["days"][4] == {
        "date": "2026-04-05",
        "energy_kwh": 0.15,
        "energy_cost_inr": 0.9,
        "loss_kwh": 0.0,
        "loss_cost_inr": 0.0,
    }


@pytest.mark.asyncio
async def test_get_monthly_calendar_keeps_historical_month_without_live_overlay(monkeypatch):
    engine = EnergyEngine(_SessionStub([
        SimpleNamespace(day=date(2026, 3, 31), energy_kwh=0.1200, energy_cost_inr=0.72, loss_kwh=0.0, loss_cost_inr=0.0, version=3),
    ]))

    async def fake_allowed(_tenant_id):
        return {"SMOKE_A"}

    async def fake_tariff(_tenant_id):
        return {"rate": 6.0, "currency": "INR"}

    async def fail_totals(*_args, **_kwargs):
        raise AssertionError("dashboard live overlay should not be called for historical month")

    class _FrozenDatetime:
        @staticmethod
        def now(tz):
            import datetime as _dt

            return _dt.datetime(2026, 4, 5, 23, 6, 0, tzinfo=tz)

    monkeypatch.setattr(engine, "_get_allowed_device_ids", fake_allowed)
    monkeypatch.setattr("app.services.energy_engine.tariff_cache.get", fake_tariff)
    monkeypatch.setattr(engine, "_fetch_live_dashboard_today_totals", fail_totals)
    monkeypatch.setattr("app.services.energy_engine.datetime", _FrozenDatetime)

    result = await engine.get_monthly_calendar(2026, 3, tenant_id="tenant-1")

    assert result["summary"]["total_energy_kwh"] == 0.12
    assert result["summary"]["total_energy_cost_inr"] == 0.72
    assert result["days"][30] == {
        "date": "2026-03-31",
        "energy_kwh": 0.12,
        "energy_cost_inr": 0.72,
        "loss_kwh": 0.0,
        "loss_cost_inr": 0.0,
    }


@pytest.mark.asyncio
async def test_get_device_range_prefers_persisted_historical_costs_over_current_tariff(monkeypatch):
    engine = EnergyEngine(_SessionStub([
        SimpleNamespace(
            day=date(2026, 3, 30),
            energy_kwh=1.0,
            energy_cost_inr=6.5,
            loss_kwh=0.2,
            loss_cost_inr=1.3,
            idle_kwh=0.0,
            offhours_kwh=0.2,
            overconsumption_kwh=0.0,
            quality_flags="[]",
            version=2,
        ),
        SimpleNamespace(
            day=date(2026, 3, 31),
            energy_kwh=2.0,
            energy_cost_inr=14.0,
            loss_kwh=0.1,
            loss_cost_inr=0.7,
            idle_kwh=0.0,
            offhours_kwh=0.1,
            overconsumption_kwh=0.0,
            quality_flags="[]",
            version=3,
        ),
    ]))

    async def fake_allowed(_tenant_id):
        return {"SMOKE_A"}

    async def fake_tariff(_tenant_id):
        return {"rate": 10.0, "currency": "INR"}

    async def fail_live(*_args, **_kwargs):
        raise AssertionError("historical range should not request live overlay")

    class _FrozenDatetime:
        @staticmethod
        def now(tz):
            import datetime as _dt

            return _dt.datetime(2026, 4, 5, 23, 6, 0, tzinfo=tz)

    monkeypatch.setattr(engine, "_get_allowed_device_ids", fake_allowed)
    monkeypatch.setattr("app.services.energy_engine.tariff_cache.get", fake_tariff)
    monkeypatch.setattr(engine, "_fetch_live_current_day_totals", fail_live)
    monkeypatch.setattr("app.services.energy_engine.datetime", _FrozenDatetime)

    result = await engine.get_device_range("SMOKE_A", date(2026, 3, 30), date(2026, 3, 31), tenant_id="tenant-1")

    assert result["totals"]["energy_kwh"] == 3.0
    assert result["totals"]["energy_cost_inr"] == 20.5
    assert result["totals"]["loss_cost_inr"] == 2.0
    assert result["days"] == [
        {
            "date": "2026-03-30",
            "energy_kwh": 1.0,
            "energy_cost_inr": 6.5,
            "idle_kwh": 0.0,
            "offhours_kwh": 0.2,
            "overconsumption_kwh": 0.0,
            "loss_kwh": 0.2,
            "loss_cost_inr": 1.3,
            "quality_flags": [],
            "version": 2,
        },
        {
            "date": "2026-03-31",
            "energy_kwh": 2.0,
            "energy_cost_inr": 14.0,
            "idle_kwh": 0.0,
            "offhours_kwh": 0.1,
            "overconsumption_kwh": 0.0,
            "loss_kwh": 0.1,
            "loss_cost_inr": 0.7,
            "quality_flags": [],
            "version": 3,
        },
    ]


@pytest.mark.asyncio
async def test_get_monthly_calendar_prefers_persisted_historical_costs_over_current_tariff(monkeypatch):
    engine = EnergyEngine(_SessionStub([
        SimpleNamespace(day=date(2026, 3, 30), energy_kwh=1.0, energy_cost_inr=6.5, loss_kwh=0.0, loss_cost_inr=0.0, version=2),
        SimpleNamespace(day=date(2026, 3, 31), energy_kwh=2.0, energy_cost_inr=14.0, loss_kwh=0.0, loss_cost_inr=0.0, version=3),
    ]))

    async def fake_allowed(_tenant_id):
        return {"SMOKE_A"}

    async def fake_tariff(_tenant_id):
        return {"rate": 10.0, "currency": "INR"}

    async def fail_totals(*_args, **_kwargs):
        raise AssertionError("historical month should not request live overlay")

    class _FrozenDatetime:
        @staticmethod
        def now(tz):
            import datetime as _dt

            return _dt.datetime(2026, 4, 5, 23, 6, 0, tzinfo=tz)

    monkeypatch.setattr(engine, "_get_allowed_device_ids", fake_allowed)
    monkeypatch.setattr("app.services.energy_engine.tariff_cache.get", fake_tariff)
    monkeypatch.setattr(engine, "_fetch_live_dashboard_today_totals", fail_totals)
    monkeypatch.setattr("app.services.energy_engine.datetime", _FrozenDatetime)

    result = await engine.get_monthly_calendar(2026, 3, tenant_id="tenant-1")

    assert result["summary"]["total_energy_kwh"] == 3.0
    assert result["summary"]["total_energy_cost_inr"] == 20.5
    assert result["days"][29] == {"date": "2026-03-30", "energy_kwh": 1.0, "energy_cost_inr": 6.5, "loss_kwh": 0.0, "loss_cost_inr": 0.0}
    assert result["days"][30] == {"date": "2026-03-31", "energy_kwh": 2.0, "energy_cost_inr": 14.0, "loss_kwh": 0.0, "loss_cost_inr": 0.0}


@pytest.mark.asyncio
async def test_get_summary_prefers_persisted_aggregate_costs(monkeypatch):
    call_count = {"value": 0}

    async def fake_allowed(_tenant_id):
        return {"SMOKE_A"}

    async def fake_tariff(_tenant_id):
        return {"rate": 10.0, "currency": "INR"}

    async def no_live_totals(*_args, **_kwargs):
        return None

    class _SummarySessionStub:
        async def execute(self, _query):
            call_count["value"] += 1
            if call_count["value"] == 1:
                return _ScalarResult([SimpleNamespace(energy_kwh=0.41, energy_cost_inr=2.667, loss_kwh=0.29, loss_cost_inr=1.887, version=3)])
            return _ScalarResult([SimpleNamespace(energy_kwh=0.70, energy_cost_inr=4.233, loss_kwh=0.29, loss_cost_inr=1.887, version=4)])

    engine = EnergyEngine(_SummarySessionStub())
    monkeypatch.setattr(engine, "_get_allowed_device_ids", fake_allowed)
    monkeypatch.setattr("app.services.energy_engine.tariff_cache.get", fake_tariff)
    monkeypatch.setattr(engine, "_fetch_live_dashboard_today_totals", no_live_totals)

    result = await engine.get_summary(tenant_id="tenant-1")

    assert result["energy_widgets"]["today_energy_kwh"] == 0.41
    assert result["energy_widgets"]["today_energy_cost_inr"] == 2.667
    assert result["energy_widgets"]["today_loss_cost_inr"] == 1.887
    assert result["energy_widgets"]["month_energy_kwh"] == 0.7
    assert result["energy_widgets"]["month_energy_cost_inr"] == 4.233


@pytest.mark.asyncio
async def test_get_summary_overlays_tenant_live_dashboard_widgets(monkeypatch):
    engine = EnergyEngine(_SessionStub([
        SimpleNamespace(day=date(2026, 4, 5), energy_kwh=0.41, loss_kwh=0.29, version=3),
        SimpleNamespace(month=date(2026, 4, 1), energy_kwh=0.41, loss_kwh=0.29, version=4),
    ]))

    call_count = {"value": 0}

    async def fake_allowed(_tenant_id):
        return {"SMOKE_A"}

    async def fake_tariff(_tenant_id):
        return {"rate": 10.0, "currency": "INR"}

    async def fake_widgets(_tenant_id):
        return {
            "month_energy_kwh": 0.4667,
            "today_energy_kwh": 0.4667,
            "today_loss_kwh": 0.3667,
        }

    class _SummarySessionStub:
        async def execute(self, _query):
            call_count["value"] += 1
            if call_count["value"] == 1:
                return _ScalarResult([SimpleNamespace(energy_kwh=0.41, loss_kwh=0.29, version=3)])
            return _ScalarResult([SimpleNamespace(energy_kwh=0.41, loss_kwh=0.29, version=4)])

    engine = EnergyEngine(_SummarySessionStub())
    monkeypatch.setattr(engine, "_get_allowed_device_ids", fake_allowed)
    monkeypatch.setattr("app.services.energy_engine.tariff_cache.get", fake_tariff)
    monkeypatch.setattr(engine, "_fetch_live_dashboard_energy_widgets", fake_widgets)

    result = await engine.get_summary(tenant_id="tenant-1")

    assert result["energy_widgets"]["today_energy_kwh"] == 0.4667
    assert result["energy_widgets"]["today_loss_kwh"] == 0.3667
    assert result["energy_widgets"]["month_energy_kwh"] == 0.4667
    assert result["energy_widgets"]["today_energy_cost_inr"] == 4.667
    assert result["energy_widgets"]["today_loss_cost_inr"] == 3.667
    assert result["energy_widgets"]["month_energy_cost_inr"] == 4.667


@pytest.mark.asyncio
async def test_get_today_loss_breakdown_overlays_current_day_live_totals(monkeypatch):
    engine = EnergyEngine(_SessionStub([
        SimpleNamespace(
            device_id="SMOKE_A",
            day=date(2026, 4, 5),
            energy_kwh=0.10,
            loss_kwh=0.08,
            idle_kwh=0.01,
            offhours_kwh=0.07,
            overconsumption_kwh=0.0,
            version=2,
        ),
        SimpleNamespace(
            device_id="SMOKE_B",
            day=date(2026, 4, 5),
            energy_kwh=0.20,
            loss_kwh=0.18,
            idle_kwh=0.02,
            offhours_kwh=0.16,
            overconsumption_kwh=0.0,
            version=3,
        ),
    ]))

    async def fake_allowed(_tenant_id):
        return {"SMOKE_A", "SMOKE_B"}

    async def fake_tariff(_tenant_id):
        return {"rate": 10.0, "currency": "INR"}

    async def fake_live(device_id, _tenant_id):
        if device_id == "SMOKE_A":
            return {
                "date": "2026-04-05",
                "energy_kwh": 0.1667,
                "loss_kwh": 0.1667,
                "idle_kwh": 0.0,
                "offhours_kwh": 0.1667,
                "overconsumption_kwh": 0.0,
            }
        return {
            "date": "2026-04-05",
            "energy_kwh": 0.1,
            "loss_kwh": 0.1,
            "idle_kwh": 0.0,
            "offhours_kwh": 0.1,
            "overconsumption_kwh": 0.0,
        }

    async def fake_meta(device_id, tenant_id):
        return {"device_name": device_id}

    class _FrozenDatetime:
        @staticmethod
        def now(tz):
            import datetime as _dt

            return _dt.datetime(2026, 4, 5, 23, 6, 0, tzinfo=tz)

    monkeypatch.setattr(engine, "_get_allowed_device_ids", fake_allowed)
    monkeypatch.setattr("app.services.energy_engine.tariff_cache.get", fake_tariff)
    monkeypatch.setattr(engine, "_fetch_live_current_day_totals", fake_live)
    monkeypatch.setattr("app.services.energy_engine.meta_cache.get", fake_meta)
    monkeypatch.setattr("app.services.energy_engine.datetime", _FrozenDatetime)

    result = await engine.get_today_loss_breakdown(tenant_id="tenant-1")

    assert result["totals"]["today_energy_kwh"] == 0.2667
    assert result["totals"]["total_loss_kwh"] == 0.2667
    assert result["totals"]["today_energy_cost_inr"] == 2.667
    assert result["totals"]["total_loss_cost_inr"] == 2.667
    assert result["rows"] == [
        {
            "device_id": "SMOKE_A",
            "device_name": "SMOKE_A",
            "idle_kwh": 0.0,
            "idle_cost_inr": 0.0,
            "off_hours_kwh": 0.1667,
            "off_hours_cost_inr": 1.667,
            "overconsumption_kwh": 0.0,
            "overconsumption_cost_inr": 0.0,
            "total_loss_kwh": 0.1667,
            "total_loss_cost_inr": 1.667,
            "status": "computed",
            "reason": "live_projection_overlay",
        },
        {
            "device_id": "SMOKE_B",
            "device_name": "SMOKE_B",
            "idle_kwh": 0.0,
            "idle_cost_inr": 0.0,
            "off_hours_kwh": 0.1,
            "off_hours_cost_inr": 1.0,
            "overconsumption_kwh": 0.0,
            "overconsumption_cost_inr": 0.0,
            "total_loss_kwh": 0.1,
            "total_loss_cost_inr": 1.0,
            "status": "computed",
            "reason": "live_projection_overlay",
        },
    ]


@pytest.mark.asyncio
async def test_get_monthly_calendar_today_cell_preserves_current_day_loss_fields(monkeypatch):
    engine = EnergyEngine(_SessionStub([
        SimpleNamespace(day=date(2026, 4, 4), energy_kwh=0.2000, energy_cost_inr=1.2, loss_kwh=0.08, loss_cost_inr=0.48, version=2),
        SimpleNamespace(day=date(2026, 4, 5), energy_kwh=0.1400, energy_cost_inr=0.84, loss_kwh=0.03, loss_cost_inr=0.18, version=7),
    ]))

    async def fake_allowed(_tenant_id):
        return {"SMOKE_A"}

    async def fake_tariff(_tenant_id):
        return {"rate": 6.0, "currency": "INR"}

    async def fake_totals(_tenant_id, plant_id=None):
        return {
            "today_energy_kwh": 0.15,
            "today_loss_kwh": 0.05,
        }

    class _FrozenDatetime:
        @staticmethod
        def now(tz):
            import datetime as _dt
            return _dt.datetime(2026, 4, 5, 23, 6, 0, tzinfo=tz)

    monkeypatch.setattr(engine, "_get_allowed_device_ids", fake_allowed)
    monkeypatch.setattr("app.services.energy_engine.tariff_cache.get", fake_tariff)
    monkeypatch.setattr(engine, "_fetch_live_dashboard_today_totals", fake_totals)
    monkeypatch.setattr("app.services.energy_engine.datetime", _FrozenDatetime)

    result = await engine.get_monthly_calendar(2026, 4, tenant_id="tenant-1")

    historical_day = result["days"][3]
    assert historical_day["date"] == "2026-04-04"
    assert historical_day["loss_kwh"] == 0.08
    assert historical_day["loss_cost_inr"] == 0.48

    today_day = result["days"][4]
    assert today_day["date"] == "2026-04-05"
    assert today_day["energy_kwh"] == 0.15
    assert today_day["loss_kwh"] == 0.05
    assert today_day["loss_kwh"] <= today_day["energy_kwh"]


@pytest.mark.asyncio
async def test_get_today_loss_breakdown_satisfies_loss_leq_energy_invariant(monkeypatch):
    engine = EnergyEngine(_SessionStub([
        SimpleNamespace(
            device_id="INV_A",
            day=date(2026, 4, 5),
            energy_kwh=0.20,
            loss_kwh=0.05,
            idle_kwh=0.01,
            offhours_kwh=0.03,
            overconsumption_kwh=0.01,
            version=2,
        ),
        SimpleNamespace(
            device_id="INV_B",
            day=date(2026, 4, 5),
            energy_kwh=0.10,
            loss_kwh=0.02,
            idle_kwh=0.01,
            offhours_kwh=0.01,
            overconsumption_kwh=0.0,
            version=3,
        ),
    ]))

    async def fake_allowed(_tenant_id):
        return {"INV_A", "INV_B"}

    async def fake_tariff(_tenant_id):
        return {"rate": 10.0, "currency": "INR"}

    async def fake_live(device_id, _tenant_id):
        if device_id == "INV_A":
            return {
                "date": "2026-04-05",
                "energy_kwh": 0.25,
                "loss_kwh": 0.06,
                "idle_kwh": 0.01,
                "offhours_kwh": 0.04,
                "overconsumption_kwh": 0.01,
            }
        return {
            "date": "2026-04-05",
            "energy_kwh": 0.12,
            "loss_kwh": 0.03,
            "idle_kwh": 0.01,
            "offhours_kwh": 0.02,
            "overconsumption_kwh": 0.0,
        }

    async def fake_meta(device_id, tenant_id):
        return {"device_name": device_id}

    class _FrozenDatetime:
        @staticmethod
        def now(tz):
            import datetime as _dt
            return _dt.datetime(2026, 4, 5, 23, 6, 0, tzinfo=tz)

    monkeypatch.setattr(engine, "_get_allowed_device_ids", fake_allowed)
    monkeypatch.setattr("app.services.energy_engine.tariff_cache.get", fake_tariff)
    monkeypatch.setattr(engine, "_fetch_live_current_day_totals", fake_live)
    monkeypatch.setattr("app.services.energy_engine.meta_cache.get", fake_meta)
    monkeypatch.setattr("app.services.energy_engine.datetime", _FrozenDatetime)

    result = await engine.get_today_loss_breakdown(tenant_id="tenant-1")

    assert result["totals"]["total_loss_kwh"] <= result["totals"]["today_energy_kwh"]

    for row in result["rows"]:
        assert row["total_loss_kwh"] <= result["totals"]["today_energy_kwh"]
