from __future__ import annotations

from datetime import datetime, timedelta, timezone

from services.shared.telemetry_normalization import (
    INTERVAL_ENERGY_ALGORITHM_VERSION,
    compute_interval_energy_delta,
    normalize_telemetry_sample,
)


def _base_ts() -> datetime:
    return datetime(2026, 4, 5, 12, 0, tzinfo=timezone.utc)


def test_interval_energy_rejects_implausible_counter_jump_and_uses_power_fallback() -> None:
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
    assert delta.quality_class == "estimated"
    assert delta.reason_code == "fallback_measured_power"
    assert delta.counter_delta_kwh == 8.9
    assert round(delta.business_energy_delta_kwh, 4) == round(8744.0 * 20.0 / 3600.0 / 1000.0, 4)
    assert round(delta.fallback_delta_kwh or 0.0, 4) == round(delta.business_energy_delta_kwh, 4)
    assert round(delta.implied_avg_kw or 0.0, 1) == 1602.0
    assert round(delta.comparison_power_kw or 0.0, 3) == 8.744
    assert delta.coverage_seconds == 20.0
    assert delta.algorithm_version == INTERVAL_ENERGY_ALGORITHM_VERSION
    assert "counter_implausible_vs_power" in delta.quality_flags


def test_interval_energy_accepts_healthy_monotonic_counter() -> None:
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
    assert delta.quality_class == "billing_grade"
    assert delta.reason_code == "counter_accepted"
    assert delta.business_energy_delta_kwh == 0.75
    assert delta.fallback_delta_kwh == 0.75
    assert delta.elapsed_seconds == 300.0


def test_interval_energy_rejects_counter_reversal_and_uses_fallback() -> None:
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
    assert delta.counter_delta_kwh is None
    assert "counter_reverse_seen" in delta.quality_flags


def test_interval_energy_detects_reset_and_uses_fallback() -> None:
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
    assert delta.counter_delta_kwh is None
    assert "counter_reset_detected" in delta.quality_flags


def test_interval_energy_missing_counter_uses_measured_power_fallback() -> None:
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
    assert delta.quality_class == "estimated"
    assert "counter_missing" in delta.quality_flags


def test_interval_energy_missing_power_uses_vi_pf_fallback() -> None:
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
    assert round(delta.business_energy_delta_kwh, 2) == 2.07


def test_interval_energy_rejects_long_gap_when_windows_are_exceeded() -> None:
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
    assert delta.coverage_seconds == 0.0
    assert "long_gap_fallback_blocked" in delta.quality_flags


def test_interval_energy_rejects_counter_when_hard_max_is_breached() -> None:
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


def test_noise_floor_with_power_fallback_returns_fallback_energy() -> None:
    prev = normalize_telemetry_sample(
        {
            "timestamp": _base_ts().isoformat(),
            "power": 1000.0,
            "energy_kwh": 0.0,
        },
        {},
    )
    curr = normalize_telemetry_sample(
        {
            "timestamp": (_base_ts() + timedelta(seconds=60)).isoformat(),
            "power": 1000.0,
            "energy_kwh": 0.0,
        },
        {},
    )

    delta = compute_interval_energy_delta(
        prev,
        curr,
        max_counter_gap_seconds=900.0,
        max_fallback_gap_seconds=900.0,
    )

    assert delta.business_energy_delta_kwh > 0
    assert delta.energy_delta_method == "power_integration"
    assert delta.reason_code == "fallback_measured_power"
    assert "counter_noise_floor_applied" in delta.quality_flags
    assert delta.counter_delta_kwh == 0.0


def test_noise_floor_without_fallback_returns_zero() -> None:
    prev = normalize_telemetry_sample(
        {
            "timestamp": _base_ts().isoformat(),
            "energy_kwh": 0.0,
        },
        {},
    )
    curr = normalize_telemetry_sample(
        {
            "timestamp": (_base_ts() + timedelta(seconds=60)).isoformat(),
            "energy_kwh": 0.0,
        },
        {},
    )

    delta = compute_interval_energy_delta(
        prev,
        curr,
        max_counter_gap_seconds=900.0,
        max_fallback_gap_seconds=900.0,
    )

    assert delta.business_energy_delta_kwh == 0.0
    assert delta.reason_code == "counter_accepted"
    assert "counter_noise_floor_applied" in delta.quality_flags


def test_noise_floor_nonzero_counter_tiny_delta_uses_fallback() -> None:
    prev = normalize_telemetry_sample(
        {
            "timestamp": _base_ts().isoformat(),
            "power": 1000.0,
            "energy_kwh": 10.0,
        },
        {},
    )
    curr = normalize_telemetry_sample(
        {
            "timestamp": (_base_ts() + timedelta(seconds=60)).isoformat(),
            "power": 1000.0,
            "energy_kwh": 10.0005,
        },
        {},
    )

    delta = compute_interval_energy_delta(
        prev,
        curr,
        max_counter_gap_seconds=900.0,
        max_fallback_gap_seconds=900.0,
    )

    assert delta.business_energy_delta_kwh > 0
    assert "counter_noise_floor_applied" in delta.quality_flags
    assert delta.energy_delta_method == "power_integration"


def test_noise_floor_exact_zero_counter_with_power_uses_fallback() -> None:
    prev = normalize_telemetry_sample(
        {
            "timestamp": _base_ts().isoformat(),
            "power": 5000.0,
            "energy_kwh": 0.0,
        },
        {},
    )
    curr = normalize_telemetry_sample(
        {
            "timestamp": (_base_ts() + timedelta(seconds=120)).isoformat(),
            "power": 5000.0,
            "energy_kwh": 0.0,
        },
        {},
    )

    delta = compute_interval_energy_delta(
        prev,
        curr,
        max_counter_gap_seconds=900.0,
        max_fallback_gap_seconds=900.0,
    )

    assert delta.business_energy_delta_kwh > 0
    assert "counter_noise_floor_applied" in delta.quality_flags
    assert delta.energy_delta_method == "power_integration"
