"""Pure baseline learner for the degradation pipeline.

Learns a statistical baseline from steady-running feature windows only.
Returns a structured ``BaselineLearnResult`` — never an ORM instance.
"""

from __future__ import annotations

import math
from datetime import timedelta
from typing import Optional, Sequence

from .types import BaselineInput, BaselineLearnResult, FeatureWindowResult

_EPSILON = 1e-9
_INSUFFICIENT_WINDOW_THRESHOLD = 3
_HIGH_QUALITY_THRESHOLD = 0.85
_MEDIUM_QUALITY_THRESHOLD = 0.70
_LOW_QUALITY_THRESHOLD = 0.50
_MINIMUM_DAYS_DEFAULT = 7


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _is_valid(v: Optional[float]) -> bool:
    return v is not None and math.isfinite(v)


def _mean(values: Sequence[float]) -> Optional[float]:
    if not values:
        return None
    result = sum(values) / len(values)
    return result if math.isfinite(result) else None


def _std(values: Sequence[float]) -> Optional[float]:
    if len(values) < 2:
        return None
    m = sum(values) / len(values)
    if not math.isfinite(m):
        return None
    variance = sum((x - m) ** 2 for x in values) / (len(values) - 1)
    result = math.sqrt(variance)
    return result if math.isfinite(result) else None


def _p95(values: Sequence[float]) -> Optional[float]:
    if not values:
        return None
    sorted_vals = sorted(values)
    idx = int(math.ceil(0.95 * len(sorted_vals))) - 1
    idx = _clamp(idx, 0, len(sorted_vals) - 1)
    result = sorted_vals[idx]
    return result if math.isfinite(result) else None


def _compute_signal_completeness(windows: Sequence[FeatureWindowResult]) -> float:
    if not windows:
        return 0.0
    key_fields = [
        "current_avg_mean", "current_avg_std",
        "power_mean", "power_p95", "power_factor_mean",
        "voltage_avg_mean", "phase_imbalance", "frequency_mean",
    ]
    total_possible = len(windows) * len(key_fields)
    total_present = 0
    for w in windows:
        for field in key_fields:
            val = getattr(w.window, field, None)
            if _is_valid(val):
                total_present += 1
    if total_possible == 0:
        return 0.0
    return _clamp(total_present / total_possible, 0.0, 1.0)


def _compute_quality_score(
    signal_completeness: float,
    steady_coverage: float,
    window_count: int,
) -> float:
    if window_count < _INSUFFICIENT_WINDOW_THRESHOLD:
        return _clamp(window_count / _INSUFFICIENT_WINDOW_THRESHOLD * 0.2, 0.0, 1.0)
    raw = (signal_completeness * 0.5 + steady_coverage * 0.3 + min(window_count / 20.0, 1.0) * 0.2)
    return _clamp(raw, 0.0, 1.0)


def _determine_quality_band(quality_score: float) -> str:
    if quality_score >= _HIGH_QUALITY_THRESHOLD:
        return "high"
    if quality_score >= _MEDIUM_QUALITY_THRESHOLD:
        return "medium"
    if quality_score >= _LOW_QUALITY_THRESHOLD:
        return "low"
    return "insufficient"


def _temporal_span_days(windows: Sequence[FeatureWindowResult]) -> float:
    starts = [w.window_start for w in windows if getattr(w, "window_start", None) is not None]
    ends = [w.window_end for w in windows if getattr(w, "window_end", None) is not None]
    if not starts or not ends:
        return 0.0
    span = max(ends) - min(starts)
    return span.total_seconds() / 86400.0


def learn_baseline(
    windows: Sequence[FeatureWindowResult],
    minimum_days: int = _MINIMUM_DAYS_DEFAULT,
) -> BaselineLearnResult:
    steady_windows = [w for w in windows if w.running_state == "STEADY_RUNNING"]
    total_count = len(windows)
    steady_count = len(steady_windows)

    if steady_count == 0:
        return BaselineLearnResult(
            baseline_input=BaselineInput(quality_score=0.0, quality_band="insufficient"),
            quality_score=0.0,
            quality_band="insufficient",
            signal_completeness=0.0,
            steady_running_coverage=0.0,
            learning_window_count=0,
        )

    span_days = _temporal_span_days(steady_windows)
    minimum_days_met = span_days >= minimum_days

    steady_coverage = _clamp(steady_count / total_count, 0.0, 1.0) if total_count > 0 else 0.0

    current_avgs = [w.window.current_avg_mean for w in steady_windows if _is_valid(w.window.current_avg_mean)]
    current_stds = [w.window.current_avg_std for w in steady_windows if _is_valid(w.window.current_avg_std)]
    powers = [w.window.power_mean for w in steady_windows if _is_valid(w.window.power_mean)]
    power_p95s = [w.window.power_p95 for w in steady_windows if _is_valid(w.window.power_p95)]
    pfs = [w.window.power_factor_mean for w in steady_windows if _is_valid(w.window.power_factor_mean)]
    voltages = [w.window.voltage_avg_mean for w in steady_windows if _is_valid(w.window.voltage_avg_mean)]
    phases = [w.window.phase_imbalance for w in steady_windows if _is_valid(w.window.phase_imbalance)]
    freqs = [w.window.frequency_mean for w in steady_windows if _is_valid(w.window.frequency_mean)]

    signal_completeness = _compute_signal_completeness(steady_windows)
    quality_score = _compute_quality_score(signal_completeness, steady_coverage, steady_count)

    if not minimum_days_met:
        quality_score = _clamp(quality_score * _clamp(span_days / minimum_days, 0.0, 1.0), 0.0, 1.0)

    quality_band = _determine_quality_band(quality_score)

    baseline_input = BaselineInput(
        current_avg_mean=_mean(current_avgs),
        current_avg_std=_std(current_stds) if len(current_stds) >= 2 else _mean(current_stds),
        power_mean=_mean(powers),
        power_p95=_mean(power_p95s),
        power_factor_mean=_mean(pfs),
        voltage_avg_mean=_mean(voltages),
        phase_imbalance_mean=_mean(phases),
        frequency_mean=_mean(freqs),
        quality_score=quality_score,
        quality_band=quality_band,
    )

    return BaselineLearnResult(
        baseline_input=baseline_input,
        quality_score=quality_score,
        quality_band=quality_band,
        signal_completeness=signal_completeness,
        steady_running_coverage=steady_coverage,
        learning_window_count=steady_count,
    )
