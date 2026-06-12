"""Pure degradation scorer — no DB, no HTTP, no I/O.

Takes a baseline, recent steady-running feature windows, and optional prior
score history.  Returns a deterministic, explainable ``ScoreResult``.
"""

from __future__ import annotations

import math
from typing import Optional, Sequence

from .types import BaselineInput, Contribution, FeatureWindowInput, PriorScoreEntry, ScoreResult

_EPSILON = 1e-9
_SCORE_MIN = 1.0
_SCORE_MAX = 10.0
_MAX_DRIFT = 3.0
_WEAK_BASELINE_THRESHOLD = 0.3

_MIN_SIGNAL_COMPLETENESS = 0.4
_REQUIRED_SIGNALS = {"current_variability_drift", "power_factor_drop"}

_SIGNAL_WEIGHTS: dict[str, float] = {
    "current_variability_drift": 0.25,
    "power_factor_drop": 0.25,
    "abnormal_power_draw": 0.20,
    "phase_imbalance_drift": 0.15,
    "trend_worsening": 0.15,
}

_SIGNAL_MAX_DRIFT: dict[str, float] = {
    "current_variability_drift": 3.0,
    "power_factor_drop": 1.0,
    "abnormal_power_draw": 3.0,
    "phase_imbalance_drift": 3.0,
    "trend_worsening": 3.0,
}

_STATUS_BANDS: list[tuple[float, str]] = [
    (7.0, "critical"),
    (5.0, "warning"),
    (3.0, "watch"),
]

_REASON_LABELS: dict[str, str] = {
    "current_variability_drift": "Current variability above baseline",
    "power_factor_drop": "Power factor below baseline",
    "abnormal_power_draw": "Power draw deviating from baseline",
    "phase_imbalance_drift": "Phase imbalance above baseline",
    "trend_worsening": "Degradation trend worsening",
}

SIGNAL_TO_ANOMALY_FIELD: dict[str, str | None] = {
    "current_variability_drift": "current_avg",
    "power_factor_drop": "power_factor",
    "abnormal_power_draw": "power",
    "phase_imbalance_drift": "phase_imbalance",
    "trend_worsening": None,
}


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _is_valid(v: Optional[float]) -> bool:
    return v is not None and math.isfinite(v)


def _avg(values: Sequence[float]) -> Optional[float]:
    if not values:
        return None
    result = sum(values) / len(values)
    return result if math.isfinite(result) else None


def _compute_drift(
    recent: Optional[float],
    baseline: Optional[float],
    mode: str = "absolute",
) -> Optional[float]:
    if not _is_valid(recent) or not _is_valid(baseline):
        return None
    if mode == "absolute":
        if abs(baseline) < _EPSILON:
            return 0.0 if abs(recent) < _EPSILON else _MAX_DRIFT
        return abs(recent - baseline) / abs(baseline)
    if mode == "increase":
        if abs(baseline) < _EPSILON:
            return _MAX_DRIFT if recent > _EPSILON else 0.0
        return max(0.0, (recent - baseline) / abs(baseline))
    # mode == "decrease"
    if abs(baseline) < _EPSILON:
        return _MAX_DRIFT if recent < -_EPSILON else 0.0
    return max(0.0, (baseline - recent) / abs(baseline))


def _compute_trend_worsening(prior_scores: Optional[Sequence[PriorScoreEntry]]) -> Optional[float]:
    if prior_scores is None or len(prior_scores) < 2:
        return None
    sorted_entries = sorted(prior_scores, key=lambda e: e.computed_at)
    scores = [e.score for e in sorted_entries]
    n = len(scores)
    if n < 2:
        return None
    y_mean = sum(scores) / n
    if not math.isfinite(y_mean):
        return None
    x_mean = (n - 1) / 2.0
    numerator = 0.0
    denominator = 0.0
    for i, s in enumerate(scores):
        if not math.isfinite(s):
            return None
        numerator += (i - x_mean) * (s - y_mean)
        denominator += (i - x_mean) ** 2
    if abs(denominator) < _EPSILON:
        return None
    slope = numerator / denominator
    if slope <= 0.0:
        return 0.0
    return min(slope / 0.5, _MAX_DRIFT)


def _determine_status(score: float, baseline_quality: float) -> str:
    if baseline_quality < _WEAK_BASELINE_THRESHOLD:
        return "learning"
    for threshold, status in _STATUS_BANDS:
        if score >= threshold:
            return status
    return "healthy"


def _compute_top_reasons(contributions: Sequence[Contribution]) -> tuple[str, ...]:
    active = sorted(
        [(c.drift * c.weight, c.signal) for c in contributions if c.drift > 0.0],
        key=lambda x: x[0],
        reverse=True,
    )
    if not active:
        return ()
    return tuple(_REASON_LABELS.get(signal, signal) for _, signal in active[:3])


def compute_degradation_score(
    baseline: BaselineInput,
    recent_windows: Sequence[FeatureWindowInput],
    prior_scores: Optional[Sequence[PriorScoreEntry]] = None,
) -> ScoreResult:
    if not recent_windows:
        return ScoreResult(
            score=None,
            status="unavailable",
            confidence=0.0,
            contributions=(),
            top_reasons=("insufficient_data",),
        )

    recent_std = _avg([w.current_avg_std for w in recent_windows if _is_valid(w.current_avg_std)])
    recent_pf = _avg([w.power_factor_mean for w in recent_windows if _is_valid(w.power_factor_mean)])
    recent_power = _avg([w.power_mean for w in recent_windows if _is_valid(w.power_mean)])
    recent_phase = _avg([w.phase_imbalance for w in recent_windows if _is_valid(w.phase_imbalance)])

    signal_drifts: dict[str, Optional[float]] = {
        "current_variability_drift": _compute_drift(recent_std, baseline.current_avg_std, "increase"),
        "power_factor_drop": _compute_drift(recent_pf, baseline.power_factor_mean, "decrease"),
        "abnormal_power_draw": _compute_drift(recent_power, baseline.power_mean, "absolute"),
        "phase_imbalance_drift": _compute_drift(recent_phase, baseline.phase_imbalance_mean, "increase"),
        "trend_worsening": _compute_trend_worsening(prior_scores),
    }

    _SIGNAL_OBSERVED_BASELINE: dict[str, tuple[Optional[float], Optional[float]]] = {
        "current_variability_drift": (recent_std, baseline.current_avg_std),
        "power_factor_drop": (recent_pf, baseline.power_factor_mean),
        "abnormal_power_draw": (recent_power, baseline.power_mean),
        "phase_imbalance_drift": (recent_phase, baseline.phase_imbalance_mean),
        "trend_worsening": (None, None),
    }

    contributions: list[Contribution] = []
    available_count = 0
    total_weighted = 0.0
    available_weight_sum = 0.0
    available_signals: set[str] = set()

    for signal, weight in _SIGNAL_WEIGHTS.items():
        drift = signal_drifts[signal]
        if drift is None:
            contributions.append(Contribution(signal=signal, weight=weight, drift=0.0, available=False))
            continue
        available_count += 1
        available_weight_sum += weight
        available_signals.add(signal)
        normalized = _clamp(drift / _SIGNAL_MAX_DRIFT[signal], 0.0, 1.0)
        observed, baseline_val = _SIGNAL_OBSERVED_BASELINE[signal]
        contributions.append(Contribution(
            signal=signal, weight=weight, drift=normalized, available=True,
            observed_value=observed, baseline_value=baseline_val, raw_drift=drift,
        ))
        total_weighted += weight * normalized

    total_signals = len(_SIGNAL_WEIGHTS)
    signal_completeness = available_count / total_signals

    missing_required = _REQUIRED_SIGNALS - available_signals
    if missing_required:
        return ScoreResult(
            score=None,
            status="insufficient_signals",
            confidence=_clamp(signal_completeness * _clamp(baseline.quality_score, 0.0, 1.0), 0.0, 1.0),
            contributions=tuple(contributions),
            top_reasons=(f"insufficient_signal_coverage:{','.join(sorted(missing_required))}",),
        )

    if signal_completeness < _MIN_SIGNAL_COMPLETENESS:
        return ScoreResult(
            score=None,
            status="insufficient_signals",
            confidence=_clamp(signal_completeness * _clamp(baseline.quality_score, 0.0, 1.0), 0.0, 1.0),
            contributions=tuple(contributions),
            top_reasons=("insufficient_signal_coverage",),
        )

    if available_weight_sum > 0:
        total_weighted = total_weighted / available_weight_sum

    score = _SCORE_MIN + total_weighted * (_SCORE_MAX - _SCORE_MIN)
    score = _clamp(score, _SCORE_MIN, _SCORE_MAX)
    if not math.isfinite(score):
        score = _SCORE_MIN

    confidence = _clamp(signal_completeness * _clamp(baseline.quality_score, 0.0, 1.0), 0.0, 1.0)
    if not math.isfinite(confidence):
        confidence = 0.0

    status = _determine_status(score, baseline.quality_score)
    top_reasons = _compute_top_reasons(contributions)

    return ScoreResult(
        score=score,
        status=status,
        confidence=confidence,
        contributions=tuple(contributions),
        top_reasons=top_reasons,
    )
