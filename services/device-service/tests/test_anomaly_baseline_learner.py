from __future__ import annotations

import sys
import types
from datetime import datetime, timezone
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

from app.services.anomaly.baseline_learner import learn_anomaly_baseline
from app.services.anomaly.types import AnomalyFieldBaseline


def _make_window(current_avg_mean=None, power_mean=None, power_factor_mean=None,
                 voltage_avg_mean=None, phase_imbalance=None,
                 running_state="STEADY_RUNNING", window_start=None, window_end=None):
    class _Window:
        pass
    class _Input:
        pass
    inp = _Input()
    inp.current_avg_mean = current_avg_mean
    inp.power_mean = power_mean
    inp.power_factor_mean = power_factor_mean
    inp.voltage_avg_mean = voltage_avg_mean
    inp.phase_imbalance = phase_imbalance
    w = _Window()
    w.window = inp
    w.running_state = running_state
    w.window_start = window_start or datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    w.window_end = window_end or datetime(2026, 1, 1, 1, 0, tzinfo=timezone.utc)
    return w


def test_baseline_happy_path_current_avg():
    windows = [_make_window(current_avg_mean=10.0 + i * 0.5) for i in range(10)]
    result = learn_anomaly_baseline(windows, "current_avg", minimum_days=0)

    assert isinstance(result, AnomalyFieldBaseline)
    assert result.field_name == "current_avg"
    assert result.time_window == "5min"
    assert result.reading_count == 10
    assert result.baseline_mean is not None
    assert result.baseline_std is not None
    assert result.baseline_std > 0
    assert result.status == "active"
    assert result.quality_score >= 0.3
    assert result.quality_band in ("high", "medium", "low")
    assert result.baseline_p05 is not None
    assert result.baseline_p95 is not None
    assert result.learned_from_ts is not None
    assert result.learned_to_ts is not None


def test_baseline_insufficient_data():
    windows = [_make_window(current_avg_mean=10.0)]
    result = learn_anomaly_baseline(windows, "current_avg", minimum_days=0)

    assert result.reading_count == 1
    assert result.quality_score < 0.3
    assert result.status == "candidate"


def test_baseline_std_zero_unusable():
    windows = [_make_window(current_avg_mean=10.0) for _ in range(10)]
    result = learn_anomaly_baseline(windows, "current_avg", minimum_days=0)

    assert result.baseline_std is not None
    assert result.baseline_std < 1e-9
    assert result.status == "candidate"


def test_baseline_mad_computation():
    values = [10.0, 10.5, 11.0, 9.5, 9.0, 10.2, 10.8, 9.3, 10.6, 9.8]
    windows = [_make_window(current_avg_mean=v) for v in values]
    result = learn_anomaly_baseline(windows, "current_avg", minimum_days=0)

    assert result.baseline_mad is not None
    assert result.baseline_mad > 0


def test_baseline_percentile_computation():
    values = list(range(1, 21))
    windows = [_make_window(current_avg_mean=float(v)) for v in values]
    result = learn_anomaly_baseline(windows, "current_avg", minimum_days=0)

    assert result.baseline_p05 is not None
    assert result.baseline_p95 is not None
    assert result.baseline_p05 < result.baseline_p95


def test_baseline_quality_score_clamping():
    windows = [_make_window(current_avg_mean=10.0 + i) for i in range(50)]
    result = learn_anomaly_baseline(windows, "current_avg", minimum_days=0)

    assert 0.0 <= result.quality_score <= 1.0


def test_baseline_learned_timestamps():
    ws = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    we = datetime(2026, 1, 1, 1, 0, tzinfo=timezone.utc)
    windows = [_make_window(current_avg_mean=10.0 + i, window_start=ws.replace(hour=i), window_end=we.replace(hour=i)) for i in range(10)]

    result = learn_anomaly_baseline(windows, "current_avg", minimum_days=0)

    assert result.learned_from_ts == ws.replace(hour=0)
    assert result.learned_to_ts == we.replace(hour=9)


def test_baseline_profile_label_5min():
    windows = [_make_window(current_avg_mean=10.0 + i * 0.5) for i in range(10)]
    result = learn_anomaly_baseline(windows, "current_avg", time_window="5min", minimum_days=0)

    assert result.time_window == "5min"


def test_baseline_power_field():
    windows = [_make_window(power_mean=500.0 + i * 10) for i in range(10)]
    result = learn_anomaly_baseline(windows, "power", minimum_days=0)

    assert result.field_name == "power"
    assert result.baseline_mean is not None
    assert result.status == "active"


def test_baseline_power_factor_field():
    windows = [_make_window(power_factor_mean=0.85 + i * 0.01) for i in range(10)]
    result = learn_anomaly_baseline(windows, "power_factor", minimum_days=0)

    assert result.field_name == "power_factor"
    assert result.baseline_mean is not None


def test_baseline_voltage_avg_field():
    windows = [_make_window(voltage_avg_mean=230.0 + i * 0.5) for i in range(10)]
    result = learn_anomaly_baseline(windows, "voltage_avg", minimum_days=0)

    assert result.field_name == "voltage_avg"
    assert result.baseline_mean is not None


def test_baseline_phase_imbalance_field():
    windows = [_make_window(phase_imbalance=0.02 + i * 0.005) for i in range(10)]
    result = learn_anomaly_baseline(windows, "phase_imbalance", minimum_days=0)

    assert result.field_name == "phase_imbalance"
    assert result.baseline_mean is not None


def test_baseline_unknown_field_returns_insufficient():
    windows = [_make_window(current_avg_mean=10.0) for _ in range(10)]
    result = learn_anomaly_baseline(windows, "nonexistent_field", minimum_days=0)

    assert result.quality_band == "insufficient"
    assert result.baseline_mean is None


def test_baseline_zero_windows():
    result = learn_anomaly_baseline([], "current_avg")

    assert result.reading_count == 0
    assert result.quality_score == 0.0
    assert result.quality_band == "insufficient"
    assert result.status == "candidate"


def test_baseline_only_non_steady_windows():
    windows = [_make_window(current_avg_mean=10.0, running_state="STARTUP") for _ in range(10)]
    result = learn_anomaly_baseline(windows, "current_avg", minimum_days=0)

    assert result.reading_count == 0
    assert result.quality_band == "insufficient"


def test_baseline_null_values_skipped():
    windows = [_make_window(current_avg_mean=None) for _ in range(10)]
    result = learn_anomaly_baseline(windows, "current_avg", minimum_days=0)

    assert result.reading_count == 0
    assert result.baseline_mean is None


def test_baseline_mixed_null_and_valid():
    windows = [_make_window(current_avg_mean=10.0 + i if i % 2 == 0 else None) for i in range(12)]
    result = learn_anomaly_baseline(windows, "current_avg", minimum_days=0)

    assert result.reading_count == 6
    assert result.field_coverage > 0.0
    assert result.field_coverage < 1.0


def test_baseline_version_carried_through():
    windows = [_make_window(current_avg_mean=10.0 + i * 0.5) for i in range(10)]
    result = learn_anomaly_baseline(windows, "current_avg", baseline_version=3, minimum_days=0)

    assert result.baseline_version == 3


def test_baseline_minimum_days_enforced():
    from datetime import timedelta
    base = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    windows = [
        _make_window(
            current_avg_mean=10.0 + i * 0.5,
            window_start=base + timedelta(hours=i),
            window_end=base + timedelta(hours=i + 1),
        )
        for i in range(10)
    ]
    result = learn_anomaly_baseline(windows, "current_avg", minimum_days=7)

    assert result.status == "candidate"
    assert result.quality_band in ("insufficient", "low")


def test_baseline_minimum_days_met():
    from datetime import timedelta
    base = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    windows = [
        _make_window(
            current_avg_mean=10.0 + i * 0.5,
            window_start=base + timedelta(days=i),
            window_end=base + timedelta(days=i, hours=1),
        )
        for i in range(8)
    ]
    result = learn_anomaly_baseline(windows, "current_avg", minimum_days=7)

    assert result.status == "active"
    assert result.quality_band in ("high", "medium", "low")
