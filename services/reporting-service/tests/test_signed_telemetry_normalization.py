from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import csv

from services.shared.telemetry_normalization import (
    compute_interval_energy_delta,
    normalize_telemetry_sample,
)
from src.services.demand_engine import calculate_demand
from src.services.energy_engine import calculate_energy
from src.services.report_engine import _compute_from_df, _to_df, compute_device_report


REPO_ROOT = Path(__file__).resolve().parents[3]
TRUTH_PROOF_CSV = REPO_ROOT / "implementation-docs" / "Dataset" / "ad00000001_apr20.csv"


def _base_ts() -> datetime:
    return datetime(2026, 4, 5, 12, 0, tzinfo=timezone.utc)


def test_normalize_consumption_only_normal_clamps_negative_business_power():
    sample = normalize_telemetry_sample(
        {
            "timestamp": _base_ts().isoformat(),
            "power": -500.0,
            "power_factor": -0.8,
            "current": -2.0,
            "voltage": 230.0,
        },
        {"energy_flow_mode": "consumption_only", "polarity_mode": "normal"},
    )

    assert sample.net_power_w == -500.0
    assert sample.business_power_w == 0.0
    assert sample.export_power_w == 0.0
    assert sample.pf_signed == -0.8
    assert sample.pf_business == 0.8
    assert sample.current_a == 2.0
    assert "signed_power_seen" in sample.quality_flags
    assert "signed_pf_seen" in sample.quality_flags


def test_normalize_consumption_only_inverted_restores_positive_business_power():
    sample = normalize_telemetry_sample(
        {
            "timestamp": _base_ts().isoformat(),
            "power": -1500.0,
            "power_factor": -0.9,
            "current": -6.0,
            "voltage": 230.0,
        },
        {"energy_flow_mode": "consumption_only", "polarity_mode": "inverted"},
    )

    assert sample.net_power_w == 1500.0
    assert sample.business_power_w == 1500.0
    assert sample.export_power_w == 0.0
    assert sample.pf_signed == 0.9
    assert sample.pf_business == 0.9


def test_normalize_bidirectional_normal_tracks_export_separately():
    sample = normalize_telemetry_sample(
        {
            "timestamp": _base_ts().isoformat(),
            "active_power": -2200.0,
            "power_factor": -0.95,
        },
        {"energy_flow_mode": "bidirectional", "polarity_mode": "normal"},
    )

    assert sample.net_power_w == -2200.0
    assert sample.import_power_w == 0.0
    assert sample.export_power_w == 2200.0
    assert sample.business_power_w == 0.0


def test_active_power_alias_conflict_is_flagged_and_active_power_wins():
    sample = normalize_telemetry_sample(
        {
            "timestamp": _base_ts().isoformat(),
            "active_power_kw": 2.0,
            "power": 500.0,
        },
        {},
    )

    assert sample.raw_active_power_w == 2000.0
    assert sample.raw_source_power_field == "active_power_kw"
    assert "active_power_alias_used" in sample.quality_flags
    assert "active_power_conflict" in sample.quality_flags


def test_phase_diagnostics_do_not_override_authoritative_aggregate_business_fields():
    sample = normalize_telemetry_sample(
        {
            "timestamp": _base_ts().isoformat(),
            "current": 10.0,
            "voltage": 230.0,
            "current_l1": 999.0,
            "current_l2": 0.0,
            "current_l3": -999.0,
            "voltage_l1": 500.0,
            "voltage_l2": 0.0,
            "voltage_l3": -100.0,
            "power_factor": 0.9,
        },
        {},
    )

    assert sample.current_a == 10.0
    assert sample.voltage_v == 230.0
    assert sample.pf_business == 0.9
    assert "phase_diagnostics_present" in sample.quality_flags

    previous = normalize_telemetry_sample(
        {
            "timestamp": _base_ts().isoformat(),
            "current": 10.0,
            "voltage": 230.0,
            "current_l1": 1.0,
            "voltage_l1": 1.0,
            "power_factor": 0.9,
        },
        {},
    )
    current = normalize_telemetry_sample(
        {
            "timestamp": (_base_ts() + timedelta(hours=1)).isoformat(),
            "current": 10.0,
            "voltage": 230.0,
            "current_l1": 9999.0,
            "voltage_l1": 9999.0,
            "power_factor": 0.9,
        },
        {},
    )
    delta = compute_interval_energy_delta(previous, current, max_fallback_gap_seconds=7200.0)

    assert round(delta.business_energy_delta_kwh, 2) == 2.07
    assert delta.energy_delta_method == "derived_vi_pf"


def test_missing_aggregate_current_voltage_are_not_derived_from_phase_diagnostics():
    sample = normalize_telemetry_sample(
        {
            "timestamp": _base_ts().isoformat(),
            "current_l1": 10.0,
            "current_l2": 11.0,
            "current_l3": 12.0,
            "voltage_l1": 230.0,
            "voltage_l2": 231.0,
            "voltage_l3": 229.0,
        },
        {},
    )

    assert sample.current_a is None
    assert sample.voltage_v is None
    assert sample.business_power_w == 0.0
    assert "phase_diagnostics_present" in sample.quality_flags

    next_sample = normalize_telemetry_sample(
        {
            "timestamp": (_base_ts() + timedelta(hours=1)).isoformat(),
            "current_l1": 20.0,
            "current_l2": 21.0,
            "current_l3": 22.0,
            "voltage_l1": 240.0,
            "voltage_l2": 241.0,
            "voltage_l3": 239.0,
        },
        {},
    )
    delta = compute_interval_energy_delta(sample, next_sample, max_fallback_gap_seconds=7200.0)

    assert delta.business_energy_delta_kwh == 0.0
    assert delta.energy_delta_method == "none"
    assert "insufficient_power_for_fallback" in delta.quality_flags


def test_interval_energy_uses_normalized_business_power():
    prev = normalize_telemetry_sample(
        {"timestamp": _base_ts().isoformat(), "power": -1000.0},
        {"energy_flow_mode": "consumption_only", "polarity_mode": "inverted"},
    )
    curr = normalize_telemetry_sample(
        {"timestamp": (_base_ts() + timedelta(minutes=30)).isoformat(), "power": -1000.0},
        {"energy_flow_mode": "consumption_only", "polarity_mode": "inverted"},
    )

    delta = compute_interval_energy_delta(prev, curr, max_fallback_gap_seconds=3600.0)

    assert round(delta.business_energy_delta_kwh, 4) == 0.5
    assert delta.energy_delta_method == "power_integration"
    assert delta.reason_code == "fallback_measured_power"
    assert delta.quality_class == "estimated"


def test_interval_energy_uses_power_fallback_for_flat_counter_with_positive_power():
    prev = normalize_telemetry_sample(
        {"timestamp": _base_ts().isoformat(), "power": 67.5, "energy_kwh": 7.4},
        {},
    )
    curr = normalize_telemetry_sample(
        {"timestamp": (_base_ts() + timedelta(seconds=30)).isoformat(), "power": 67.5, "energy_kwh": 7.4},
        {},
    )

    delta = compute_interval_energy_delta(
        prev,
        curr,
        max_counter_gap_seconds=300.0,
        max_fallback_gap_seconds=300.0,
    )

    assert delta.energy_delta_method == "power_integration"
    assert delta.reason_code == "fallback_measured_power"
    assert delta.quality_class == "estimated"
    assert delta.counter_delta_kwh == 0.0
    assert round(delta.business_energy_delta_kwh, 6) == round(67.5 * 30.0 / 3600.0 / 1000.0, 6)
    assert "counter_noise_floor_applied" in delta.quality_flags


def test_interval_energy_uses_vi_fallback_for_flat_counter_with_positive_vi_signal():
    prev = normalize_telemetry_sample(
        {
            "timestamp": _base_ts().isoformat(),
            "current": 0.1,
            "voltage": 243.0,
            "power_factor": 0.92,
            "energy_kwh": 7.4,
        },
        {},
    )
    curr = normalize_telemetry_sample(
        {
            "timestamp": (_base_ts() + timedelta(seconds=30)).isoformat(),
            "current": 0.1,
            "voltage": 243.0,
            "power_factor": 0.92,
            "energy_kwh": 7.4,
        },
        {},
    )

    delta = compute_interval_energy_delta(
        prev,
        curr,
        max_counter_gap_seconds=300.0,
        max_fallback_gap_seconds=300.0,
    )

    assert delta.energy_delta_method == "derived_vi_pf"
    assert delta.reason_code == "fallback_vipf"
    assert delta.quality_class == "estimated"
    assert delta.counter_delta_kwh == 0.0
    assert round(delta.business_energy_delta_kwh, 6) == round((0.1 * 243.0 * 0.92) * 30.0 / 3600.0 / 1000.0, 6)
    assert "counter_noise_floor_applied" in delta.quality_flags
    assert "power_derived_from_vi_pf" in delta.quality_flags


def test_interval_energy_keeps_zero_energy_for_flat_counter_without_fallback_inputs():
    prev = normalize_telemetry_sample(
        {"timestamp": _base_ts().isoformat(), "energy_kwh": 7.4},
        {},
    )
    curr = normalize_telemetry_sample(
        {"timestamp": (_base_ts() + timedelta(seconds=30)).isoformat(), "energy_kwh": 7.4},
        {},
    )

    delta = compute_interval_energy_delta(
        prev,
        curr,
        max_counter_gap_seconds=300.0,
        max_fallback_gap_seconds=300.0,
    )

    assert delta.energy_delta_method == "counter"
    assert delta.reason_code == "counter_accepted"
    assert delta.quality_class == "billing_grade"
    assert delta.business_energy_delta_kwh == 0.0
    assert delta.counter_delta_kwh == 0.0
    assert "counter_noise_floor_applied" in delta.quality_flags


def test_interval_energy_rejects_implausible_counter_jump_and_uses_power_fallback():
    prev = normalize_telemetry_sample(
        {
            "timestamp": _base_ts().isoformat(),
            "power": 8744.0,
            "energy_kwh": 0.0,
        },
        {},
    )
    curr = normalize_telemetry_sample(
        {
            "timestamp": (_base_ts() + timedelta(seconds=20)).isoformat(),
            "power": 8744.0,
            "energy_kwh": 8.9,
        },
        {},
    )

    delta = compute_interval_energy_delta(
        prev,
        curr,
        max_counter_gap_seconds=300.0,
        max_fallback_gap_seconds=300.0,
    )

    assert delta.energy_delta_method == "power_integration"
    assert delta.reason_code == "fallback_measured_power"
    assert delta.quality_class == "estimated"
    assert delta.counter_delta_kwh == 8.9
    assert round(delta.business_energy_delta_kwh, 4) == round(8744.0 * 20.0 / 3600.0 / 1000.0, 4)
    assert round(delta.fallback_delta_kwh or 0.0, 4) == round(delta.business_energy_delta_kwh, 4)
    assert round(delta.implied_avg_kw or 0.0, 1) == 1602.0
    assert round(delta.comparison_power_kw or 0.0, 3) == 8.744
    assert "counter_implausible_vs_power" in delta.quality_flags


def test_interval_energy_accepts_healthy_monotonic_counter():
    prev = normalize_telemetry_sample(
        {
            "timestamp": _base_ts().isoformat(),
            "power": 9000.0,
            "energy_kwh": 10.0,
        },
        {},
    )
    curr = normalize_telemetry_sample(
        {
            "timestamp": (_base_ts() + timedelta(minutes=5)).isoformat(),
            "power": 9000.0,
            "energy_kwh": 10.75,
        },
        {},
    )

    delta = compute_interval_energy_delta(
        prev,
        curr,
        max_counter_gap_seconds=900.0,
        max_fallback_gap_seconds=900.0,
    )

    assert delta.energy_delta_method == "counter"
    assert delta.reason_code == "counter_accepted"
    assert delta.quality_class == "billing_grade"
    assert delta.business_energy_delta_kwh == 0.75
    assert delta.fallback_delta_kwh == 0.75


def test_interval_energy_rejects_counter_reversal_and_uses_fallback():
    prev = normalize_telemetry_sample(
        {
            "timestamp": _base_ts().isoformat(),
            "power": 3600.0,
            "energy_kwh": 12.0,
        },
        {},
    )
    curr = normalize_telemetry_sample(
        {
            "timestamp": (_base_ts() + timedelta(minutes=10)).isoformat(),
            "power": 3600.0,
            "energy_kwh": 11.8,
        },
        {},
    )

    delta = compute_interval_energy_delta(
        prev,
        curr,
        max_counter_gap_seconds=900.0,
        max_fallback_gap_seconds=900.0,
    )

    assert delta.energy_delta_method == "power_integration"
    assert delta.reason_code == "fallback_measured_power"
    assert "counter_reverse_seen" in delta.quality_flags
    assert delta.counter_delta_kwh is None


def test_interval_energy_detects_reset_and_uses_fallback():
    prev = normalize_telemetry_sample(
        {
            "timestamp": _base_ts().isoformat(),
            "power": 5000.0,
            "energy_kwh": 18.0,
        },
        {},
    )
    curr = normalize_telemetry_sample(
        {
            "timestamp": (_base_ts() + timedelta(minutes=10)).isoformat(),
            "power": 5000.0,
            "energy_kwh": 0.05,
        },
        {},
    )

    delta = compute_interval_energy_delta(
        prev,
        curr,
        max_counter_gap_seconds=900.0,
        max_fallback_gap_seconds=900.0,
    )

    assert delta.energy_delta_method == "power_integration"
    assert delta.reason_code == "fallback_measured_power"
    assert "counter_reset_detected" in delta.quality_flags
    assert delta.counter_delta_kwh is None


def test_interval_energy_missing_counter_uses_measured_power_fallback():
    prev = normalize_telemetry_sample(
        {"timestamp": _base_ts().isoformat(), "power": 7200.0},
        {},
    )
    curr = normalize_telemetry_sample(
        {"timestamp": (_base_ts() + timedelta(minutes=15)).isoformat(), "power": 7200.0},
        {},
    )

    delta = compute_interval_energy_delta(
        prev,
        curr,
        max_counter_gap_seconds=3600.0,
        max_fallback_gap_seconds=3600.0,
    )

    assert delta.energy_delta_method == "power_integration"
    assert delta.reason_code == "fallback_measured_power"
    assert "counter_missing" in delta.quality_flags


def test_interval_energy_missing_power_uses_vi_pf_fallback():
    prev = normalize_telemetry_sample(
        {
            "timestamp": _base_ts().isoformat(),
            "current": 10.0,
            "voltage": 230.0,
            "power_factor": 0.9,
        },
        {},
    )
    curr = normalize_telemetry_sample(
        {
            "timestamp": (_base_ts() + timedelta(hours=1)).isoformat(),
            "current": 10.0,
            "voltage": 230.0,
            "power_factor": 0.9,
        },
        {},
    )

    delta = compute_interval_energy_delta(
        prev,
        curr,
        max_counter_gap_seconds=7200.0,
        max_fallback_gap_seconds=7200.0,
    )

    assert delta.energy_delta_method == "derived_vi_pf"
    assert delta.reason_code == "fallback_vipf"
    assert delta.quality_class == "estimated"


def test_interval_energy_rejects_long_gap_when_counter_and_fallback_windows_are_exceeded():
    prev = normalize_telemetry_sample(
        {"timestamp": _base_ts().isoformat(), "power": 1000.0, "energy_kwh": 1.0},
        {},
    )
    curr = normalize_telemetry_sample(
        {"timestamp": (_base_ts() + timedelta(hours=1)).isoformat(), "power": 1000.0, "energy_kwh": 2.0},
        {},
    )

    delta = compute_interval_energy_delta(
        prev,
        curr,
        max_counter_gap_seconds=120.0,
        max_fallback_gap_seconds=120.0,
    )

    assert delta.energy_delta_method == "none"
    assert delta.reason_code == "fallback_gap_exceeded"
    assert delta.quality_class == "gap_exceeded"
    assert "long_gap_fallback_blocked" in delta.quality_flags


def test_interval_energy_rejects_counter_when_hard_max_is_breached():
    prev = normalize_telemetry_sample(
        {"timestamp": _base_ts().isoformat(), "power": 1500.0, "energy_kwh": 0.0},
        {},
    )
    curr = normalize_telemetry_sample(
        {"timestamp": (_base_ts() + timedelta(minutes=1)).isoformat(), "power": 1500.0, "energy_kwh": 5.0},
        {},
    )

    delta = compute_interval_energy_delta(prev, curr, hard_max_kw=100.0)

    assert delta.energy_delta_method == "power_integration"
    assert delta.reason_code == "fallback_measured_power"
    assert "counter_implausible_hard_max" in delta.quality_flags


def test_report_uses_normalized_business_power_for_peak_and_load_metrics():
    ts = _base_ts()
    rows = [
        {"timestamp": ts, "power": -1200.0, "power_factor": -0.9, "current": -5.0, "voltage": 230.0},
        {"timestamp": ts + timedelta(minutes=30), "power": -1200.0, "power_factor": -0.9, "current": -5.0, "voltage": 230.0},
        {"timestamp": ts + timedelta(minutes=60), "power": -1200.0, "power_factor": -0.9, "current": -5.0, "voltage": 230.0},
    ]

    result = compute_device_report(
        rows=rows,
        device_id="D-1",
        device_name="Machine 1",
        data_source_type="metered",
        device_power_config={"energy_flow_mode": "consumption_only", "polarity_mode": "inverted"},
    )

    assert result.total_kwh is not None and result.total_kwh > 0
    assert result.peak_demand_kw == 1.2
    assert result.average_load_kw is not None and result.average_load_kw > 0
    assert result.load_factor_pct is not None and result.load_factor_pct >= 0


def test_report_derives_peak_from_normalized_business_power_when_active_power_missing():
    ts = _base_ts()
    rows = [
        {"timestamp": ts, "current": 10.0, "voltage": 230.0, "power_factor": 0.9},
        {"timestamp": ts + timedelta(hours=24), "current": 10.0, "voltage": 230.0, "power_factor": 0.9},
        {"timestamp": ts + timedelta(hours=48), "current": 10.0, "voltage": 230.0, "power_factor": 0.9},
    ]

    result = compute_device_report(
        rows=rows,
        device_id="D-2",
        device_name="Machine 2",
        data_source_type="metered",
        device_power_config={"energy_flow_mode": "consumption_only", "polarity_mode": "normal"},
    )

    assert result.total_kwh == 99.36
    assert result.peak_demand_kw == 2.07
    assert result.average_load_kw == 2.07
    assert result.load_factor_pct == 100.0


def test_report_kpis_ignore_extreme_phase_diagnostic_values():
    ts = _base_ts()
    base_rows = [
        {"timestamp": ts, "current": 10.0, "voltage": 230.0, "power_factor": 0.9},
        {"timestamp": ts + timedelta(hours=24), "current": 10.0, "voltage": 230.0, "power_factor": 0.9},
        {"timestamp": ts + timedelta(hours=48), "current": 10.0, "voltage": 230.0, "power_factor": 0.9},
    ]
    diagnostic_rows = [
        {
            **row,
            "current_l1": 9999.0,
            "current_l2": -9999.0,
            "current_l3": 0.0,
            "voltage_l1": 9999.0,
            "voltage_l2": -9999.0,
            "voltage_l3": 0.0,
        }
        for row in base_rows
    ]

    baseline = compute_device_report(
        rows=base_rows,
        device_id="D-PHASE-BASE",
        device_name="Machine Base",
        data_source_type="metered",
        device_power_config={"energy_flow_mode": "consumption_only", "polarity_mode": "normal"},
    )
    with_phase = compute_device_report(
        rows=diagnostic_rows,
        device_id="D-PHASE-DIAG",
        device_name="Machine Diagnostics",
        data_source_type="metered",
        device_power_config={"energy_flow_mode": "consumption_only", "polarity_mode": "normal"},
    )

    assert with_phase.total_kwh == baseline.total_kwh == 99.36
    assert with_phase.peak_demand_kw == baseline.peak_demand_kw == 2.07
    assert with_phase.average_load_kw == baseline.average_load_kw == 2.07
    assert with_phase.load_factor_pct == baseline.load_factor_pct == 100.0


def test_report_uses_same_normalized_basis_for_simulator_and_replay_daily_kpis():
    ts = _base_ts()
    simulator_rows = [
        {"timestamp": ts, "power": 1200.0, "power_factor": 0.9, "current": 5.0, "voltage": 230.0},
        {"timestamp": ts + timedelta(minutes=30), "power": 1200.0, "power_factor": 0.9, "current": 5.0, "voltage": 230.0},
        {"timestamp": ts + timedelta(minutes=60), "power": 1200.0, "power_factor": 0.9, "current": 5.0, "voltage": 230.0},
    ]
    replay_rows = [
        {"timestamp": ts, "power": -1200.0, "power_factor": -0.9, "current": -5.0, "voltage": 230.0},
        {"timestamp": ts + timedelta(minutes=30), "power": -1200.0, "power_factor": -0.9, "current": -5.0, "voltage": 230.0},
        {"timestamp": ts + timedelta(minutes=60), "power": -1200.0, "power_factor": -0.9, "current": -5.0, "voltage": 230.0},
    ]

    simulator_result = compute_device_report(
        rows=simulator_rows,
        device_id="SIM-1",
        device_name="Simulator Machine",
        data_source_type="simulator",
        device_power_config={"energy_flow_mode": "consumption_only", "polarity_mode": "normal"},
    )
    replay_result = compute_device_report(
        rows=replay_rows,
        device_id="CSV-1",
        device_name="Replay Machine",
        data_source_type="metered",
        device_power_config={"energy_flow_mode": "consumption_only", "polarity_mode": "inverted"},
    )

    assert simulator_result.total_kwh == 1.2
    assert replay_result.total_kwh == simulator_result.total_kwh
    assert simulator_result.peak_demand_kw == 1.2
    assert replay_result.peak_demand_kw == simulator_result.peak_demand_kw
    assert simulator_result.average_load_kw == 1.2
    assert replay_result.average_load_kw == simulator_result.average_load_kw
    assert simulator_result.load_factor_pct == 100.0
    assert replay_result.load_factor_pct == simulator_result.load_factor_pct
    assert simulator_result.daily_breakdown == replay_result.daily_breakdown
    assert simulator_result.daily_breakdown[0]["peak_demand_kw"] == 1.2
    assert simulator_result.daily_breakdown[0]["average_load_kw"] == 1.2


def test_compute_from_df_normalizes_raw_rows_before_kpi_computation():
    ts = _base_ts()
    raw_df = _to_df(
        [
            {"timestamp": ts, "power": -1200.0, "power_factor": -0.9, "current": -5.0, "voltage": 230.0},
            {"timestamp": ts + timedelta(minutes=30), "power": -1200.0, "power_factor": -0.9, "current": -5.0, "voltage": 230.0},
            {"timestamp": ts + timedelta(minutes=60), "power": -1200.0, "power_factor": -0.9, "current": -5.0, "voltage": 230.0},
        ]
    )

    result = _compute_from_df(
        raw_df,
        device_id="RAW-1",
        device_name="Raw Replay Machine",
        data_source_type="metered",
        device_power_config={"energy_flow_mode": "consumption_only", "polarity_mode": "inverted"},
    )

    assert result.total_kwh == 1.2
    assert result.peak_demand_kw == 1.2
    assert result.average_load_kw == 1.2
    assert result.load_factor_pct == 100.0
    assert result.daily_breakdown[0]["peak_demand_kw"] == 1.2


def test_comparison_energy_series_uses_canonical_business_power_when_active_power_missing():
    ts = _base_ts()
    rows = [
        {"timestamp": ts, "current": 10.0, "voltage": 230.0, "power_factor": 0.9},
        {"timestamp": ts + timedelta(minutes=30), "current": 10.0, "voltage": 230.0, "power_factor": 0.9},
    ]

    energy = calculate_energy(
        rows,
        "single",
        {"energy_flow_mode": "consumption_only", "polarity_mode": "normal"},
    )

    assert energy["success"] is True
    assert energy["data"]["total_kwh"] == 1.03
    assert energy["data"]["peak_power_w"] == 2070.0
    assert energy["data"]["power_series"][0]["power_w"] == 2070.0

    demand = calculate_demand(energy["data"]["power_series"], 15)
    assert demand["success"] is True
    assert demand["data"]["peak_demand_kw"] == 2.07


def test_calculate_energy_uses_same_physical_basis_as_consumption_path_for_truth_proof_csv():
    rows = []
    assert TRUTH_PROOF_CSV.exists(), f"Missing truth-proof CSV fixture: {TRUTH_PROOF_CSV}"
    for row in csv.DictReader(TRUTH_PROOF_CSV.open().readlines()[3:]):
        rows.append(
            {
                "timestamp": row["_time"],
                "power": float(row["power"]),
                "energy_kwh": float(row["energy_kwh"]),
                "current": float(row["current"]),
                "voltage": float(row["voltage"]),
                "power_factor": float(row["power_factor"]),
            }
        )

    energy = calculate_energy(
        rows,
        "single",
        {"energy_flow_mode": "consumption_only", "polarity_mode": "normal"},
    )
    report = compute_device_report(
        rows=rows,
        device_id="AD00000001",
        device_name="AD00000001",
        data_source_type="metered",
        device_power_config={"energy_flow_mode": "consumption_only", "polarity_mode": "normal"},
    )

    assert energy["success"] is True
    assert energy["data"]["energy_basis"] == "normalized_telemetry"
    assert energy["data"]["computation_mode"] == "normalized_business_power"
    assert energy["data"]["total_kwh"] == 1.4
    assert report.total_kwh == 1.3992
    assert abs(energy["data"]["total_kwh"] - report.total_kwh) <= 0.01


def test_calculate_energy_truth_proof_csv_rejects_first_counter_jump_but_keeps_full_window_total():
    rows = []
    assert TRUTH_PROOF_CSV.exists(), f"Missing truth-proof CSV fixture: {TRUTH_PROOF_CSV}"
    for row in csv.DictReader(TRUTH_PROOF_CSV.open().readlines()[3:]):
        rows.append(
            {
                "timestamp": row["_time"],
                "power": float(row["power"]),
                "energy_kwh": float(row["energy_kwh"]),
                "current": float(row["current"]),
                "voltage": float(row["voltage"]),
                "power_factor": float(row["power_factor"]),
            }
        )

    first = normalize_telemetry_sample(rows[0], {})
    second = normalize_telemetry_sample(rows[1], {})
    first_interval = compute_interval_energy_delta(first, second)
    energy = calculate_energy(rows, "single", {"energy_flow_mode": "consumption_only", "polarity_mode": "normal"})

    assert first_interval.energy_delta_method == "power_integration"
    assert round(first_interval.business_energy_delta_kwh, 4) == 0.0486
    assert energy["data"]["total_kwh"] == 1.4
    assert energy["data"]["total_kwh"] != 10.2
    assert energy["data"]["total_kwh"] != 0.05
