"""Lightweight dict-building helpers for the anomaly pipeline.

Transforms pure computation results into flat dicts matching ORM column
names.  No SQLAlchemy imports, no DB access, no I/O.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from .types import AnomalyFieldBaseline, AnomalyCandidate, DailyCountResult, SignalBreakdownEntry, WeeklyCountResult


def build_anomaly_baseline_dict(
    baseline: AnomalyFieldBaseline,
    tenant_id: str,
    device_id: str,
) -> dict:
    """Build a flat dict matching MachineAnomalyBaseline ORM columns."""
    return {
        "tenant_id": tenant_id,
        "device_id": device_id,
        "field_name": baseline.field_name,
        "time_window": baseline.time_window,
        "baseline_mean": baseline.baseline_mean,
        "baseline_std": baseline.baseline_std,
        "baseline_median": baseline.baseline_median,
        "baseline_mad": baseline.baseline_mad,
        "baseline_p05": baseline.baseline_p05,
        "baseline_p95": baseline.baseline_p95,
        "reading_count": baseline.reading_count if baseline.reading_count > 0 else None,
        "quality_score": baseline.quality_score if baseline.quality_score > 0.0 else None,
        "learned_from_ts": baseline.learned_from_ts,
        "learned_to_ts": baseline.learned_to_ts,
        "status": baseline.status,
        "baseline_version": baseline.baseline_version,
    }


def build_anomaly_event_dict(
    candidate: AnomalyCandidate,
    tenant_id: str,
    device_id: str,
    computed_at: Optional[datetime] = None,
) -> dict:
    """Build a flat dict matching MachineAnomalyEvent ORM columns."""
    correlated_json: Optional[str] = None
    if candidate.correlated_signals:
        correlated_json = json.dumps(list(candidate.correlated_signals))

    duration_seconds = candidate.duration_seconds
    if duration_seconds is None and candidate.occurred_at is not None and candidate.ended_at is not None:
        duration_seconds = int((candidate.ended_at - candidate.occurred_at).total_seconds())

    return {
        "tenant_id": tenant_id,
        "device_id": device_id,
        "occurred_at": candidate.occurred_at,
        "ended_at": candidate.ended_at,
        "duration_seconds": duration_seconds,
        "signal_field": candidate.signal_field,
        "signal_value": candidate.signal_value,
        "baseline_mean": candidate.baseline_mean,
        "baseline_std": candidate.baseline_std,
        "z_score": candidate.z_score,
        "anomaly_type": candidate.anomaly_type,
        "severity": candidate.severity,
        "confidence": candidate.confidence if candidate.confidence > 0.0 else None,
        "supply_related": candidate.supply_related,
        "startup_adjacent": candidate.startup_adjacent,
        "mode_change": candidate.mode_change,
        "recurring": candidate.recurring,
        "time_window": candidate.time_window,
        "correlated_signals_json": correlated_json,
        "baseline_version": candidate.baseline_version,
    }


def build_daily_count_dict(
    result: DailyCountResult,
    tenant_id: str,
    device_id: str,
) -> dict:
    signal_breakdown_json: Optional[str] = None
    if result.signal_breakdown:
        signal_breakdown_json = json.dumps([
            {"field_name": e.field_name, "count": e.count,
             "mild": e.mild, "strong": e.strong, "severe": e.severe}
            for e in result.signal_breakdown
        ])

    return {
        "tenant_id": tenant_id,
        "device_id": device_id,
        "date": result.date,
        "total_count": result.total_count,
        "mild_count": result.mild_count,
        "strong_count": result.strong_count,
        "severe_count": result.severe_count,
        "supply_related_count": result.supply_related_count,
        "top_signal": result.top_signal,
        "avg_confidence": result.avg_confidence,
        "signal_breakdown_json": signal_breakdown_json,
    }


def build_weekly_count_dict(
    result: WeeklyCountResult,
    tenant_id: str,
    device_id: str,
) -> dict:
    """Build a flat dict matching MachineAnomalyWeeklyCount ORM columns."""
    signal_breakdown_json: Optional[str] = None
    if result.signal_breakdown:
        signal_breakdown_json = json.dumps([
            {"field_name": e.field_name, "count": e.count,
             "mild": e.mild, "strong": e.strong, "severe": e.severe}
            for e in result.signal_breakdown
        ])

    return {
        "tenant_id": tenant_id,
        "device_id": device_id,
        "week_start_date": result.week_start_date,
        "total_count": result.total_count,
        "mild_count": result.mild_count,
        "strong_count": result.strong_count,
        "severe_count": result.severe_count,
        "supply_related_count": result.supply_related_count,
        "top_signal": result.top_signal,
        "avg_confidence": result.avg_confidence,
        "signal_breakdown_json": signal_breakdown_json,
        "week_over_week_change": result.week_over_week_change,
    }
