"""Machine degradation scoring package."""

from .baseline_learner import learn_baseline
from .feature_aggregator import aggregate_feature_window, classify_running_state
from .scorer import compute_degradation_score
from .service import (
    build_feature_window_from_samples,
    build_history_entry,
    build_latest_score_snapshot,
    learn_baseline_from_windows,
)
from .types import (
    BaselineInput,
    BaselineLearnResult,
    Contribution,
    FeatureWindowInput,
    FeatureWindowResult,
    PriorScoreEntry,
    ScoreResult,
    TelemetrySample,
)

__all__ = [
    "BaselineInput",
    "BaselineLearnResult",
    "Contribution",
    "FeatureWindowInput",
    "FeatureWindowResult",
    "PriorScoreEntry",
    "ScoreResult",
    "TelemetrySample",
    "aggregate_feature_window",
    "build_feature_window_from_samples",
    "build_history_entry",
    "build_latest_score_snapshot",
    "classify_running_state",
    "compute_degradation_score",
    "learn_baseline",
    "learn_baseline_from_windows",
]
