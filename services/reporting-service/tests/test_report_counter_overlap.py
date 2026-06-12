from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(1, str(ROOT / "services" / "reporting-service"))
sys.path.insert(2, str(ROOT / "services"))

from src.services.report_engine import (
    _compute_overlap_subtraction,
    _to_df,
    compute_device_report,
)
from services.shared.telemetry_normalization import (
    NormalizedIntervalEnergy,
    NormalizedTelemetrySample,
    compute_interval_energy_delta,
    normalize_telemetry_sample,
)


def _ts(offset_min: int = 0) -> datetime:
    return datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc) + timedelta(minutes=offset_min)


def _make_sample(power_w: float = 1000.0, energy_kwh: float | None = None, offset_min: int = 0) -> NormalizedTelemetrySample:
    return normalize_telemetry_sample(
        {
            "timestamp": _ts(offset_min).isoformat(),
            "power": power_w,
            "energy_kwh": energy_kwh,
        },
        {},
    )


def _flat_with_fallback(business_kwh: float = 0.5) -> NormalizedIntervalEnergy:
    return NormalizedIntervalEnergy(
        business_energy_delta_kwh=business_kwh,
        import_energy_delta_kwh=business_kwh,
        export_energy_delta_kwh=0.0,
        counter_delta_kwh=0.0,
        energy_delta_method="power_integration",
        quality_flags=("counter_noise_floor_applied", "fallback_integration"),
        quality_class="estimated",
        reason_code="fallback_measured_power",
        elapsed_seconds=60.0,
        fallback_delta_kwh=business_kwh,
        implied_avg_kw=0.0,
        comparison_power_kw=1.0,
        coverage_seconds=60.0,
    )


def _counter_jump(delta_kwh: float = 0.75) -> NormalizedIntervalEnergy:
    return NormalizedIntervalEnergy(
        business_energy_delta_kwh=delta_kwh,
        import_energy_delta_kwh=delta_kwh,
        export_energy_delta_kwh=0.0,
        counter_delta_kwh=delta_kwh,
        energy_delta_method="counter",
        quality_flags=(),
        quality_class="billing_grade",
        reason_code="counter_accepted",
        elapsed_seconds=60.0,
        fallback_delta_kwh=delta_kwh,
        implied_avg_kw=delta_kwh * 60.0,
        comparison_power_kw=1.0,
        coverage_seconds=60.0,
    )


def _counter_accepted_zero() -> NormalizedIntervalEnergy:
    return NormalizedIntervalEnergy(
        business_energy_delta_kwh=0.0,
        import_energy_delta_kwh=0.0,
        export_energy_delta_kwh=0.0,
        counter_delta_kwh=0.0,
        energy_delta_method="counter",
        quality_flags=("counter_noise_floor_applied",),
        quality_class="billing_grade",
        reason_code="counter_accepted",
        elapsed_seconds=60.0,
        fallback_delta_kwh=None,
        implied_avg_kw=0.0,
        comparison_power_kw=1.0,
        coverage_seconds=60.0,
    )


def _power_fallback_no_counter(business_kwh: float = 0.5) -> NormalizedIntervalEnergy:
    return NormalizedIntervalEnergy(
        business_energy_delta_kwh=business_kwh,
        import_energy_delta_kwh=business_kwh,
        export_energy_delta_kwh=0.0,
        counter_delta_kwh=None,
        energy_delta_method="power_integration",
        quality_flags=("counter_missing", "fallback_integration"),
        quality_class="estimated",
        reason_code="fallback_measured_power",
        elapsed_seconds=60.0,
        fallback_delta_kwh=business_kwh,
        implied_avg_kw=0.0,
        comparison_power_kw=1.0,
        coverage_seconds=60.0,
    )


def test_coarse_counter_jump_subtracts_preceding_fallback_overlap() -> None:
    flat = _flat_with_fallback(business_kwh=0.5)
    jump = _counter_jump(delta_kwh=0.75)
    overlap = _compute_overlap_subtraction([flat, jump])
    assert overlap == 0.5


def test_trailing_flat_after_jump_preserves_fallback() -> None:
    flat0 = _flat_with_fallback(business_kwh=0.5)
    jump = _counter_jump(delta_kwh=0.75)
    trailing = _flat_with_fallback(business_kwh=0.4)
    overlap = _compute_overlap_subtraction([flat0, jump, trailing])
    assert overlap == 0.5
    assert trailing.business_energy_delta_kwh == 0.4


def test_stuck_counter_preserves_all_fallback() -> None:
    flat0 = _flat_with_fallback(business_kwh=0.5)
    flat1 = _flat_with_fallback(business_kwh=0.6)
    flat2 = _flat_with_fallback(business_kwh=0.4)
    overlap = _compute_overlap_subtraction([flat0, flat1, flat2])
    assert overlap == 0.0


def test_per_interval_counter_no_subtraction() -> None:
    c0 = _counter_jump(delta_kwh=0.75)
    c1 = _counter_jump(delta_kwh=0.75)
    overlap = _compute_overlap_subtraction([c0, c1])
    assert overlap == 0.0


def test_multiple_jumps_do_not_double_subtract() -> None:
    flat0 = _flat_with_fallback(business_kwh=0.5)
    jump1 = _counter_jump(delta_kwh=0.75)
    flat2 = _flat_with_fallback(business_kwh=0.3)
    jump2 = _counter_jump(delta_kwh=0.6)
    overlap = _compute_overlap_subtraction([flat0, jump1, flat2, jump2])
    assert overlap == flat0.business_energy_delta_kwh + flat2.business_energy_delta_kwh


def test_noise_floor_accepted_zero_not_subtracted() -> None:
    zero = _counter_accepted_zero()
    jump = _counter_jump(delta_kwh=0.75)
    overlap = _compute_overlap_subtraction([zero, jump])
    assert overlap == 0.0


def test_counter_missing_not_subtracted() -> None:
    missing = _power_fallback_no_counter(business_kwh=0.5)
    jump = _counter_jump(delta_kwh=0.75)
    overlap = _compute_overlap_subtraction([missing, jump])
    assert overlap == 0.0


def test_time_fallback_in_to_df() -> None:
    rows = [
        {"_time": _ts(0).isoformat(), "power": 1000.0},
        {"_time": _ts(1).isoformat(), "power": 2000.0},
    ]
    df = _to_df(rows)
    assert not df.empty
    assert "timestamp" in df.columns


def test_timestamp_preferred_over_time() -> None:
    rows = [
        {"timestamp": _ts(0).isoformat(), "_time": _ts(10).isoformat(), "power": 1000.0},
        {"timestamp": _ts(1).isoformat(), "_time": _ts(11).isoformat(), "power": 2000.0},
    ]
    df = _to_df(rows)
    assert not df.empty
    first_ts = df.iloc[0]["timestamp"]
    assert first_ts.hour == 12


def test_compute_device_report_uses_counter_delta() -> None:
    rows = [
        {"timestamp": _ts(0).isoformat(), "power": 9000.0, "energy_kwh": 10.0},
        {"timestamp": _ts(5).isoformat(), "power": 9000.0, "energy_kwh": 10.75},
        {"timestamp": _ts(10).isoformat(), "power": 9000.0, "energy_kwh": 11.5},
    ]
    result = compute_device_report(rows, "dev-1", "Test Device", "metered")
    assert result.total_kwh is not None
    assert result.method == "counter_integration"
    assert result.quality == "billing_grade"


def test_daily_breakdown_inherits_counter_logic() -> None:
    base = datetime(2026, 5, 1, 23, 50, tzinfo=timezone.utc)
    rows = [
        {"timestamp": base.isoformat(), "power": 9000.0, "energy_kwh": 10.0},
        {"timestamp": (base + timedelta(minutes=5)).isoformat(), "power": 9000.0, "energy_kwh": 10.75},
        {"timestamp": (base + timedelta(minutes=15)).isoformat(), "power": 9000.0, "energy_kwh": 11.5},
    ]
    result = compute_device_report(rows, "dev-1", "Test Device", "metered")
    assert len(result.daily_breakdown) >= 1
    for day in result.daily_breakdown:
        if day["energy_kwh"] is not None and day["energy_kwh"] > 0:
            assert day["method"] in ("counter_integration", "normalized_business_power")


def test_bounded_gap_900s_rejects_long_intervals() -> None:
    rows = [
        {"timestamp": _ts(0).isoformat(), "power": 1000.0},
        {"timestamp": _ts(20).isoformat(), "power": 1000.0},
    ]
    result = compute_device_report(rows, "dev-1", "Test Device", "metered")
    assert result.total_kwh is None
    assert result.method.startswith("insufficient")
    assert result.quality == "insufficient"


def test_bounded_gap_900s_accepts_short_intervals() -> None:
    rows = [
        {"timestamp": _ts(0).isoformat(), "power": 1000.0},
        {"timestamp": _ts(1).isoformat(), "power": 1000.0},
    ]
    result = compute_device_report(rows, "dev-1", "Test Device", "metered")
    assert result.total_kwh is not None
    assert result.total_kwh > 0.0


def test_daily_breakdown_subtracts_overlap_within_each_day() -> None:
    rows = [
        {"timestamp": _ts(0).isoformat(), "power": 9000.0, "energy_kwh": 10.0},
        {"timestamp": _ts(1).isoformat(), "power": 9000.0, "energy_kwh": 10.0},
        {"timestamp": _ts(2).isoformat(), "power": 9000.0, "energy_kwh": 10.0},
        {"timestamp": _ts(7).isoformat(), "power": 9000.0, "energy_kwh": 10.75},
    ]
    result = compute_device_report(rows, "dev-1", "Test Device", "metered")
    assert result.total_kwh is not None
    assert len(result.daily_breakdown) == 1
    day = result.daily_breakdown[0]
    assert day["energy_kwh"] is not None
    noise_floor_fallback_kwh = 9000.0 * 60.0 / 3600.0 / 1000.0
    raw_sum = noise_floor_fallback_kwh * 2 + 0.75
    assert day["energy_kwh"] < raw_sum
    assert abs(day["energy_kwh"] - 0.75) < 0.01


def test_mixed_method_counter_and_gap_exceeded() -> None:
    rows = [
        {"timestamp": _ts(0).isoformat(), "power": 9000.0, "energy_kwh": 10.0},
        {"timestamp": _ts(5).isoformat(), "power": 9000.0, "energy_kwh": 10.75},
        {"timestamp": _ts(25).isoformat(), "power": 9000.0, "energy_kwh": 11.5},
        {"timestamp": _ts(30).isoformat(), "power": 9000.0, "energy_kwh": 12.25},
    ]
    result = compute_device_report(rows, "dev-1", "Test Device", "metered")
    assert result.total_kwh is not None
    assert abs(result.total_kwh - 1.5) < 0.01
    assert result.method == "counter_integration"


def test_counter_reset_in_reporting_path_uses_fallback_no_double_count() -> None:
    rows = [
        {"timestamp": _ts(0).isoformat(), "power": 5000.0, "energy_kwh": 18.0},
        {"timestamp": _ts(10).isoformat(), "power": 5000.0, "energy_kwh": 0.05},
        {"timestamp": _ts(20).isoformat(), "power": 5000.0, "energy_kwh": 0.80},
        {"timestamp": _ts(30).isoformat(), "power": 5000.0, "energy_kwh": 1.55},
    ]
    result = compute_device_report(rows, "dev-1", "Test Device", "metered")
    assert result.total_kwh is not None
    assert result.total_kwh > 0
    reset_fallback_kwh = 5000.0 * 600.0 / 3600.0 / 1000.0
    expected_total = reset_fallback_kwh + 0.75 + 0.75
    assert abs(result.total_kwh - expected_total) < 0.01


def test_compute_device_report_empty_rows_returns_no_data() -> None:
    result = compute_device_report([], "dev-1", "Test Device", "metered")
    assert result.method == "no_data"
    assert result.quality == "insufficient"
    assert result.total_kwh is None
    assert result.error is not None
    assert "No telemetry data" in result.error


def test_compute_device_report_single_sample_returns_insufficient() -> None:
    rows = [
        {"timestamp": _ts(0).isoformat(), "power": 9000.0, "energy_kwh": 10.0},
    ]
    result = compute_device_report(rows, "dev-1", "Test Device", "metered")
    assert result.total_kwh is None
    assert result.method.startswith("insufficient")


def test_to_df_returns_empty_when_no_timestamp_or_time() -> None:
    df = _to_df([{"power": 1000.0}, {"power": 2000.0}])
    assert df.empty


def test_daily_breakdown_clean_counter_sum_matches_total() -> None:
    rows = [
        {"timestamp": _ts(0).isoformat(), "power": 9000.0, "energy_kwh": 10.0},
        {"timestamp": _ts(5).isoformat(), "power": 9000.0, "energy_kwh": 10.75},
        {"timestamp": _ts(10).isoformat(), "power": 9000.0, "energy_kwh": 11.5},
        {"timestamp": _ts(15).isoformat(), "power": 9000.0, "energy_kwh": 12.25},
    ]
    result = compute_device_report(rows, "dev-1", "Test Device", "metered")
    assert result.total_kwh is not None
    assert len(result.daily_breakdown) >= 1
    daily_sum = sum(
        d["energy_kwh"] for d in result.daily_breakdown if d["energy_kwh"] is not None
    )
    assert abs(daily_sum - result.total_kwh) < 0.01


def test_gap_exceeded_interval_contributes_zero_energy_in_valid_window() -> None:
    rows = [
        {"timestamp": _ts(0).isoformat(), "power": 9000.0, "energy_kwh": 10.0},
        {"timestamp": _ts(5).isoformat(), "power": 9000.0, "energy_kwh": 10.75},
        {"timestamp": _ts(25).isoformat(), "power": 9000.0, "energy_kwh": 11.5},
        {"timestamp": _ts(30).isoformat(), "power": 9000.0, "energy_kwh": 12.25},
    ]
    result = compute_device_report(rows, "dev-1", "Test Device", "metered")
    assert result.total_kwh is not None
    counter_only_kwh = 0.75 + 0.75
    assert abs(result.total_kwh - counter_only_kwh) < 0.01


def test_midnight_crossing_daily_sum_equals_total() -> None:
    base = datetime(2026, 5, 1, 23, 45, tzinfo=timezone.utc)
    rows = [
        {"timestamp": base.isoformat(), "power": 9000.0, "energy_kwh": 10.0},
        {"timestamp": (base + timedelta(minutes=5)).isoformat(), "power": 9000.0, "energy_kwh": 10.75},
        {"timestamp": (base + timedelta(minutes=10)).isoformat(), "power": 9000.0, "energy_kwh": 11.5},
        {"timestamp": (base + timedelta(minutes=15)).isoformat(), "power": 9000.0, "energy_kwh": 12.25},
        {"timestamp": (base + timedelta(minutes=20)).isoformat(), "power": 9000.0, "energy_kwh": 13.0},
    ]
    result = compute_device_report(rows, "dev-1", "Test Device", "metered")
    assert result.total_kwh is not None
    daily_sum = sum(
        d["energy_kwh"] for d in result.daily_breakdown if d["energy_kwh"] is not None
    )
    assert abs(daily_sum - result.total_kwh) < 0.01


def test_midnight_crossing_interval_in_later_day() -> None:
    base = datetime(2026, 5, 1, 23, 45, tzinfo=timezone.utc)
    rows = [
        {"timestamp": base.isoformat(), "power": 9000.0, "energy_kwh": 10.0},
        {"timestamp": (base + timedelta(minutes=5)).isoformat(), "power": 9000.0, "energy_kwh": 10.75},
        {"timestamp": (base + timedelta(minutes=15)).isoformat(), "power": 9000.0, "energy_kwh": 11.5},
        {"timestamp": (base + timedelta(minutes=20)).isoformat(), "power": 9000.0, "energy_kwh": 12.25},
    ]
    result = compute_device_report(rows, "dev-1", "Test Device", "metered")
    assert len(result.daily_breakdown) == 2
    may2 = [d for d in result.daily_breakdown if d["date"] == "2026-05-02"]
    assert len(may2) == 1
    assert may2[0]["energy_kwh"] is not None
    assert may2[0]["energy_kwh"] > 0


def test_all_gap_exceeded_zero_kwh_insufficient_quality() -> None:
    base = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    rows = [
        {"timestamp": base.isoformat(), "power": 1000.0},
        {"timestamp": (base + timedelta(minutes=20)).isoformat(), "power": 1000.0},
        {"timestamp": (base + timedelta(minutes=40)).isoformat(), "power": 1000.0},
    ]
    result = compute_device_report(rows, "dev-1", "Test Device", "metered")
    assert result.total_kwh is None
    assert result.method.startswith("insufficient")
    assert result.quality == "insufficient"


def test_all_gap_exceeded_daily_quality_insufficient() -> None:
    base = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    rows = [
        {"timestamp": base.isoformat(), "power": 1000.0},
        {"timestamp": (base + timedelta(minutes=20)).isoformat(), "power": 1000.0},
        {"timestamp": (base + timedelta(minutes=40)).isoformat(), "power": 1000.0},
    ]
    result = compute_device_report(rows, "dev-1", "Test Device", "metered")
    assert len(result.daily_breakdown) >= 1
    day = result.daily_breakdown[0]
    assert day["quality"] == "insufficient"
    assert day["method"] == "insufficient"


def test_gap_exceeded_does_not_inflate_total_hours() -> None:
    rows = [
        {"timestamp": _ts(0).isoformat(), "power": 9000.0, "energy_kwh": 10.0},
        {"timestamp": _ts(5).isoformat(), "power": 9000.0, "energy_kwh": 10.75},
        {"timestamp": _ts(25).isoformat(), "power": 9000.0, "energy_kwh": 11.5},
        {"timestamp": _ts(30).isoformat(), "power": 9000.0, "energy_kwh": 12.25},
    ]
    result = compute_device_report(rows, "dev-1", "Test Device", "metered")
    assert result.total_kwh is not None
    expected_hours = (5.0 + 5.0) / 60.0
    assert abs(result.total_hours - expected_hours) < 0.01
