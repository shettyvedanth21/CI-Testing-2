from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


DEFAULT_IDLE_THRESHOLD_PCT_OF_FLA = 0.25


@dataclass(frozen=True)
class ThresholdResolution:
    full_load_current_a: Optional[float]
    idle_threshold_pct_of_fla: float
    derived_idle_threshold_a: Optional[float]
    derived_overconsumption_threshold_a: Optional[float]
    configured: bool
    source: str


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def resolve_device_thresholds(device: Any) -> ThresholdResolution:
    full_load_current_a = _to_float(getattr(device, "full_load_current_a", None))
    idle_threshold_pct_of_fla = _to_float(getattr(device, "idle_threshold_pct_of_fla", None))
    if idle_threshold_pct_of_fla is None or idle_threshold_pct_of_fla <= 0 or idle_threshold_pct_of_fla >= 1:
        idle_threshold_pct_of_fla = DEFAULT_IDLE_THRESHOLD_PCT_OF_FLA

    if full_load_current_a is None or full_load_current_a <= 0:
        return ThresholdResolution(
            full_load_current_a=None,
            idle_threshold_pct_of_fla=idle_threshold_pct_of_fla,
            derived_idle_threshold_a=None,
            derived_overconsumption_threshold_a=None,
            configured=False,
            source="unconfigured",
        )

    derived_idle_threshold_a = round(full_load_current_a * idle_threshold_pct_of_fla, 6)
    return ThresholdResolution(
        full_load_current_a=full_load_current_a,
        idle_threshold_pct_of_fla=idle_threshold_pct_of_fla,
        derived_idle_threshold_a=derived_idle_threshold_a,
        derived_overconsumption_threshold_a=full_load_current_a,
        configured=True,
        source="full_load_current_a",
    )


def classify_current_band(
    current: Optional[float],
    voltage: Optional[float],
    thresholds: ThresholdResolution,
) -> str:
    if current is None or voltage is None:
        return "unknown"
    if current <= 0 and voltage > 0:
        return "unloaded"
    if current > 0 and voltage > 0 and not thresholds.configured:
        return "unknown"
    if current > 0 and voltage > 0 and thresholds.derived_idle_threshold_a is not None and current < thresholds.derived_idle_threshold_a:
        return "idle"
    if (
        current > 0
        and voltage > 0
        and thresholds.derived_overconsumption_threshold_a is not None
        and current > thresholds.derived_overconsumption_threshold_a
    ):
        return "overconsumption"
    if (
        current > 0
        and voltage > 0
        and thresholds.derived_idle_threshold_a is not None
        and thresholds.full_load_current_a is not None
        and thresholds.derived_idle_threshold_a <= current <= thresholds.full_load_current_a
    ):
        return "in_load"
    return "unknown"


def classify_load_state(
    current: Optional[float],
    voltage: Optional[float],
    thresholds: ThresholdResolution,
) -> str:
    band = classify_current_band(current, voltage, thresholds)
    if band == "unloaded":
        return "unloaded"
    if band == "idle":
        return "idle"
    if band == "overconsumption":
        return "overconsumption"
    if band == "in_load":
        return "running"
    return "unknown"
