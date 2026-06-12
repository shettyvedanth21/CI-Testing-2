"""Pure data types for the degradation scoring pipeline.

No SQLAlchemy, no HTTP, no I/O — only dataclasses consumed by the scorer,
aggregator, and baseline learner.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class TelemetrySample:
    timestamp: datetime
    current_avg: Optional[float] = None
    current_l1: Optional[float] = None
    current_l2: Optional[float] = None
    current_l3: Optional[float] = None
    power: Optional[float] = None
    power_factor: Optional[float] = None
    voltage_avg: Optional[float] = None
    voltage_l1: Optional[float] = None
    voltage_l2: Optional[float] = None
    voltage_l3: Optional[float] = None
    frequency: Optional[float] = None
    energy_kwh: Optional[float] = None


@dataclass(frozen=True)
class BaselineInput:
    current_avg_mean: Optional[float] = None
    current_avg_std: Optional[float] = None
    power_mean: Optional[float] = None
    power_p95: Optional[float] = None
    power_factor_mean: Optional[float] = None
    voltage_avg_mean: Optional[float] = None
    phase_imbalance_mean: Optional[float] = None
    frequency_mean: Optional[float] = None
    quality_score: float = 1.0
    quality_band: Optional[str] = None


@dataclass(frozen=True)
class FeatureWindowInput:
    current_avg_mean: Optional[float] = None
    current_avg_std: Optional[float] = None
    current_avg_p95: Optional[float] = None
    current_l1_mean: Optional[float] = None
    current_l2_mean: Optional[float] = None
    current_l3_mean: Optional[float] = None
    power_mean: Optional[float] = None
    power_p95: Optional[float] = None
    power_factor_mean: Optional[float] = None
    voltage_avg_mean: Optional[float] = None
    voltage_imbalance: Optional[float] = None
    phase_imbalance: Optional[float] = None
    frequency_mean: Optional[float] = None
    energy_kwh: Optional[float] = None


@dataclass(frozen=True)
class FeatureWindowResult:
    window: FeatureWindowInput
    running_state: str
    telemetry_coverage: float
    sample_count: int
    window_start: Optional[datetime] = None
    window_end: Optional[datetime] = None


@dataclass(frozen=True)
class BaselineLearnResult:
    baseline_input: BaselineInput
    quality_score: float
    quality_band: str
    signal_completeness: float
    steady_running_coverage: float
    learning_window_count: int


@dataclass(frozen=True)
class PriorScoreEntry:
    score: float
    computed_at: datetime


@dataclass(frozen=True)
class Contribution:
    signal: str
    weight: float
    drift: float
    available: bool = True
    observed_value: Optional[float] = None
    baseline_value: Optional[float] = None
    raw_drift: Optional[float] = None


@dataclass(frozen=True)
class ScoreResult:
    score: Optional[float]
    status: str
    confidence: float
    contributions: tuple[Contribution, ...]
    top_reasons: tuple[str, ...]
