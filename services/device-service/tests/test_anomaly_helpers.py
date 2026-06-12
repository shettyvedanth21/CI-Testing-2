from __future__ import annotations

import sys
import types
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

_BASE_DIR = Path(__file__).resolve().parents[1]


def _ensure_app_stubs() -> None:
    if "app" not in sys.modules:
        _app = types.ModuleType("app")
        _app.__path__ = [str(_BASE_DIR / "app")]
        _app.__package__ = "app"
        _app.__file__ = str(_BASE_DIR / "app" / "__init__.py")
        sys.modules["app"] = _app

    if "app.services" not in sys.modules:
        _svc = types.ModuleType("app.services")
        _svc.__path__ = [str(_BASE_DIR / "app" / "services")]
        _svc.__package__ = "app.services"
        _svc.__file__ = str(_BASE_DIR / "app" / "services" / "__init__.py")
        sys.modules["app.services"] = _svc
        sys.modules["app"].services = _svc


_ensure_app_stubs()

from app.services.anomaly.helpers import (
    build_anomaly_baseline_dict,
    build_anomaly_event_dict,
    build_daily_count_dict,
    build_weekly_count_dict,
)
from app.services.anomaly.types import (
    AnomalyFieldBaseline,
    AnomalyCandidate,
    DailyCountResult,
    WeeklyCountResult,
)


def _make_baseline(field_name="current_avg", mean=10.0, std=1.0, median=10.0,
                   mad=1.0, p05=8.0, p95=12.0, reading_count=20,
                   quality_score=0.9, quality_band="high", status="active",
                   time_window="5min", baseline_version=2,
                   learned_from_ts=None, learned_to_ts=None,
                   field_coverage=0.95, steady_coverage=0.85):
    return AnomalyFieldBaseline(
        field_name=field_name,
        time_window=time_window,
        baseline_mean=mean,
        baseline_std=std,
        baseline_median=median,
        baseline_mad=mad,
        baseline_p05=p05,
        baseline_p95=p95,
        reading_count=reading_count,
        quality_score=quality_score,
        quality_band=quality_band,
        status=status,
        baseline_version=baseline_version,
        learned_from_ts=learned_from_ts,
        learned_to_ts=learned_to_ts,
        field_coverage=field_coverage,
        steady_coverage=steady_coverage,
    )


def _make_candidate(signal_field="current_avg", signal_value=14.0,
                     baseline_mean=10.0, baseline_std=1.0, z_score=4.0,
                     anomaly_type="deviation", severity="severe",
                     confidence=0.9, supply_related=False,
                     startup_adjacent=False, mode_change=False,
                     recurring=False, time_window="5min",
                     correlated_signals=(), baseline_version=2,
                     occurred_at=None, ended_at=None,
                     duration_seconds=None, merged_window_count=1,
                     z_score_history=()):
    return AnomalyCandidate(
        signal_field=signal_field,
        signal_value=signal_value,
        baseline_mean=baseline_mean,
        baseline_std=baseline_std,
        z_score=z_score,
        anomaly_type=anomaly_type,
        severity=severity,
        confidence=confidence,
        supply_related=supply_related,
        startup_adjacent=startup_adjacent,
        mode_change=mode_change,
        recurring=recurring,
        time_window=time_window,
        correlated_signals=correlated_signals,
        baseline_version=baseline_version,
        occurred_at=occurred_at,
        ended_at=ended_at,
        duration_seconds=duration_seconds,
        merged_window_count=merged_window_count,
        z_score_history=z_score_history,
    )


def test_build_anomaly_baseline_dict_happy():
    ts_from = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    ts_to = datetime(2026, 1, 2, 0, 0, tzinfo=timezone.utc)
    bl = _make_baseline(learned_from_ts=ts_from, learned_to_ts=ts_to)
    d = build_anomaly_baseline_dict(bl, tenant_id="t1", device_id="d1")

    assert d["tenant_id"] == "t1"
    assert d["device_id"] == "d1"
    assert d["field_name"] == "current_avg"
    assert d["time_window"] == "5min"
    assert d["baseline_mean"] == 10.0
    assert d["baseline_std"] == 1.0
    assert d["baseline_median"] == 10.0
    assert d["baseline_mad"] == 1.0
    assert d["baseline_p05"] == 8.0
    assert d["baseline_p95"] == 12.0
    assert d["reading_count"] == 20
    assert d["quality_score"] == 0.9
    assert d["learned_from_ts"] == ts_from
    assert d["learned_to_ts"] == ts_to
    assert d["status"] == "active"
    assert d["baseline_version"] == 2


def test_build_anomaly_baseline_dict_zero_reading_count():
    bl = _make_baseline(reading_count=0, quality_score=0.0)
    d = build_anomaly_baseline_dict(bl, tenant_id="t1", device_id="d1")

    assert d["reading_count"] is None
    assert d["quality_score"] is None


def test_build_anomaly_baseline_dict_candidate_status():
    bl = _make_baseline(status="candidate", quality_band="low", quality_score=0.2)
    d = build_anomaly_baseline_dict(bl, tenant_id="t1", device_id="d1")

    assert d["status"] == "candidate"
    assert d["quality_score"] == 0.2


def test_build_anomaly_event_dict_happy():
    ts_occ = datetime(2026, 1, 1, 1, 0, tzinfo=timezone.utc)
    ts_end = datetime(2026, 1, 1, 2, 0, tzinfo=timezone.utc)
    cand = _make_candidate(occurred_at=ts_occ, ended_at=ts_end)
    d = build_anomaly_event_dict(cand, tenant_id="t1", device_id="d1")

    assert d["tenant_id"] == "t1"
    assert d["device_id"] == "d1"
    assert d["occurred_at"] == ts_occ
    assert d["ended_at"] == ts_end
    assert d["duration_seconds"] == 3600
    assert d["signal_field"] == "current_avg"
    assert d["signal_value"] == 14.0
    assert d["baseline_mean"] == 10.0
    assert d["baseline_std"] == 1.0
    assert d["z_score"] == 4.0
    assert d["anomaly_type"] == "deviation"
    assert d["severity"] == "severe"
    assert d["confidence"] == 0.9
    assert d["supply_related"] is False
    assert d["startup_adjacent"] is False
    assert d["mode_change"] is False
    assert d["recurring"] is False
    assert d["time_window"] == "5min"
    assert d["correlated_signals_json"] is None
    assert d["baseline_version"] == 2


def test_build_anomaly_event_dict_with_correlated_signals():
    cand = _make_candidate(correlated_signals=("power", "power_factor"))
    d = build_anomaly_event_dict(cand, tenant_id="t1", device_id="d1")

    assert d["correlated_signals_json"] is not None
    import json
    parsed = json.loads(d["correlated_signals_json"])
    assert set(parsed) == {"power", "power_factor"}


def test_build_anomaly_event_dict_zero_confidence():
    cand = _make_candidate(confidence=0.0)
    d = build_anomaly_event_dict(cand, tenant_id="t1", device_id="d1")

    assert d["confidence"] is None


def test_build_anomaly_event_dict_supply_related():
    cand = _make_candidate(supply_related=True)
    d = build_anomaly_event_dict(cand, tenant_id="t1", device_id="d1")

    assert d["supply_related"] is True


def test_build_anomaly_event_dict_no_timestamps():
    cand = _make_candidate(occurred_at=None, ended_at=None, duration_seconds=None)
    d = build_anomaly_event_dict(cand, tenant_id="t1", device_id="d1")

    assert d["occurred_at"] is None
    assert d["ended_at"] is None
    assert d["duration_seconds"] is None


def test_build_anomaly_event_dict_explicit_duration():
    cand = _make_candidate(
        occurred_at=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
        ended_at=datetime(2026, 1, 1, 2, 0, tzinfo=timezone.utc),
        duration_seconds=9999,
    )
    d = build_anomaly_event_dict(cand, tenant_id="t1", device_id="d1")

    assert d["duration_seconds"] == 9999


def test_build_anomaly_event_dict_computed_at():
    ts_occ = datetime(2026, 1, 1, 1, 0, tzinfo=timezone.utc)
    ts_end = datetime(2026, 1, 1, 2, 0, tzinfo=timezone.utc)
    computed = datetime(2026, 1, 1, 2, 5, tzinfo=timezone.utc)
    cand = _make_candidate(occurred_at=ts_occ, ended_at=ts_end)
    d = build_anomaly_event_dict(cand, tenant_id="t1", device_id="d1", computed_at=computed)

    assert d["occurred_at"] == ts_occ
    assert d["ended_at"] == ts_end


def test_build_daily_count_dict_happy():
    dc = DailyCountResult(
        date=date(2026, 5, 21),
        total_count=5,
        mild_count=3,
        strong_count=1,
        severe_count=1,
        supply_related_count=0,
        top_signal="current_avg",
        avg_confidence=0.85,
    )
    d = build_daily_count_dict(dc, tenant_id="t1", device_id="d1")

    assert d["tenant_id"] == "t1"
    assert d["device_id"] == "d1"
    assert d["date"] == date(2026, 5, 21)
    assert d["total_count"] == 5
    assert d["mild_count"] == 3
    assert d["strong_count"] == 1
    assert d["severe_count"] == 1
    assert d["supply_related_count"] == 0
    assert d["top_signal"] == "current_avg"
    assert d["avg_confidence"] == 0.85


def test_build_daily_count_dict_none_optional():
    dc = DailyCountResult(
        date=date(2026, 5, 21),
        total_count=1,
        mild_count=1,
        top_signal=None,
        avg_confidence=None,
    )
    d = build_daily_count_dict(dc, tenant_id="t1", device_id="d1")

    assert d["top_signal"] is None
    assert d["avg_confidence"] is None


def test_build_weekly_count_dict_happy():
    wc = WeeklyCountResult(
        week_start_date=date(2026, 5, 19),
        total_count=10,
        mild_count=6,
        strong_count=3,
        severe_count=1,
        week_over_week_change=3,
    )
    d = build_weekly_count_dict(wc, tenant_id="t1", device_id="d1")

    assert d["tenant_id"] == "t1"
    assert d["device_id"] == "d1"
    assert d["week_start_date"] == date(2026, 5, 19)
    assert d["total_count"] == 10
    assert d["mild_count"] == 6
    assert d["strong_count"] == 3
    assert d["severe_count"] == 1
    assert d["week_over_week_change"] == 3


def test_build_weekly_count_dict_no_prior():
    wc = WeeklyCountResult(
        week_start_date=date(2026, 5, 19),
        total_count=5,
        week_over_week_change=None,
    )
    d = build_weekly_count_dict(wc, tenant_id="t1", device_id="d1")

    assert d["week_over_week_change"] is None


def test_build_weekly_count_dict_negative_wow():
    wc = WeeklyCountResult(
        week_start_date=date(2026, 5, 19),
        total_count=2,
        week_over_week_change=-3,
    )
    d = build_weekly_count_dict(wc, tenant_id="t1", device_id="d1")

    assert d["week_over_week_change"] == -3


def test_build_weekly_count_dict_includes_signal_breakdown_json():
    from app.services.anomaly.types import SignalBreakdownEntry
    wc = WeeklyCountResult(
        week_start_date=date(2026, 5, 19),
        total_count=10,
        mild_count=6,
        strong_count=3,
        severe_count=1,
        supply_related_count=2,
        top_signal="current_avg",
        avg_confidence=0.85,
        signal_breakdown=(
            SignalBreakdownEntry(field_name="current_avg", count=6, mild=4, strong=2, severe=0),
            SignalBreakdownEntry(field_name="power", count=4, mild=2, strong=1, severe=1),
        ),
        week_over_week_change=3,
    )
    d = build_weekly_count_dict(wc, tenant_id="t1", device_id="d1")

    assert d["supply_related_count"] == 2
    assert d["top_signal"] == "current_avg"
    assert d["avg_confidence"] == pytest.approx(0.85)
    assert d["signal_breakdown_json"] is not None
    import json
    parsed = json.loads(d["signal_breakdown_json"])
    assert len(parsed) == 2
    assert parsed[0]["field_name"] == "current_avg"
    assert parsed[0]["count"] == 6


def test_build_weekly_count_dict_no_signal_breakdown():
    wc = WeeklyCountResult(
        week_start_date=date(2026, 5, 19),
        total_count=5,
        week_over_week_change=None,
    )
    d = build_weekly_count_dict(wc, tenant_id="t1", device_id="d1")

    assert d["signal_breakdown_json"] is None


def test_build_anomaly_event_dict_merged_duration_precomputed():
    ts_occ = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    ts_end = datetime(2026, 1, 1, 3, 0, tzinfo=timezone.utc)
    cand = _make_candidate(
        occurred_at=ts_occ, ended_at=ts_end,
        duration_seconds=10800,
        merged_window_count=3,
        z_score_history=(3.0, 3.2, 3.5),
    )
    d = build_anomaly_event_dict(cand, tenant_id="t1", device_id="d1")

    assert d["duration_seconds"] == 10800
    assert d["occurred_at"] == ts_occ
    assert d["ended_at"] == ts_end
