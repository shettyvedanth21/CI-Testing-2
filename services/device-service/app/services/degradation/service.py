"""Orchestration helpers for the degradation pipeline.

Bridges pure computation results into ORM-ready dictionaries and provides
async persistence helpers for the scheduler to upsert rows into
``MachineHealthFeatureWindow``, ``MachineHealthBaseline``,
``MachineHealthLatest``, and ``MachineHealthHistory``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional, Sequence

from sqlalchemy import select, delete, and_, desc
from sqlalchemy.ext.asyncio import AsyncSession

from .baseline_learner import learn_baseline
from .feature_aggregator import aggregate_feature_window
from .scorer import compute_degradation_score
from .types import (
    BaselineLearnResult,
    BaselineInput,
    FeatureWindowInput,
    FeatureWindowResult,
    PriorScoreEntry,
    ScoreResult,
    TelemetrySample,
)


def build_feature_window_from_samples(
    samples: Sequence[TelemetrySample],
    tenant_id: str,
    device_id: str,
    window_start: datetime,
    window_end: datetime,
    expected_sample_count: int = 0,
) -> dict:
    result = aggregate_feature_window(samples, expected_sample_count, window_start, window_end)
    window_minutes = max(0, int((window_end - window_start).total_seconds()) // 60)

    return {
        "tenant_id": tenant_id,
        "device_id": device_id,
        "window_start": window_start,
        "window_end": window_end,
        "window_minutes": window_minutes,
        "running_state": result.running_state,
        "current_avg_mean": result.window.current_avg_mean,
        "current_avg_std": result.window.current_avg_std,
        "current_avg_p95": result.window.current_avg_p95,
        "current_l1_mean": result.window.current_l1_mean,
        "current_l2_mean": result.window.current_l2_mean,
        "current_l3_mean": result.window.current_l3_mean,
        "power_mean": result.window.power_mean,
        "power_p95": result.window.power_p95,
        "power_factor_mean": result.window.power_factor_mean,
        "voltage_avg_mean": result.window.voltage_avg_mean,
        "voltage_imbalance": result.window.voltage_imbalance,
        "phase_imbalance": result.window.phase_imbalance,
        "frequency_mean": result.window.frequency_mean,
        "energy_kwh": result.window.energy_kwh,
        "telemetry_coverage": result.telemetry_coverage,
        "sample_count": result.sample_count,
    }


def learn_baseline_from_windows(
    feature_windows: Sequence[FeatureWindowResult],
    tenant_id: str,
    device_id: str,
    baseline_version: int = 1,
    minimum_days: int = 7,
) -> dict:
    learn_result = learn_baseline(feature_windows, minimum_days=minimum_days)
    status = (
        "active"
        if learn_result.quality_score >= 0.3 and learn_result.learning_window_count >= 3
        else "candidate"
    )

    learned_from_start: Optional[datetime] = None
    learned_from_end: Optional[datetime] = None
    steady_windows = [w for w in feature_windows if w.running_state == "STEADY_RUNNING"]
    if steady_windows:
        starts = [w.window_start for w in steady_windows if w.window_start is not None]
        ends = [w.window_end for w in steady_windows if w.window_end is not None]
        if starts:
            learned_from_start = min(starts)
        if ends:
            learned_from_end = max(ends)

    return {
        "tenant_id": tenant_id,
        "device_id": device_id,
        "baseline_version": baseline_version,
        "status": status,
        "current_avg_mean": learn_result.baseline_input.current_avg_mean,
        "current_avg_std": learn_result.baseline_input.current_avg_std,
        "power_mean": learn_result.baseline_input.power_mean,
        "power_p95": learn_result.baseline_input.power_p95,
        "power_factor_mean": learn_result.baseline_input.power_factor_mean,
        "voltage_avg_mean": learn_result.baseline_input.voltage_avg_mean,
        "phase_imbalance_mean": learn_result.baseline_input.phase_imbalance_mean,
        "frequency_mean": learn_result.baseline_input.frequency_mean,
        "quality_score": learn_result.quality_score,
        "quality_band": learn_result.quality_band,
        "signal_completeness": learn_result.signal_completeness,
        "steady_running_coverage": learn_result.steady_running_coverage,
        "learning_window_count": learn_result.learning_window_count,
        "learned_from_start": learned_from_start,
        "learned_from_end": learned_from_end,
    }


def build_latest_score_snapshot(
    score_result: ScoreResult,
    tenant_id: str,
    device_id: str,
    baseline_version: Optional[int] = None,
    baseline_quality: Optional[str] = None,
    computed_at: Optional[datetime] = None,
    source_window_start: Optional[datetime] = None,
    source_window_end: Optional[datetime] = None,
    worker_version: str = "1",
) -> dict:
    top_reasons_json = json.dumps(list(score_result.top_reasons)) if score_result.top_reasons else None
    contributions_json = json.dumps(
        [{"signal": c.signal, "weight": c.weight, "drift": c.drift, "available": c.available,
          "observed_value": c.observed_value, "baseline_value": c.baseline_value, "raw_drift": c.raw_drift}
         for c in score_result.contributions]
    ) if score_result.contributions else None

    signal_completeness: float | None = None
    if score_result.contributions:
        total = len(score_result.contributions)
        available = sum(1 for c in score_result.contributions if c.available)
        if total > 0:
            signal_completeness = available / total

    return {
        "device_id": device_id,
        "tenant_id": tenant_id,
        "score": score_result.score,
        "status": score_result.status,
        "confidence": score_result.confidence,
        "baseline_version": baseline_version,
        "baseline_quality": baseline_quality,
        "top_reasons_json": top_reasons_json,
        "contributions_json": contributions_json,
        "signal_completeness": signal_completeness,
        "computed_at": computed_at or datetime.now(timezone.utc),
        "source_window_start": source_window_start,
        "source_window_end": source_window_end,
        "worker_version": worker_version,
    }


def build_history_entry(
    score_result: ScoreResult,
    tenant_id: str,
    device_id: str,
    baseline_version: Optional[int] = None,
    computed_at: Optional[datetime] = None,
) -> dict:
    contributions_json = json.dumps(
        [{"signal": c.signal, "weight": c.weight, "drift": c.drift, "available": c.available,
          "observed_value": c.observed_value, "baseline_value": c.baseline_value, "raw_drift": c.raw_drift}
         for c in score_result.contributions]
    ) if score_result.contributions else None

    return {
        "tenant_id": tenant_id,
        "device_id": device_id,
        "computed_at": computed_at or datetime.now(timezone.utc),
        "score": score_result.score,
        "status": score_result.status,
        "confidence": score_result.confidence,
        "baseline_version": baseline_version,
        "contributions_json": contributions_json,
    }


async def persist_feature_window(db: AsyncSession, window_dict: dict) -> None:
    from app.models.device import MachineHealthFeatureWindow

    tenant_id = window_dict["tenant_id"]
    device_id = window_dict["device_id"]
    window_start = window_dict["window_start"]

    result = await db.execute(
        select(MachineHealthFeatureWindow).where(
            and_(
                MachineHealthFeatureWindow.tenant_id == tenant_id,
                MachineHealthFeatureWindow.device_id == device_id,
                MachineHealthFeatureWindow.window_start == window_start,
            )
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        for key, value in window_dict.items():
            if key not in ("tenant_id", "device_id", "window_start"):
                setattr(existing, key, value)
    else:
        db.add(MachineHealthFeatureWindow(**window_dict))
    await db.flush()


async def persist_baseline(db: AsyncSession, baseline_dict: dict) -> None:
    from app.models.device import MachineHealthBaseline

    tenant_id = baseline_dict["tenant_id"]
    device_id = baseline_dict["device_id"]

    result = await db.execute(
        select(MachineHealthBaseline).where(
            and_(
                MachineHealthBaseline.tenant_id == tenant_id,
                MachineHealthBaseline.device_id == device_id,
                MachineHealthBaseline.status.in_(["active", "candidate"]),
            )
        ).order_by(MachineHealthBaseline.baseline_version.desc())
    )
    existing = result.scalars().first()
    if existing:
        for key, value in baseline_dict.items():
            if key not in ("tenant_id", "device_id"):
                setattr(existing, key, value)
    else:
        db.add(MachineHealthBaseline(**baseline_dict))
    await db.flush()


async def persist_latest_snapshot(db: AsyncSession, snapshot_dict: dict) -> None:
    from app.models.device import MachineHealthLatest

    device_id = snapshot_dict["device_id"]
    tenant_id = snapshot_dict["tenant_id"]

    result = await db.execute(
        select(MachineHealthLatest).where(
            and_(
                MachineHealthLatest.device_id == device_id,
                MachineHealthLatest.tenant_id == tenant_id,
            )
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        for key, value in snapshot_dict.items():
            if key not in ("device_id", "tenant_id"):
                setattr(existing, key, value)
    else:
        db.add(MachineHealthLatest(**snapshot_dict))
    await db.flush()


async def persist_history_entry(db: AsyncSession, history_dict: dict) -> None:
    from app.models.device import MachineHealthHistory

    db.add(MachineHealthHistory(**history_dict))
    await db.flush()


async def load_feature_windows_for_device(
    db: AsyncSession,
    tenant_id: str,
    device_id: str,
) -> list[FeatureWindowResult]:
    from app.models.device import MachineHealthFeatureWindow

    result = await db.execute(
        select(MachineHealthFeatureWindow).where(
            and_(
                MachineHealthFeatureWindow.tenant_id == tenant_id,
                MachineHealthFeatureWindow.device_id == device_id,
            )
        ).order_by(desc(MachineHealthFeatureWindow.window_start)).limit(5)
    )
    rows = list(reversed(result.scalars().all()))
    return [
        FeatureWindowResult(
            window=FeatureWindowInput(
                current_avg_mean=r.current_avg_mean,
                current_avg_std=r.current_avg_std,
                current_avg_p95=r.current_avg_p95,
                current_l1_mean=r.current_l1_mean,
                current_l2_mean=r.current_l2_mean,
                current_l3_mean=r.current_l3_mean,
                power_mean=r.power_mean,
                power_p95=r.power_p95,
                power_factor_mean=r.power_factor_mean,
                voltage_avg_mean=r.voltage_avg_mean,
                voltage_imbalance=r.voltage_imbalance,
                phase_imbalance=r.phase_imbalance,
                frequency_mean=r.frequency_mean,
                energy_kwh=r.energy_kwh,
            ),
            running_state=r.running_state,
            telemetry_coverage=r.telemetry_coverage or 0.0,
            sample_count=r.sample_count or 0,
            window_start=r.window_start,
            window_end=r.window_end,
        )
        for r in rows
    ]


async def load_feature_windows_for_baseline(
    db: AsyncSession,
    tenant_id: str,
    device_id: str,
    *,
    minimum_days: int,
    interval_seconds: int,
) -> list[FeatureWindowResult]:
    from app.models.device import MachineHealthFeatureWindow

    windows_per_day = max(1, int((24 * 60 * 60) / max(300, interval_seconds)))
    window_limit = max(24, (max(1, minimum_days) * windows_per_day) + windows_per_day)

    result = await db.execute(
        select(MachineHealthFeatureWindow).where(
            and_(
                MachineHealthFeatureWindow.tenant_id == tenant_id,
                MachineHealthFeatureWindow.device_id == device_id,
            )
        ).order_by(desc(MachineHealthFeatureWindow.window_start)).limit(window_limit)
    )
    rows = list(reversed(result.scalars().all()))
    return [
        FeatureWindowResult(
            window=FeatureWindowInput(
                current_avg_mean=r.current_avg_mean,
                current_avg_std=r.current_avg_std,
                current_avg_p95=r.current_avg_p95,
                current_l1_mean=r.current_l1_mean,
                current_l2_mean=r.current_l2_mean,
                current_l3_mean=r.current_l3_mean,
                power_mean=r.power_mean,
                power_p95=r.power_p95,
                power_factor_mean=r.power_factor_mean,
                voltage_avg_mean=r.voltage_avg_mean,
                voltage_imbalance=r.voltage_imbalance,
                phase_imbalance=r.phase_imbalance,
                frequency_mean=r.frequency_mean,
                energy_kwh=r.energy_kwh,
            ),
            running_state=r.running_state,
            telemetry_coverage=r.telemetry_coverage or 0.0,
            sample_count=r.sample_count or 0,
            window_start=r.window_start,
            window_end=r.window_end,
        )
        for r in rows
    ]


async def load_latest_baseline_for_device(
    db: AsyncSession,
    tenant_id: str,
    device_id: str,
) -> Optional[BaselineInput]:
    from app.models.device import MachineHealthBaseline

    result = await db.execute(
        select(MachineHealthBaseline).where(
            and_(
                MachineHealthBaseline.tenant_id == tenant_id,
                MachineHealthBaseline.device_id == device_id,
                MachineHealthBaseline.status.in_(["active", "candidate"]),
            )
        ).order_by(MachineHealthBaseline.baseline_version.desc())
    )
    row = result.scalars().first()
    if row is None:
        return None
    return BaselineInput(
        current_avg_mean=row.current_avg_mean,
        current_avg_std=row.current_avg_std,
        power_mean=row.power_mean,
        power_p95=row.power_p95,
        power_factor_mean=row.power_factor_mean,
        voltage_avg_mean=row.voltage_avg_mean,
        phase_imbalance_mean=row.phase_imbalance_mean,
        frequency_mean=row.frequency_mean,
        quality_score=row.quality_score or 0.0,
        quality_band=row.quality_band,
    )


async def load_prior_scores_for_device(
    db: AsyncSession,
    tenant_id: str,
    device_id: str,
    limit: int = 10,
) -> list[PriorScoreEntry]:
    from app.models.device import MachineHealthHistory

    result = await db.execute(
        select(MachineHealthHistory).where(
            and_(
                MachineHealthHistory.tenant_id == tenant_id,
                MachineHealthHistory.device_id == device_id,
            )
        ).order_by(MachineHealthHistory.computed_at.desc()).limit(limit)
    )
    rows = result.scalars().all()
    return [
        PriorScoreEntry(score=r.score or 1.0, computed_at=r.computed_at)
        for r in reversed(rows)
        if r.score is not None
    ]


async def cleanup_old_degradation_rows(
    db: AsyncSession,
    retention_days: int,
    batch_size: int = 1000,
) -> dict[str, int]:
    from app.models.device import MachineHealthFeatureWindow, MachineHealthHistory

    cutoff = datetime.now(timezone.utc) - __import__("datetime").timedelta(days=retention_days)
    total_deleted = 0

    for Model, label in [
        (MachineHealthFeatureWindow, "feature_windows"),
        (MachineHealthHistory, "history"),
    ]:
        result = await db.execute(
            delete(Model).where(Model.created_at < cutoff)
        )
        count = result.rowcount
        total_deleted += count

    await db.flush()
    return {"deleted": total_deleted}


async def score_device(
    db: AsyncSession,
    tenant_id: str,
    device_id: str,
) -> Optional[ScoreResult]:
    baseline = await load_latest_baseline_for_device(db, tenant_id, device_id)
    if baseline is None:
        return None

    windows = await load_feature_windows_for_device(db, tenant_id, device_id)
    recent_windows = [w.window for w in windows[-5:]] if windows else []

    prior_scores = await load_prior_scores_for_device(db, tenant_id, device_id)

    score_result = compute_degradation_score(baseline, recent_windows, prior_scores or None)
    return score_result
