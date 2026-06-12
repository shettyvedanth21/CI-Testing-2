"""Pure feature-window aggregation from raw telemetry samples.

No DB, no HTTP, no I/O — takes a list of ``TelemetrySample`` objects and
produces a ``FeatureWindowResult`` with computed stats and a running-state
classification.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Optional, Sequence

from .types import FeatureWindowInput, FeatureWindowResult, TelemetrySample

_EPSILON = 1e-9
_OFF_POWER_THRESHOLD = 50.0
_OFF_CURRENT_THRESHOLD = 0.5
_STEADY_CV_THRESHOLD = 0.15
_LOAD_CHANGE_STEP_FRACTION = 0.25
_STARTUP_RAMP_FRACTION = 0.5
_MIN_SAMPLES_FOR_STATE = 3


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


def _compute_phase_imbalance(
    l1: Optional[float],
    l2: Optional[float],
    l3: Optional[float],
) -> Optional[float]:
    phases = [v for v in (l1, l2, l3) if _is_valid(v)]
    if len(phases) < 2:
        return None
    avg = sum(phases) / len(phases)
    if abs(avg) < _EPSILON:
        return 0.0 if all(abs(p) < _EPSILON for p in phases) else None
    result = max(abs(p - avg) for p in phases) / abs(avg)
    return result if math.isfinite(result) else None


def _compute_voltage_imbalance(
    l1: Optional[float],
    l2: Optional[float],
    l3: Optional[float],
) -> Optional[float]:
    phases = [v for v in (l1, l2, l3) if _is_valid(v)]
    if len(phases) < 2:
        return None
    avg = sum(phases) / len(phases)
    if abs(avg) < _EPSILON:
        return 0.0 if all(abs(p) < _EPSILON for p in phases) else None
    result = max(abs(p - avg) for p in phases) / abs(avg)
    return result if math.isfinite(result) else None


def classify_running_state(samples: Sequence[TelemetrySample]) -> str:
    if len(samples) < _MIN_SAMPLES_FOR_STATE:
        return "UNKNOWN"

    powers = [s.power for s in samples if _is_valid(s.power)]
    currents = [s.current_avg for s in samples if _is_valid(s.current_avg)]

    if not powers and not currents:
        return "UNKNOWN"

    if powers:
        mean_power = sum(powers) / len(powers)
        if mean_power < _OFF_POWER_THRESHOLD:
            return "OFF"
    if currents and not powers:
        mean_current = sum(currents) / len(currents)
        if mean_current < _OFF_CURRENT_THRESHOLD:
            return "OFF"

    if currents and len(currents) >= 3:
        first_third = currents[: len(currents) // 3]
        last_third = currents[-len(currents) // 3 :]
        first_mean = sum(first_third) / len(first_third)
        last_mean = sum(last_third) / len(last_third)
        overall_mean = sum(currents) / len(currents)

        step_changes = 0
        if overall_mean > _EPSILON:
            for i in range(1, len(currents)):
                delta = abs(currents[i] - currents[i - 1]) / overall_mean
                if delta > _LOAD_CHANGE_STEP_FRACTION:
                    step_changes += 1

        if step_changes > 0 and overall_mean > _EPSILON:
            ramp = (last_mean - first_mean) / overall_mean
            if ramp > _STARTUP_RAMP_FRACTION and first_mean < _OFF_CURRENT_THRESHOLD * 2:
                return "STARTUP"
            if ramp < -_STARTUP_RAMP_FRACTION and last_mean < _OFF_CURRENT_THRESHOLD * 2:
                return "SHUTDOWN"
            return "LOAD_CHANGE"

        if overall_mean > _EPSILON:
            ramp = (last_mean - first_mean) / overall_mean
            if ramp > _STARTUP_RAMP_FRACTION and first_mean < _OFF_CURRENT_THRESHOLD * 2:
                return "STARTUP"
            if ramp < -_STARTUP_RAMP_FRACTION and last_mean < _OFF_CURRENT_THRESHOLD * 2:
                return "SHUTDOWN"

        cv = _std(currents)
        if cv is not None and overall_mean > _EPSILON:
            coefficient_of_variation = cv / overall_mean
            if coefficient_of_variation <= _STEADY_CV_THRESHOLD:
                return "STEADY_RUNNING"
            if step_changes == 0:
                return "STEADY_RUNNING"

    if powers and len(powers) >= 3:
        overall_mean = sum(powers) / len(powers)
        cv = _std(powers)
        if cv is not None and overall_mean > _EPSILON:
            coefficient_of_variation = cv / overall_mean
            if coefficient_of_variation <= _STEADY_CV_THRESHOLD:
                return "STEADY_RUNNING"
            return "LOAD_CHANGE"

    return "UNKNOWN"


def aggregate_feature_window(
    samples: Sequence[TelemetrySample],
    expected_sample_count: int = 0,
    window_start: Optional[datetime] = None,
    window_end: Optional[datetime] = None,
) -> FeatureWindowResult:
    if not samples:
        return FeatureWindowResult(
            window=FeatureWindowInput(),
            running_state="UNKNOWN",
            telemetry_coverage=0.0,
            sample_count=0,
            window_start=window_start,
            window_end=window_end,
        )

    current_avgs = [s.current_avg for s in samples if _is_valid(s.current_avg)]
    l1s = [s.current_l1 for s in samples if _is_valid(s.current_l1)]
    l2s = [s.current_l2 for s in samples if _is_valid(s.current_l2)]
    l3s = [s.current_l3 for s in samples if _is_valid(s.current_l3)]
    powers = [s.power for s in samples if _is_valid(s.power)]
    pfs = [s.power_factor for s in samples if _is_valid(s.power_factor)]
    voltages = [s.voltage_avg for s in samples if _is_valid(s.voltage_avg)]
    vl1s = [s.voltage_l1 for s in samples if _is_valid(s.voltage_l1)]
    vl2s = [s.voltage_l2 for s in samples if _is_valid(s.voltage_l2)]
    vl3s = [s.voltage_l3 for s in samples if _is_valid(s.voltage_l3)]
    freqs = [s.frequency for s in samples if _is_valid(s.frequency)]
    energies = [s.energy_kwh for s in samples if _is_valid(s.energy_kwh)]

    current_avg_mean = _mean(current_avgs)
    current_avg_std = _std(current_avgs) if len(current_avgs) >= 2 else None
    current_avg_p95 = _p95(current_avgs)
    current_l1_mean = _mean(l1s)
    current_l2_mean = _mean(l2s)
    current_l3_mean = _mean(l3s)

    last_l1 = l1s[-1] if l1s else None
    last_l2 = l2s[-1] if l2s else None
    last_l3 = l3s[-1] if l3s else None
    phase_imbalance = _compute_phase_imbalance(last_l1, last_l2, last_l3)

    last_vl1 = vl1s[-1] if vl1s else None
    last_vl2 = vl2s[-1] if vl2s else None
    last_vl3 = vl3s[-1] if vl3s else None
    voltage_imbalance = _compute_voltage_imbalance(last_vl1, last_vl2, last_vl3)

    power_mean = _mean(powers)
    power_p95 = _p95(powers)
    power_factor_mean = _mean(pfs)
    voltage_avg_mean = _mean(voltages)
    frequency_mean = _mean(freqs)

    energy_kwh: Optional[float] = None
    if len(energies) >= 2:
        delta = energies[-1] - energies[0]
        if math.isfinite(delta):
            energy_kwh = max(0.0, delta)

    sample_count = len(samples)
    if expected_sample_count > 0:
        coverage = _clamp(sample_count / expected_sample_count, 0.0, 1.0)
    else:
        coverage = 1.0

    running_state = classify_running_state(samples)

    return FeatureWindowResult(
        window=FeatureWindowInput(
            current_avg_mean=current_avg_mean,
            current_avg_std=current_avg_std,
            current_avg_p95=current_avg_p95,
            current_l1_mean=current_l1_mean,
            current_l2_mean=current_l2_mean,
            current_l3_mean=current_l3_mean,
            power_mean=power_mean,
            power_p95=power_p95,
            power_factor_mean=power_factor_mean,
            voltage_avg_mean=voltage_avg_mean,
            voltage_imbalance=voltage_imbalance,
            phase_imbalance=phase_imbalance,
            frequency_mean=frequency_mean,
            energy_kwh=energy_kwh,
        ),
        running_state=running_state,
        telemetry_coverage=coverage,
        sample_count=sample_count,
        window_start=window_start,
        window_end=window_end,
    )
