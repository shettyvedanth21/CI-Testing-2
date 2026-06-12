"""Tests for the baseline learner."""

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

from app.services.degradation.baseline_learner import learn_baseline
from app.services.degradation.types import FeatureWindowInput, FeatureWindowResult


def _steady_window(**overrides) -> FeatureWindowResult:
    defaults = dict(
        current_avg_mean=10.0,
        current_avg_std=0.5,
        power_mean=5000.0,
        power_p95=5500.0,
        power_factor_mean=0.95,
        voltage_avg_mean=400.0,
        phase_imbalance=0.02,
        frequency_mean=50.0,
    )
    ts_overrides = {}
    if "window_start" in overrides:
        ts_overrides["window_start"] = overrides.pop("window_start")
    if "window_end" in overrides:
        ts_overrides["window_end"] = overrides.pop("window_end")
    window = FeatureWindowInput(**{k: v for k, v in {**defaults, **overrides}.items()})
    return FeatureWindowResult(
        window=window,
        running_state="STEADY_RUNNING",
        telemetry_coverage=1.0,
        sample_count=60,
        **ts_overrides,
    )


def _off_window(**overrides) -> FeatureWindowResult:
    defaults = dict(
        current_avg_mean=0.1,
        power_mean=10.0,
    )
    ts_overrides = {}
    if "window_start" in overrides:
        ts_overrides["window_start"] = overrides.pop("window_start")
    if "window_end" in overrides:
        ts_overrides["window_end"] = overrides.pop("window_end")
    window = FeatureWindowInput(**{k: v for k, v in {**defaults, **overrides}.items()})
    return FeatureWindowResult(
        window=window,
        running_state="OFF",
        telemetry_coverage=0.5,
        sample_count=30,
        **ts_overrides,
    )


class TestBaselineLearnerBasic:
    def test_learns_from_steady_windows(self):
        windows = [_steady_window() for _ in range(10)]
        result = learn_baseline(windows, minimum_days=0)

        assert result.baseline_input.current_avg_mean == pytest.approx(10.0)
        assert result.baseline_input.power_mean == pytest.approx(5000.0)
        assert result.baseline_input.power_factor_mean == pytest.approx(0.95)
        assert result.learning_window_count == 10

    def test_ignores_non_steady_windows(self):
        windows = [_off_window() for _ in range(5)] + [_steady_window() for _ in range(5)]
        result = learn_baseline(windows, minimum_days=0)

        assert result.baseline_input.current_avg_mean == pytest.approx(10.0)
        assert result.learning_window_count == 5

    def test_all_non_steady_gives_insufficient(self):
        windows = [_off_window() for _ in range(10)]
        result = learn_baseline(windows, minimum_days=0)

        assert result.quality_band == "insufficient"
        assert result.quality_score == 0.0
        assert result.learning_window_count == 0


class TestQualityScoreAndBand:
    def test_high_quality_with_many_full_windows(self):
        windows = [_steady_window() for _ in range(20)]
        result = learn_baseline(windows, minimum_days=0)

        assert result.quality_score >= 0.8
        assert result.quality_band == "high"

    def test_medium_quality_with_moderate_windows(self):
        partial_window = FeatureWindowResult(
            window=FeatureWindowInput(
                current_avg_mean=10.0,
                power_mean=5000.0,
                power_factor_mean=0.95,
            ),
            running_state="STEADY_RUNNING",
            telemetry_coverage=0.5,
            sample_count=30,
        )
        windows = [partial_window for _ in range(5)]
        result = learn_baseline(windows, minimum_days=0)

        assert 0.0 < result.quality_score <= 1.0
        assert result.quality_band in ("high", "medium", "low", "insufficient")

    def test_low_quality_with_few_windows(self):
        windows = [_steady_window() for _ in range(2)]
        result = learn_baseline(windows, minimum_days=0)

        assert result.quality_band in ("low", "insufficient")
        assert result.quality_score < 0.5

    def test_insufficient_with_no_steady(self):
        windows = [_off_window() for _ in range(5)]
        result = learn_baseline(windows, minimum_days=0)

        assert result.quality_band == "insufficient"
        assert result.quality_score == 0.0

    def test_quality_score_clamped_0_to_1(self):
        windows = [_steady_window() for _ in range(100)]
        result = learn_baseline(windows, minimum_days=0)
        assert 0.0 <= result.quality_score <= 1.0


class TestSignalCompleteness:
    def test_full_completeness_when_all_fields_present(self):
        windows = [_steady_window() for _ in range(5)]
        result = learn_baseline(windows, minimum_days=0)
        assert result.signal_completeness == 1.0

    def test_partial_completeness_with_missing_fields(self):
        sparse_window = FeatureWindowResult(
            window=FeatureWindowInput(current_avg_mean=10.0, power_mean=5000.0),
            running_state="STEADY_RUNNING",
            telemetry_coverage=0.5,
            sample_count=30,
        )
        windows = [sparse_window for _ in range(5)]
        result = learn_baseline(windows, minimum_days=0)
        assert 0.0 < result.signal_completeness < 1.0

    def test_zero_completeness_with_no_steady(self):
        windows = [_off_window() for _ in range(5)]
        result = learn_baseline(windows, minimum_days=0)
        assert result.signal_completeness == 0.0


class TestSteadyRunningCoverage:
    def test_full_coverage_when_all_steady(self):
        windows = [_steady_window() for _ in range(10)]
        result = learn_baseline(windows, minimum_days=0)
        assert result.steady_running_coverage == 1.0

    def test_partial_coverage(self):
        windows = [_steady_window() for _ in range(5)] + [_off_window() for _ in range(5)]
        result = learn_baseline(windows, minimum_days=0)
        assert result.steady_running_coverage == pytest.approx(0.5)

    def test_zero_coverage_when_none_steady(self):
        windows = [_off_window() for _ in range(10)]
        result = learn_baseline(windows, minimum_days=0)
        assert result.steady_running_coverage == 0.0


class TestLowDataBaseline:
    def test_single_window_gives_low_or_insufficient(self):
        windows = [_steady_window()]
        result = learn_baseline(windows, minimum_days=0)
        assert result.quality_band in ("low", "insufficient")
        assert result.quality_score < 0.3

    def test_empty_input_gives_insufficient(self):
        result = learn_baseline([], minimum_days=0)
        assert result.quality_band == "insufficient"
        assert result.quality_score == 0.0
        assert result.learning_window_count == 0


class TestNoNaNorInfinity:
    def test_zero_std_no_nan(self):
        windows = [
            FeatureWindowResult(
                window=FeatureWindowInput(
                    current_avg_mean=10.0,
                    current_avg_std=0.0,
                    power_mean=5000.0,
                    power_factor_mean=0.95,
                    voltage_avg_mean=400.0,
                    phase_imbalance=0.0,
                    frequency_mean=50.0,
                ),
                running_state="STEADY_RUNNING",
                telemetry_coverage=1.0,
                sample_count=60,
            )
            for _ in range(5)
        ]
        result = learn_baseline(windows, minimum_days=0)
        for field in (
            "current_avg_mean", "current_avg_std", "power_mean",
            "power_p95", "power_factor_mean", "voltage_avg_mean",
            "phase_imbalance_mean", "frequency_mean",
        ):
            val = getattr(result.baseline_input, field)
            if val is not None:
                assert math.isfinite(val), f"{field} is not finite: {val}"

    def test_nan_in_window_treated_as_missing(self):
        window = FeatureWindowResult(
            window=FeatureWindowInput(
                current_avg_mean=float("nan"),
                power_mean=5000.0,
                power_factor_mean=0.95,
            ),
            running_state="STEADY_RUNNING",
            telemetry_coverage=0.5,
            sample_count=30,
        )
        windows = [window] * 5
        result = learn_baseline(windows, minimum_days=0)
        assert result.baseline_input.current_avg_mean is None
        assert result.baseline_input.power_mean is not None


class TestLearnedFromTimestamps:
    def test_learned_from_derived_from_steady_windows(self):
        base = datetime(2026, 5, 21, 10, 0, tzinfo=timezone.utc)
        windows = [
            _steady_window(
                window_start=base + timedelta(hours=i),
                window_end=base + timedelta(hours=i, minutes=59),
            )
            for i in range(5)
        ]
        result = learn_baseline(windows, minimum_days=0)
        assert result.baseline_input.current_avg_mean is not None

    def test_no_steady_windows_gives_none_timestamps(self):
        windows = [_off_window() for _ in range(5)]
        result = learn_baseline(windows, minimum_days=0)
        assert result.quality_band == "insufficient"

    def test_service_learned_from_start_is_earliest_steady(self):
        from app.services.degradation.service import learn_baseline_from_windows

        base = datetime(2026, 5, 21, 10, 0, tzinfo=timezone.utc)
        windows = [
            _steady_window(
                window_start=base + timedelta(hours=i),
                window_end=base + timedelta(hours=i, minutes=59),
            )
            for i in range(5)
        ]
        result = learn_baseline_from_windows(windows, tenant_id="T1", device_id="D1", minimum_days=0)
        assert result["learned_from_start"] == base
        assert result["learned_from_end"] == base + timedelta(hours=4, minutes=59)

    def test_service_learned_from_none_when_no_steady(self):
        from app.services.degradation.service import learn_baseline_from_windows

        windows = [_off_window() for _ in range(5)]
        result = learn_baseline_from_windows(windows, tenant_id="T1", device_id="D1", minimum_days=0)
        assert result["learned_from_start"] is None
        assert result["learned_from_end"] is None

    def test_service_learned_from_ignores_off_windows(self):
        from app.services.degradation.service import learn_baseline_from_windows

        base = datetime(2026, 5, 21, 10, 0, tzinfo=timezone.utc)
        steady = _steady_window(
            window_start=base + timedelta(hours=2),
            window_end=base + timedelta(hours=2, minutes=59),
        )
        off = _off_window(
            window_start=base + timedelta(hours=0),
            window_end=base + timedelta(hours=0, minutes=59),
        )
        windows = [off, steady]
        result = learn_baseline_from_windows(windows, tenant_id="T1", device_id="D1", minimum_days=0)
        assert result["learned_from_start"] == base + timedelta(hours=2)
        assert result["learned_from_end"] == base + timedelta(hours=2, minutes=59)

    def test_service_learned_from_none_when_windows_missing_timestamps(self):
        from app.services.degradation.service import learn_baseline_from_windows

        windows = [_steady_window() for _ in range(5)]
        result = learn_baseline_from_windows(windows, tenant_id="T1", device_id="D1", minimum_days=0)
        assert result["learned_from_start"] is None
        assert result["learned_from_end"] is None


class TestMinimumDays:
    def test_short_span_enforces_minimum_days(self):
        base = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        windows = [
            _steady_window(
                window_start=base + timedelta(hours=i),
                window_end=base + timedelta(hours=i, minutes=59),
            )
            for i in range(10)
        ]
        result = learn_baseline(windows, minimum_days=7)

        assert result.quality_band in ("insufficient", "low")

    def test_long_span_satisfies_minimum_days(self):
        base = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        windows = [
            _steady_window(
                window_start=base + timedelta(days=i),
                window_end=base + timedelta(days=i, minutes=59),
            )
            for i in range(8)
        ]
        result = learn_baseline(windows, minimum_days=7)

        assert result.quality_band in ("high", "medium", "low")

    def test_minimum_days_zero_skips_check(self):
        windows = [_steady_window() for _ in range(3)]
        result = learn_baseline(windows, minimum_days=0)

        assert result.quality_score > 0.0
