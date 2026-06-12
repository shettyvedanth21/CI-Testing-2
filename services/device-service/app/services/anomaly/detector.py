"""Pure anomaly detector — no DB, no HTTP, no I/O.

Takes per-field anomaly baselines and a feature window, detects anomaly
candidates, applies confirmation rules, and handles event merging.
Returns a list of ``AnomalyCandidate`` — never ORM instances.
"""

from __future__ import annotations

import math
from typing import Optional, Sequence

from .types import AnomalyFieldBaseline, AnomalyCandidate, DEFAULT_TIME_WINDOW

_EPSILON = 1e-9

_MILD_THRESHOLD = 2.0
_STRONG_THRESHOLD = 3.0
_SEVERE_THRESHOLD = 4.0

_TREND_MIN_WINDOWS = 3

# Confirmation: consecutive windows required for each severity.
_MILD_CONFIRM_WINDOWS = 2
_STRONG_CONFIRM_WINDOWS = 1

# Event merging: maximum consecutive windows that merge into one event.
_MAX_MERGE_WINDOWS = 6

# Confidence multipliers by baseline quality band.
_QUALITY_CONFIDENCE = {
    "high": 1.0,
    "medium": 0.7,
    "low": 0.4,
    "insufficient": 0.0,
}

# Cross-field confidence boost.
_CROSS_FIELD_BOOST = 1.2

# Direction mode for each field.
# "two_tailed" → abs(z) used for severity
# "decrease"   → only negative z (value dropping) counts
# "increase"   → only positive z (value rising) counts
_FIELD_DIRECTION: dict[str, str] = {
    "current_avg": "two_tailed",
    "power": "two_tailed",
    "power_factor": "decrease",
    "voltage_avg": "two_tailed",
    "phase_imbalance": "increase",
}

# Maps anomaly field_name to the attribute on FeatureWindowInput.
_FIELD_TO_FEATURE_ATTR: dict[str, str] = {
    "current_avg": "current_avg_mean",
    "power": "power_mean",
    "power_factor": "power_factor_mean",
    "voltage_avg": "voltage_avg_mean",
    "phase_imbalance": "phase_imbalance",
}

# Fields whose co-anomaly suggests supply-related issues.
_SUPPLY_INDICATOR_FIELDS = {"voltage_avg"}

# Fields whose co-anomaly increases confidence.
_CONFIDENCE_BOOST_FIELDS = {"current_avg", "power_factor", "phase_imbalance"}


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _is_valid(v: Optional[float]) -> bool:
    return v is not None and math.isfinite(v)


def _compute_z_score(
    observed: Optional[float],
    baseline: AnomalyFieldBaseline,
) -> Optional[float]:
    """Compute z-score, choosing standard or modified based on availability."""
    if not _is_valid(observed):
        return None

    # Prefer standard z-score when std is meaningful.
    if _is_valid(baseline.baseline_std) and baseline.baseline_std > _EPSILON:
        z = (observed - baseline.baseline_mean) / baseline.baseline_std
        return z if math.isfinite(z) else None

    # Fallback to modified z-score when MAD is meaningful.
    if _is_valid(baseline.baseline_mad) and baseline.baseline_mad > _EPSILON:
        if not _is_valid(baseline.baseline_median):
            return None
        z = 0.6745 * (observed - baseline.baseline_median) / baseline.baseline_mad
        return z if math.isfinite(z) else None

    # Cannot compute z-score for this field.
    return None


def _effective_z(z: float, field_name: str) -> float:
    """Return the effective z magnitude based on field direction mode."""
    direction = _FIELD_DIRECTION.get(field_name, "two_tailed")

    if direction == "two_tailed":
        return abs(z)
    if direction == "decrease":
        # Only negative z matters (value dropping below baseline).
        return -z if z < 0 else 0.0
    if direction == "increase":
        # Only positive z matters (value rising above baseline).
        return z if z > 0 else 0.0

    return abs(z)


def _classify_severity(eff_z: float) -> Optional[str]:
    """Classify severity from effective z magnitude."""
    if eff_z >= _SEVERE_THRESHOLD:
        return "severe"
    if eff_z >= _STRONG_THRESHOLD:
        return "strong"
    if eff_z >= _MILD_THRESHOLD:
        return "mild"
    return None


def _compute_confidence(
    baseline: AnomalyFieldBaseline,
    eff_z: float,
    cross_field_count: int,
) -> float:
    """Compute event confidence from baseline quality, severity, and cross-field support."""
    band_conf = _QUALITY_CONFIDENCE.get(baseline.quality_band, 0.0)
    raw = band_conf * baseline.quality_score
    if cross_field_count > 0:
        raw *= _CROSS_FIELD_BOOST
    return _clamp(raw, 0.0, 1.0)


def _classify_anomaly_type(
    consecutive_count: int,
    is_trend: bool,
) -> str:
    """Determine anomaly_type from evidence."""
    if is_trend:
        return "trend"
    if consecutive_count >= 3:
        return "persistent"
    return "deviation"


def _is_trend(z_score_history: tuple[float, ...], field_name: str) -> bool:
    """Check if z-score history shows monotonic drift qualifying as a trend.

    A trend requires:
      - At least ``_TREND_MIN_WINDOWS`` entries.
      - All z-scores drift in the same anomaly direction for the field.
      - Effective z magnitudes are non-decreasing (gradual drift).
    """
    if len(z_score_history) < _TREND_MIN_WINDOWS:
        return False

    direction = _FIELD_DIRECTION.get(field_name, "two_tailed")

    if direction == "two_tailed":
        first_sign = 1 if z_score_history[0] >= 0 else -1
        if not all((1 if z >= 0 else -1) == first_sign for z in z_score_history):
            return False
        magnitudes = [abs(z) for z in z_score_history]
    elif direction == "decrease":
        if any(z >= 0 for z in z_score_history):
            return False
        magnitudes = [abs(z) for z in z_score_history]
    elif direction == "increase":
        if any(z <= 0 for z in z_score_history):
            return False
        magnitudes = list(z_score_history)
    else:
        magnitudes = [abs(z) for z in z_score_history]

    return all(
        magnitudes[i] <= magnitudes[i + 1] + _EPSILON
        for i in range(len(magnitudes) - 1)
    )


def _is_supply_related(
    field_name: str,
    z: Optional[float],
    other_anomalies: dict[str, float],
) -> bool:
    """Heuristic: voltage anomaly without machine-side corroboration → supply-related."""
    if field_name not in _SUPPLY_INDICATOR_FIELDS:
        return False
    # If current or power_factor are ALSO anomalous in a degradation direction,
    # the voltage issue is likely machine-driven, not supply.
    for corroborating in ("current_avg", "power_factor"):
        if corroborating in other_anomalies:
            return False
    return True


def detect_anomalies(
    baselines: Sequence[AnomalyFieldBaseline],
    feature_window,
    running_state: str = "STEADY_RUNNING",
    prior_window_state: Optional[str] = None,
    prior_events: Optional[Sequence[AnomalyCandidate]] = None,
    time_window: str = DEFAULT_TIME_WINDOW,
) -> list[AnomalyCandidate]:
    """Detect anomaly candidates from a single feature window.

    Parameters
    ----------
    baselines : sequence of AnomalyFieldBaseline
        Active baselines for the device (only status="active" should be passed).
    feature_window : FeatureWindowInput
        The feature window to evaluate.
    running_state : str
        Running state of this feature window.
    prior_window_state : str or None
        Running state of the immediately prior feature window, for adjacency flags.
    prior_events : sequence of AnomalyCandidate or None
        Recent events for this device, used for recurring detection and merging.
    time_window : str
        Detection profile label.

    Returns
    -------
    list[AnomalyCandidate]
        Confirmed anomaly candidates from this window.  May be empty.
    """
    if not baselines:
        return []

    baseline_map = {b.field_name: b for b in baselines}

    # Phase 1: compute raw z-scores for each field.
    raw_scores: dict[str, tuple[float, float, AnomalyFieldBaseline]] = {}
    for field_name, attr in _FIELD_TO_FEATURE_ATTR.items():
        bl = baseline_map.get(field_name)
        if bl is None or bl.status != "active":
            continue
        if bl.quality_score < 0.3:
            continue

        observed = getattr(feature_window, attr, None)
        z = _compute_z_score(observed, bl)
        if z is None:
            continue

        eff_z = _effective_z(z, field_name)
        severity = _classify_severity(eff_z)
        if severity is None:
            continue

        raw_scores[field_name] = (z, eff_z, bl)

    if not raw_scores:
        return []

    # Phase 2: confirmation and candidate construction.
    candidates: list[AnomalyCandidate] = []

    # Check adjacency flags.
    startup_adjacent = running_state in ("STARTUP", "SHUTDOWN") or prior_window_state in ("STARTUP", "SHUTDOWN")
    mode_change = running_state == "LOAD_CHANGE" or prior_window_state == "LOAD_CHANGE"

    # Count how many fields are anomalous (for cross-field boost).
    anomalous_fields = set(raw_scores.keys())

    for field_name, (z, eff_z, bl) in raw_scores.items():
        severity = _classify_severity(eff_z)

        confirmed = False
        consecutive_count = 1
        matched_prior: Optional[AnomalyCandidate] = None

        if prior_events:
            matching = [
                e for e in prior_events
                if e.signal_field == field_name and e.severity == severity
            ]
            if matching:
                most_recent = matching[-1]
                if most_recent.merged_window_count < _MAX_MERGE_WINDOWS:
                    confirmed = True
                    consecutive_count = most_recent.merged_window_count + 1
                    matched_prior = most_recent

        # Severe can confirm from single window if not startup/supply-adjacent.
        if severity == "severe" and not startup_adjacent and not _is_supply_related(field_name, z, {f: v[1] for f, v in raw_scores.items() if f != field_name}):
            confirmed = True

        # Strong confirms from single window with cross-field support.
        if severity == "strong" and len(anomalous_fields) >= 2:
            confirmed = True

        # Mild requires 2+ consecutive or cross-field.
        if severity == "mild":
            if consecutive_count >= _MILD_CONFIRM_WINDOWS:
                confirmed = True
            elif len(anomalous_fields) >= 2:
                confirmed = True

        # Strong with 1+ consecutive.
        if severity == "strong" and consecutive_count >= _STRONG_CONFIRM_WINDOWS:
            confirmed = True

        if not confirmed:
            continue

        # Supply-related heuristic.
        other_eff = {f: v[1] for f, v in raw_scores.items() if f != field_name}
        supply_related = _is_supply_related(field_name, z, other_eff)

        # Cross-field confidence boost.
        cross_field_count = len(anomalous_fields - {field_name} & _CONFIDENCE_BOOST_FIELDS)
        confidence = _compute_confidence(bl, eff_z, cross_field_count)

        # Correlated signals.
        correlated = tuple(sorted(anomalous_fields - {field_name}))

        # Recurring: check prior events for same field+severity+type in recent history.
        recurring = False
        if prior_events:
            for pe in prior_events:
                if pe.signal_field == field_name and pe.severity == severity:
                    recurring = True
                    break

        is_trend = False
        z_score_history: tuple[float, ...] = ()
        if matched_prior is not None and z is not None:
            z_score_history = matched_prior.z_score_history + (z,)
            is_trend = _is_trend(z_score_history, field_name)
        elif z is not None:
            z_score_history = (z,)

        anomaly_type = _classify_anomaly_type(consecutive_count, is_trend)

        candidates.append(AnomalyCandidate(
            signal_field=field_name,
            signal_value=getattr(feature_window, _FIELD_TO_FEATURE_ATTR[field_name], None),
            baseline_mean=bl.baseline_mean,
            baseline_std=bl.baseline_std,
            z_score=z,
            anomaly_type=anomaly_type,
            severity=severity,
            confidence=confidence,
            supply_related=supply_related,
            startup_adjacent=startup_adjacent,
            mode_change=mode_change,
            recurring=recurring,
            time_window=time_window,
            correlated_signals=correlated,
            baseline_version=bl.baseline_version,
            occurred_at=None,
            ended_at=None,
            duration_seconds=None,
            merged_window_count=consecutive_count,
            z_score_history=z_score_history,
        ))

    return candidates


def _exceeds_gap_threshold(
    prior: AnomalyCandidate,
    current_window_start,
    gap_threshold: int,
) -> bool:
    """Return True if the gap between *prior* and *current_window_start*
    exceeds *gap_threshold* feature-window intervals.

    The average window duration is estimated from the prior event's
    ``occurred_at`` / ``ended_at`` span divided by ``merged_window_count``.
    Returns ``False`` when timestamps are unavailable (cannot determine gap).
    """
    if prior.ended_at is None or prior.occurred_at is None:
        return False
    if prior.merged_window_count <= 0:
        return False
    span_seconds = (prior.ended_at - prior.occurred_at).total_seconds()
    avg_window_seconds = span_seconds / prior.merged_window_count
    if avg_window_seconds <= 0:
        return False
    gap_seconds = (current_window_start - prior.ended_at).total_seconds()
    if gap_seconds <= 0:
        return False
    gap_windows = gap_seconds / avg_window_seconds
    return gap_windows > gap_threshold


def merge_events(
    new_candidates: Sequence[AnomalyCandidate],
    prior_open_events: Sequence[AnomalyCandidate],
    current_window_start: Optional[object] = None,
    current_window_end: Optional[object] = None,
    gap_threshold: int = 1,
) -> tuple[list[AnomalyCandidate], list[AnomalyCandidate]]:
    """Merge new candidates with prior open events.

    An open event is a prior AnomalyCandidate with ended_at=None that
    matches (device_id, signal_field, severity).

    Parameters
    ----------
    new_candidates : sequence of AnomalyCandidate
        Freshly detected candidates from the current feature window.
    prior_open_events : sequence of AnomalyCandidate
        Previously detected events that have not been closed yet.
    current_window_start, current_window_end : datetime or None
        Timestamps for the current feature window.
    gap_threshold : int
        Maximum allowed gap in feature-window intervals before starting
        a new event.  Default 1 (no gap allowed).

    Returns
    -------
    tuple of (extended_events, new_events)
        extended_events: prior events that were extended with new data.
        new_events: brand-new events that could not be merged.
    """
    extended: list[AnomalyCandidate] = []
    new_events: list[AnomalyCandidate] = []

    for cand in new_candidates:
        match: Optional[AnomalyCandidate] = None
        for pe in prior_open_events:
            if (
                pe.signal_field == cand.signal_field
                and pe.severity == cand.severity
                and pe.merged_window_count < _MAX_MERGE_WINDOWS
            ):
                match = pe
                break

        if match is not None and current_window_start is not None:
            if _exceeds_gap_threshold(match, current_window_start, gap_threshold):
                match = None

        if match is not None:
            new_merged_count = match.merged_window_count + 1
            new_z = cand.z_score if cand.z_score is not None else match.z_score
            if match.z_score is not None and cand.z_score is not None:
                new_z = cand.z_score if abs(cand.z_score) > abs(match.z_score) else match.z_score

            new_signal_value = cand.signal_value if cand.z_score is not None and match.z_score is not None and abs(cand.z_score) >= abs(match.z_score) else match.signal_value

            anomaly_type = match.anomaly_type
            if new_merged_count >= 3 and anomaly_type == "deviation":
                anomaly_type = "persistent"

            occurred_at = match.occurred_at if match.occurred_at is not None else current_window_start
            ended_at = current_window_end
            duration_seconds = None
            if occurred_at is not None and ended_at is not None:
                duration_seconds = int((ended_at - occurred_at).total_seconds())

            new_z_score_history = match.z_score_history
            if cand.z_score is not None:
                new_z_score_history = match.z_score_history + (cand.z_score,)

            extended.append(AnomalyCandidate(
                signal_field=cand.signal_field,
                signal_value=new_signal_value,
                baseline_mean=match.baseline_mean,
                baseline_std=match.baseline_std,
                z_score=new_z,
                anomaly_type=anomaly_type,
                severity=cand.severity,
                confidence=cand.confidence if cand.confidence > match.confidence else match.confidence,
                supply_related=cand.supply_related or match.supply_related,
                startup_adjacent=cand.startup_adjacent or match.startup_adjacent,
                mode_change=cand.mode_change or match.mode_change,
                recurring=cand.recurring or match.recurring,
                time_window=cand.time_window,
                correlated_signals=cand.correlated_signals if len(cand.correlated_signals) >= len(match.correlated_signals) else match.correlated_signals,
                baseline_version=match.baseline_version,
                occurred_at=occurred_at,
                ended_at=ended_at,
                duration_seconds=duration_seconds,
                merged_window_count=new_merged_count,
                z_score_history=new_z_score_history,
            ))
        else:
            occurred_at = current_window_start
            ended_at = current_window_end
            duration_seconds = None
            if occurred_at is not None and ended_at is not None:
                duration_seconds = int((ended_at - occurred_at).total_seconds())

            new_events.append(AnomalyCandidate(
                signal_field=cand.signal_field,
                signal_value=cand.signal_value,
                baseline_mean=cand.baseline_mean,
                baseline_std=cand.baseline_std,
                z_score=cand.z_score,
                anomaly_type=cand.anomaly_type,
                severity=cand.severity,
                confidence=cand.confidence,
                supply_related=cand.supply_related,
                startup_adjacent=cand.startup_adjacent,
                mode_change=cand.mode_change,
                recurring=cand.recurring,
                time_window=cand.time_window,
                correlated_signals=cand.correlated_signals,
                baseline_version=cand.baseline_version,
                occurred_at=occurred_at,
                ended_at=ended_at,
                duration_seconds=duration_seconds,
                merged_window_count=1,
                z_score_history=cand.z_score_history,
            ))

    return extended, new_events
