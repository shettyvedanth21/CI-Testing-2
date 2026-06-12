"""Tests for feature-window aggregation and running-state classification."""

from __future__ import annotations

import math
import sys
import types
from datetime import datetime, timezone, timedelta
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

from app.services.degradation.feature_aggregator import (
    aggregate_feature_window,
    classify_running_state,
)
from app.services.degradation.types import TelemetrySample


def _ts(minutes_ago: float = 0) -> datetime:
    return datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)


def _steady_sample(
    current_avg: float = 10.0,
    power: float = 5000.0,
    power_factor: float = 0.95,
    minutes_ago: float = 0,
) -> TelemetrySample:
    return TelemetrySample(
        timestamp=_ts(minutes_ago),
        current_avg=current_avg,
        current_l1=current_avg * 1.0,
        current_l2=current_avg * 0.98,
        current_l3=current_avg * 1.02,
        power=power,
        power_factor=power_factor,
        voltage_avg=400.0,
        voltage_l1=400.0,
        voltage_l2=398.0,
        voltage_l3=402.0,
        frequency=50.0,
        energy_kwh=100.0,
    )


class TestFeatureWindowStats:
    def test_current_avg_mean_computed(self):
        samples = [
            _steady_sample(current_avg=10.0, minutes_ago=59),
            _steady_sample(current_avg=12.0, minutes_ago=30),
            _steady_sample(current_avg=11.0, minutes_ago=1),
        ]
        result = aggregate_feature_window(samples)
        assert result.window.current_avg_mean == pytest.approx(11.0)

    def test_current_avg_std_computed(self):
        samples = [
            _steady_sample(current_avg=10.0, minutes_ago=59),
            _steady_sample(current_avg=12.0, minutes_ago=30),
            _steady_sample(current_avg=11.0, minutes_ago=1),
        ]
        result = aggregate_feature_window(samples)
        assert result.window.current_avg_std is not None
        assert result.window.current_avg_std > 0

    def test_current_avg_p95_computed(self):
        samples = [_steady_sample(current_avg=float(i), minutes_ago=60 - i) for i in range(1, 21)]
        result = aggregate_feature_window(samples)
        assert result.window.current_avg_p95 is not None
        assert result.window.current_avg_p95 >= 18.0

    def test_l1_l2_l3_means_computed(self):
        samples = [_steady_sample(current_avg=10.0)]
        result = aggregate_feature_window(samples)
        assert result.window.current_l1_mean is not None
        assert result.window.current_l2_mean is not None
        assert result.window.current_l3_mean is not None

    def test_power_mean_and_p95_computed(self):
        samples = [
            _steady_sample(power=5000.0, minutes_ago=59),
            _steady_sample(power=5200.0, minutes_ago=30),
            _steady_sample(power=5100.0, minutes_ago=1),
        ]
        result = aggregate_feature_window(samples)
        assert result.window.power_mean == pytest.approx(5100.0)
        assert result.window.power_p95 is not None

    def test_power_factor_mean_computed(self):
        samples = [
            _steady_sample(power_factor=0.95, minutes_ago=59),
            _steady_sample(power_factor=0.93, minutes_ago=30),
        ]
        result = aggregate_feature_window(samples)
        assert result.window.power_factor_mean == pytest.approx(0.94)

    def test_voltage_avg_mean_computed(self):
        samples = [_steady_sample()]
        result = aggregate_feature_window(samples)
        assert result.window.voltage_avg_mean == pytest.approx(400.0)

    def test_frequency_mean_computed(self):
        samples = [_steady_sample()]
        result = aggregate_feature_window(samples)
        assert result.window.frequency_mean == pytest.approx(50.0)

    def test_energy_delta_computed(self):
        s1 = TelemetrySample(timestamp=_ts(59), energy_kwh=100.0, power=5000.0)
        s2 = TelemetrySample(timestamp=_ts(1), energy_kwh=105.0, power=5000.0)
        result = aggregate_feature_window([s1, s2])
        assert result.window.energy_kwh == pytest.approx(5.0)

    def test_sample_count(self):
        samples = [_steady_sample(minutes_ago=i) for i in range(10)]
        result = aggregate_feature_window(samples, expected_sample_count=60)
        assert result.sample_count == 10

    def test_single_sample_std_is_none(self):
        samples = [_steady_sample()]
        result = aggregate_feature_window(samples)
        assert result.window.current_avg_std is None


class TestRunningStateClassifier:
    def test_off_state(self):
        samples = [
            TelemetrySample(timestamp=_ts(i), current_avg=0.1, power=10.0)
            for i in range(5)
        ]
        assert classify_running_state(samples) == "OFF"

    def test_off_by_power(self):
        samples = [
            TelemetrySample(timestamp=_ts(i), current_avg=5.0, power=20.0)
            for i in range(5)
        ]
        assert classify_running_state(samples) == "OFF"

    def test_startup_state(self):
        currents = [0.2, 0.5, 2.0, 6.0, 10.0, 12.0]
        samples = [
            TelemetrySample(timestamp=_ts(5 - i), current_avg=c, power=c * 500)
            for i, c in enumerate(currents)
        ]
        assert classify_running_state(samples) == "STARTUP"

    def test_shutdown_state(self):
        currents = [12.0, 10.0, 6.0, 2.0, 0.5, 0.1]
        samples = [
            TelemetrySample(timestamp=_ts(5 - i), current_avg=c, power=c * 500)
            for i, c in enumerate(currents)
        ]
        assert classify_running_state(samples) == "SHUTDOWN"

    def test_steady_running_state(self):
        samples = [_steady_sample(current_avg=10.0 + i * 0.01, minutes_ago=59 - i * 6) for i in range(10)]
        assert classify_running_state(samples) == "STEADY_RUNNING"

    def test_load_change_state(self):
        currents = [10.0] * 5 + [20.0] * 5
        samples = [
            TelemetrySample(timestamp=_ts(9 - i), current_avg=c, power=c * 500)
            for i, c in enumerate(currents)
        ]
        result = classify_running_state(samples)
        assert result in ("LOAD_CHANGE", "STEADY_RUNNING")

    def test_unknown_with_too_few_samples(self):
        samples = [TelemetrySample(timestamp=_ts(0), current_avg=10.0, power=5000.0)]
        assert classify_running_state(samples) == "UNKNOWN"

    def test_unknown_with_no_data(self):
        samples = [TelemetrySample(timestamp=_ts(i)) for i in range(5)]
        assert classify_running_state(samples) == "UNKNOWN"


class TestTelemetryCoverage:
    def test_coverage_with_expected_count(self):
        samples = [_steady_sample(minutes_ago=i) for i in range(30)]
        result = aggregate_feature_window(samples, expected_sample_count=60)
        assert result.telemetry_coverage == pytest.approx(0.5)

    def test_coverage_full_when_no_expected(self):
        samples = [_steady_sample(minutes_ago=i) for i in range(30)]
        result = aggregate_feature_window(samples)
        assert result.telemetry_coverage == 1.0

    def test_coverage_clamped_to_one(self):
        samples = [_steady_sample(minutes_ago=i) for i in range(70)]
        result = aggregate_feature_window(samples, expected_sample_count=60)
        assert result.telemetry_coverage == 1.0

    def test_coverage_clamped_to_zero(self):
        samples = []
        result = aggregate_feature_window(samples, expected_sample_count=60)
        assert result.telemetry_coverage == 0.0


class TestMissingTelemetry:
    def test_missing_current_gives_none(self):
        samples = [TelemetrySample(timestamp=_ts(0), power=5000.0, power_factor=0.95)]
        result = aggregate_feature_window(samples)
        assert result.window.current_avg_mean is None
        assert result.window.power_mean is not None

    def test_missing_power_gives_none(self):
        samples = [TelemetrySample(timestamp=_ts(0), current_avg=10.0)]
        result = aggregate_feature_window(samples)
        assert result.window.power_mean is None
        assert result.window.current_avg_mean is not None

    def test_empty_samples_safe(self):
        result = aggregate_feature_window([])
        assert result.sample_count == 0
        assert result.telemetry_coverage == 0.0
        assert result.running_state == "UNKNOWN"


class TestPhaseImbalance:
    def test_phase_imbalance_computed(self):
        samples = [
            TelemetrySample(
                timestamp=_ts(0),
                current_l1=10.0,
                current_l2=9.5,
                current_l3=10.5,
                power=5000.0,
            )
        ]
        result = aggregate_feature_window(samples)
        assert result.window.phase_imbalance is not None
        assert result.window.phase_imbalance > 0

    def test_phase_imbalance_zero_for_balanced(self):
        samples = [
            TelemetrySample(
                timestamp=_ts(0),
                current_l1=10.0,
                current_l2=10.0,
                current_l3=10.0,
                power=5000.0,
            )
        ]
        result = aggregate_feature_window(samples)
        assert result.window.phase_imbalance == pytest.approx(0.0)

    def test_phase_imbalance_none_for_missing_phases(self):
        samples = [
            TelemetrySample(timestamp=_ts(0), current_avg=10.0, power=5000.0)
        ]
        result = aggregate_feature_window(samples)
        assert result.window.phase_imbalance is None

    def test_phase_imbalance_with_two_phases(self):
        samples = [
            TelemetrySample(
                timestamp=_ts(0),
                current_l1=10.0,
                current_l2=11.0,
                power=5000.0,
            )
        ]
        result = aggregate_feature_window(samples)
        assert result.window.phase_imbalance is not None


class TestNoNaNorInfinity:
    def test_zero_currents_no_nan(self):
        samples = [
            TelemetrySample(
                timestamp=_ts(0),
                current_avg=0.0,
                current_l1=0.0,
                current_l2=0.0,
                current_l3=0.0,
                power=0.0,
            )
            for _ in range(3)
        ]
        result = aggregate_feature_window(samples)
        for field in (
            "current_avg_mean", "current_avg_std", "current_avg_p95",
            "power_mean", "power_p95", "phase_imbalance",
            "voltage_imbalance", "frequency_mean",
        ):
            val = getattr(result.window, field)
            if val is not None:
                assert math.isfinite(val), f"{field} is not finite: {val}"

    def test_single_value_std_none_not_nan(self):
        samples = [_steady_sample()]
        result = aggregate_feature_window(samples)
        assert result.window.current_avg_std is None

    def test_nan_input_treated_as_missing(self):
        samples = [
            TelemetrySample(timestamp=_ts(0), current_avg=float("nan"), power=5000.0),
        ]
        result = aggregate_feature_window(samples)
        assert result.window.current_avg_mean is None
        assert result.window.power_mean is not None


class TestWindowTimestamps:
    def test_window_start_end_carried_through(self):
        samples = [_steady_sample(minutes_ago=i) for i in range(5)]
        start = datetime(2026, 5, 21, 10, 0, tzinfo=timezone.utc)
        end = datetime(2026, 5, 21, 11, 0, tzinfo=timezone.utc)
        result = aggregate_feature_window(samples, window_start=start, window_end=end)
        assert result.window_start == start
        assert result.window_end == end

    def test_window_timestamps_none_by_default(self):
        samples = [_steady_sample(minutes_ago=i) for i in range(5)]
        result = aggregate_feature_window(samples)
        assert result.window_start is None
        assert result.window_end is None

    def test_empty_samples_with_timestamps(self):
        start = datetime(2026, 5, 21, 10, 0, tzinfo=timezone.utc)
        end = datetime(2026, 5, 21, 11, 0, tzinfo=timezone.utc)
        result = aggregate_feature_window([], window_start=start, window_end=end)
        assert result.window_start == start
        assert result.window_end == end
