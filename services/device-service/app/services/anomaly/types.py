"""Pure data types for the anomaly detection pipeline.

No SQLAlchemy, no HTTP, no I/O — only dataclasses consumed by the
baseline learner, detector, and aggregator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Optional


SUPPORTED_FIELDS = ("current_avg", "power", "power_factor", "voltage_avg", "phase_imbalance")

ANOMALY_FIELD_TO_SIGNAL: dict[str, str | None] = {
    "current_avg": "current_variability_drift",
    "power": "abnormal_power_draw",
    "power_factor": "power_factor_drop",
    "voltage_avg": None,
    "phase_imbalance": "phase_imbalance_drift",
}

DEFAULT_TIME_WINDOW = "5min"


@dataclass(frozen=True)
class SignalBreakdownEntry:
    field_name: str
    count: int = 0
    mild: int = 0
    strong: int = 0
    severe: int = 0


@dataclass(frozen=True)
class AnomalyFieldBaseline:
    """Per-field anomaly baseline learned from steady-running feature windows."""

    field_name: str
    time_window: str = DEFAULT_TIME_WINDOW
    baseline_mean: Optional[float] = None
    baseline_std: Optional[float] = None
    baseline_median: Optional[float] = None
    baseline_mad: Optional[float] = None
    baseline_p05: Optional[float] = None
    baseline_p95: Optional[float] = None
    reading_count: int = 0
    quality_score: float = 0.0
    quality_band: str = "insufficient"
    learned_from_ts: Optional[datetime] = None
    learned_to_ts: Optional[datetime] = None
    status: str = "candidate"
    baseline_version: int = 1
    field_coverage: float = 0.0
    steady_coverage: float = 0.0


@dataclass(frozen=True)
class AnomalyBaselineInput:
    """Active anomaly baselines for a device, keyed by field_name."""

    baselines: tuple[AnomalyFieldBaseline, ...]


@dataclass(frozen=True)
class AnomalyCandidate:
    """A confirmed anomaly event candidate from detection."""

    signal_field: str
    signal_value: Optional[float] = None
    baseline_mean: Optional[float] = None
    baseline_std: Optional[float] = None
    z_score: Optional[float] = None
    anomaly_type: str = "deviation"
    severity: str = "mild"
    confidence: float = 0.0
    supply_related: bool = False
    startup_adjacent: bool = False
    mode_change: bool = False
    recurring: bool = False
    time_window: str = DEFAULT_TIME_WINDOW
    correlated_signals: tuple[str, ...] = ()
    baseline_version: Optional[int] = None
    occurred_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    duration_seconds: Optional[int] = None
    merged_window_count: int = 1
    z_score_history: tuple[float, ...] = ()


@dataclass(frozen=True)
class DailyCountResult:
    date: date
    total_count: int = 0
    mild_count: int = 0
    strong_count: int = 0
    severe_count: int = 0
    supply_related_count: int = 0
    top_signal: Optional[str] = None
    avg_confidence: Optional[float] = None
    signal_breakdown: tuple[SignalBreakdownEntry, ...] = ()


@dataclass(frozen=True)
class WeeklyCountResult:
    """Aggregated weekly anomaly counts for a single device-week."""

    week_start_date: date
    total_count: int = 0
    mild_count: int = 0
    strong_count: int = 0
    severe_count: int = 0
    supply_related_count: int = 0
    top_signal: Optional[str] = None
    avg_confidence: Optional[float] = None
    signal_breakdown: tuple[SignalBreakdownEntry, ...] = ()
    week_over_week_change: Optional[int] = None
