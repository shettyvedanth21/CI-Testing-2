"""Pure per-field anomaly baseline learner.

Learns statistical distributions from steady-running feature windows,
one field at a time.  Returns structured ``AnomalyFieldBaseline`` —
never an ORM instance.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Optional, Sequence

from .types import AnomalyFieldBaseline, DEFAULT_TIME_WINDOW

_EPSILON = 1e-9
_MIN_READING_COUNT = 5
_MAD_SCALE = 1.4826

_QUALITY_FIELD_WEIGHT = 0.5
_QUALITY_STEADY_WEIGHT = 0.3
_QUALITY_COUNT_WEIGHT = 0.2
_QUALITY_COUNT_DENOMINATOR = 30.0

_HIGH_BAND = 0.85
_MEDIUM_BAND = 0.70
_LOW_BAND = 0.50
_USABLE_QUALITY = 0.3
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


def _median(values: Sequence[float]) -> Optional[float]:
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    if n % 2 == 1:
        result = s[n // 2]
    else:
        result = (s[n // 2 - 1] + s[n // 2]) / 2.0
    return result if math.isfinite(result) else None


def _mad(values: Sequence[float]) -> Optional[float]:
    """Median Absolute Deviation, scaled by 1.4826 for consistency with std."""
    med = _median(values)
    if med is None:
        return None
    deviations = [abs(x - med) for x in values]
    raw_mad = _median(deviations)
    if raw_mad is None:
        return None
    result = _MAD_SCALE * raw_mad
    return result if math.isfinite(result) else None


def _percentile(values: Sequence[float], pct: float) -> Optional[float]:
    if not values:
        return None
    s = sorted(values)
    idx = _clamp(int(math.ceil(pct / 100.0 * len(s))) - 1, 0, len(s) - 1)
    result = s[idx]
    return result if math.isfinite(result) else None


def _determine_quality_band(quality_score: float) -> str:
    if quality_score >= _HIGH_BAND:
        return "high"
    if quality_score >= _MEDIUM_BAND:
        return "medium"
    if quality_score >= _LOW_BAND:
        return "low"
    return "insufficient"


def _temporal_span_days(feature_windows: Sequence) -> float:
    starts = [getattr(w, "window_start", None) for w in feature_windows]
    ends = [getattr(w, "window_end", None) for w in feature_windows]
    valid_starts = [s for s in starts if s is not None]
    valid_ends = [e for e in ends if e is not None]
    if not valid_starts or not valid_ends:
        return 0.0
    span = max(valid_ends) - min(valid_starts)
    return span.total_seconds() / 86400.0


# Maps anomaly field_name to the attribute name on FeatureWindowInput.
# The anomaly baseline learner consumes the same FeatureWindowInput
# that Phase 1 produces, selecting one attribute per field.
_FIELD_TO_FEATURE_ATTR: dict[str, str] = {
    "current_avg": "current_avg_mean",
    "power": "power_mean",
    "power_factor": "power_factor_mean",
    "voltage_avg": "voltage_avg_mean",
    "phase_imbalance": "phase_imbalance",
}


def learn_anomaly_baseline(
    feature_windows: Sequence,
    field_name: str,
    time_window: str = DEFAULT_TIME_WINDOW,
    baseline_version: int = 1,
    minimum_days: int = _MINIMUM_DAYS_DEFAULT,
) -> AnomalyFieldBaseline:
    """Learn a per-field anomaly baseline from feature windows.

    Parameters
    ----------
    feature_windows : sequence of FeatureWindowResult
        Each element must have ``.window`` (FeatureWindowInput),
        ``.running_state``, ``.window_start``, ``.window_end``.
    field_name : str
        One of SUPPORTED_FIELDS (current_avg, power, power_factor, voltage_avg, phase_imbalance).
    time_window : str
        Detection profile label.  "5min" for Phase 2.2.
        This is a profile label — the actual source data is hourly feature windows.
    baseline_version : int
        Version counter; incremented on re-learn.

    Returns
    -------
    AnomalyFieldBaseline
    """
    attr = _FIELD_TO_FEATURE_ATTR.get(field_name)
    if attr is None:
        return AnomalyFieldBaseline(
            field_name=field_name,
            time_window=time_window,
            baseline_version=baseline_version,
            quality_band="insufficient",
        )

    # Filter to steady-running windows only.
    steady_windows = [w for w in feature_windows if getattr(w, "running_state", None) == "STEADY_RUNNING"]
    total_count = len(feature_windows)
    steady_count = len(steady_windows)

    # Extract valid values for this field from steady windows.
    field_values: list[float] = []
    for w in steady_windows:
        window_input = getattr(w, "window", None)
        if window_input is None:
            continue
        val = getattr(window_input, attr, None)
        if _is_valid(val):
            field_values.append(val)

    reading_count = len(field_values)
    steady_coverage = _clamp(steady_count / total_count, 0.0, 1.0) if total_count > 0 else 0.0

    # Field coverage: fraction of steady windows with valid values for this field.
    field_coverage = _clamp(reading_count / steady_count, 0.0, 1.0) if steady_count > 0 else 0.0

    # Compute distribution statistics.
    baseline_mean = _mean(field_values)
    baseline_std = _std(field_values)
    baseline_median = _median(field_values)
    baseline_mad = _mad(field_values)
    baseline_p05 = _percentile(field_values, 5.0)
    baseline_p95 = _percentile(field_values, 95.0)

    # Quality score.
    span_days = _temporal_span_days(feature_windows)
    minimum_days_met = span_days >= minimum_days

    if reading_count < _MIN_READING_COUNT:
        quality_score = _clamp(reading_count / _MIN_READING_COUNT * 0.2, 0.0, 1.0)
    else:
        raw = (
            field_coverage * _QUALITY_FIELD_WEIGHT
            + steady_coverage * _QUALITY_STEADY_WEIGHT
            + min(reading_count / _QUALITY_COUNT_DENOMINATOR, 1.0) * _QUALITY_COUNT_WEIGHT
        )
        quality_score = _clamp(raw, 0.0, 1.0)

    if not minimum_days_met:
        quality_score = _clamp(quality_score * _clamp(span_days / minimum_days, 0.0, 1.0), 0.0, 1.0)

    quality_band = _determine_quality_band(quality_score)

    # Determine status: only "active" if baseline is usable for detection.
    std_value = baseline_std
    is_usable = (
        reading_count >= _MIN_READING_COUNT
        and std_value is not None
        and std_value > _EPSILON
        and quality_score >= _USABLE_QUALITY
        and minimum_days_met
    )
    status = "active" if is_usable else "candidate"

    # Learned-from timestamps.
    learned_from_ts: Optional[datetime] = None
    learned_to_ts: Optional[datetime] = None
    if steady_count > 0:
        starts = [getattr(w, "window_start", None) for w in steady_windows]
        ends = [getattr(w, "window_end", None) for w in steady_windows]
        valid_starts = [s for s in starts if s is not None]
        valid_ends = [e for e in ends if e is not None]
        if valid_starts:
            learned_from_ts = min(valid_starts)
        if valid_ends:
            learned_to_ts = max(valid_ends)

    return AnomalyFieldBaseline(
        field_name=field_name,
        time_window=time_window,
        baseline_mean=baseline_mean,
        baseline_std=baseline_std,
        baseline_median=baseline_median,
        baseline_mad=baseline_mad,
        baseline_p05=baseline_p05,
        baseline_p95=baseline_p95,
        reading_count=reading_count,
        quality_score=quality_score,
        quality_band=quality_band,
        learned_from_ts=learned_from_ts,
        learned_to_ts=learned_to_ts,
        status=status,
        baseline_version=baseline_version,
        field_coverage=field_coverage,
        steady_coverage=steady_coverage,
    )
