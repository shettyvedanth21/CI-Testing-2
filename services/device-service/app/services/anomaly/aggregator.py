"""Pure anomaly aggregation — no DB, no HTTP, no I/O.

Aggregates anomaly events into daily and weekly counts.
Follows the explicit counting policy:
  - supply_related events contribute to total_count and supply_related_count
    but NOT to mild_count/strong_count/severe_count.
  - startup_adjacent events contribute to total_count
    but NOT to mild_count/strong_count/severe_count.
  - mode_change events contribute to total_count AND their severity bucket
    (mild/strong/severe) — the flag is contextual metadata, not a severity suppressor.
  - Each merged event counts as one event regardless of merge span.
"""

from __future__ import annotations

import math
from collections import Counter
from datetime import date, timedelta
from typing import Optional, Sequence

from .types import AnomalyCandidate, DailyCountResult, SignalBreakdownEntry, WeeklyCountResult


def _is_valid(v: Optional[float]) -> bool:
    return v is not None and math.isfinite(v)


def aggregate_daily_counts(
    events: Sequence[AnomalyCandidate],
    target_date: date,
) -> Optional[DailyCountResult]:
    """Aggregate anomaly events into daily counts for a single device-date.

    Parameters
    ----------
    events : sequence of AnomalyCandidate
        All events for a single device on the target date.
    target_date : date
        The calendar date being aggregated.

    Returns
    -------
    DailyCountResult or None
        None if there are no events for this date.
    """
    if not events:
        return None

    total_count = len(events)
    mild_count = 0
    strong_count = 0
    severe_count = 0
    supply_related_count = 0
    signal_counter: Counter[str] = Counter()
    signal_mild: Counter[str] = Counter()
    signal_strong: Counter[str] = Counter()
    signal_severe: Counter[str] = Counter()
    confidence_values: list[float] = []

    for e in events:
        signal_counter[e.signal_field] += 1
        if _is_valid(e.confidence):
            confidence_values.append(e.confidence)

        if e.supply_related:
            supply_related_count += 1
        elif e.startup_adjacent:
            pass
        else:
            if e.severity == "mild":
                mild_count += 1
                signal_mild[e.signal_field] += 1
            elif e.severity == "strong":
                strong_count += 1
                signal_strong[e.signal_field] += 1
            elif e.severity == "severe":
                severe_count += 1
                signal_severe[e.signal_field] += 1

    # top_signal: most frequent signal_field. Ties broken alphabetically.
    top_signal: Optional[str] = None
    if signal_counter:
        max_count = max(signal_counter.values())
        top_candidates = sorted(f for f, c in signal_counter.items() if c == max_count)
        top_signal = top_candidates[0]

    # avg_confidence: mean of all non-None confidence values.
    avg_confidence: Optional[float] = None
    if confidence_values:
        avg_confidence = sum(confidence_values) / len(confidence_values)
        if not math.isfinite(avg_confidence):
            avg_confidence = None

    signal_breakdown = tuple(
        SignalBreakdownEntry(
            field_name=f,
            count=signal_counter[f],
            mild=signal_mild.get(f, 0),
            strong=signal_strong.get(f, 0),
            severe=signal_severe.get(f, 0),
        )
        for f in sorted(signal_counter.keys())
    )

    return DailyCountResult(
        date=target_date,
        total_count=total_count,
        mild_count=mild_count,
        strong_count=strong_count,
        severe_count=severe_count,
        supply_related_count=supply_related_count,
        top_signal=top_signal,
        avg_confidence=avg_confidence,
        signal_breakdown=signal_breakdown,
    )


def aggregate_weekly_counts(
    daily_counts: Sequence[DailyCountResult],
    week_start_date: date,
    prior_week_total: Optional[int] = None,
) -> Optional[WeeklyCountResult]:
    """Aggregate daily counts into a weekly count for a single device.

    Parameters
    ----------
    daily_counts : sequence of DailyCountResult
        Daily counts for the 7 days of the target week.
    week_start_date : date
        The Monday of the ISO week.
    prior_week_total : int or None
        The total_count from the prior week, for week_over_week_change.
        None if no prior week data exists.

    Returns
    -------
    WeeklyCountResult or None
        None if there are no daily counts for this week.
    """
    if not daily_counts:
        return None

    total_count = sum(d.total_count for d in daily_counts)
    mild_count = sum(d.mild_count for d in daily_counts)
    strong_count = sum(d.strong_count for d in daily_counts)
    severe_count = sum(d.severe_count for d in daily_counts)
    supply_related_count = sum(d.supply_related_count for d in daily_counts)

    signal_counts: dict[str, int] = {}
    for d in daily_counts:
        if d.top_signal:
            signal_counts[d.top_signal] = signal_counts.get(d.top_signal, 0) + d.total_count
    top_signal = max(signal_counts, key=signal_counts.get) if signal_counts else None

    conf_values = [d.avg_confidence for d in daily_counts if d.avg_confidence is not None]
    avg_confidence = sum(conf_values) / len(conf_values) if conf_values else None

    merged_signal: dict[str, dict[str, int]] = {}
    for d in daily_counts:
        for e in d.signal_breakdown:
            acc = merged_signal.setdefault(e.field_name, {"count": 0, "mild": 0, "strong": 0, "severe": 0})
            acc["count"] += e.count
            acc["mild"] += e.mild
            acc["strong"] += e.strong
            acc["severe"] += e.severe
    signal_breakdown = tuple(
        SignalBreakdownEntry(
            field_name=f,
            count=merged_signal[f]["count"],
            mild=merged_signal[f]["mild"],
            strong=merged_signal[f]["strong"],
            severe=merged_signal[f]["severe"],
        )
        for f in sorted(merged_signal.keys())
    )

    week_over_week_change: Optional[int] = None
    if prior_week_total is not None:
        week_over_week_change = total_count - prior_week_total

    return WeeklyCountResult(
        week_start_date=week_start_date,
        total_count=total_count,
        mild_count=mild_count,
        strong_count=strong_count,
        severe_count=severe_count,
        supply_related_count=supply_related_count,
        top_signal=top_signal,
        avg_confidence=avg_confidence,
        signal_breakdown=signal_breakdown,
        week_over_week_change=week_over_week_change,
    )
