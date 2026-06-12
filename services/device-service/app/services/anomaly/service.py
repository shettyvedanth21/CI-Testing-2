"""Orchestration helpers for the anomaly detection pipeline.

Bridges pure computation results into async persistence helpers and provides
end-to-end per-device detection, baseline lifecycle, aggregation, and cleanup.
No parallel dict-builder implementations — imports from helpers.py.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Optional, Sequence

from .tz import get_platform_tz

from sqlalchemy import select, delete, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from .baseline_learner import learn_anomaly_baseline
from .detector import detect_anomalies, merge_events
from .aggregator import aggregate_daily_counts, aggregate_weekly_counts
from .helpers import (
    build_anomaly_baseline_dict,
    build_anomaly_event_dict,
    build_daily_count_dict,
    build_weekly_count_dict,
)
from .types import (
    AnomalyCandidate,
    AnomalyFieldBaseline,
    DailyCountResult,
    SUPPORTED_FIELDS,
)

_BASELINE_CHURN_HYSTERESIS = 0.1
_DAILY_COUNT_RETENTION_DAYS = 365
_WEEKLY_COUNT_RETENTION_DAYS = 730
_RETIRED_BASELINE_RETENTION_DAYS = 180
_INSUFFICIENT_CANDIDATE_RETENTION_DAYS = 30
_INSUFFICIENT_QUALITY_THRESHOLD = 0.1


async def load_active_anomaly_baselines_for_device(
    db: AsyncSession,
    tenant_id: str,
    device_id: str,
) -> list[AnomalyFieldBaseline]:
    from app.models.device import MachineAnomalyBaseline

    result = await db.execute(
        select(MachineAnomalyBaseline).where(
            and_(
                MachineAnomalyBaseline.tenant_id == tenant_id,
                MachineAnomalyBaseline.device_id == device_id,
                MachineAnomalyBaseline.status == "active",
            )
        )
    )
    rows = result.scalars().all()

    version_map: dict[str, int] = {}
    for r in rows:
        if r.field_name not in version_map or r.baseline_version > version_map[r.field_name]:
            version_map[r.field_name] = r.baseline_version

    latest = [r for r in rows if r.baseline_version == version_map.get(r.field_name, 0)]

    return [
        AnomalyFieldBaseline(
            field_name=r.field_name,
            time_window=r.time_window or "5min",
            baseline_mean=r.baseline_mean,
            baseline_std=r.baseline_std,
            baseline_median=r.baseline_median,
            baseline_mad=r.baseline_mad,
            baseline_p05=r.baseline_p05,
            baseline_p95=r.baseline_p95,
            reading_count=r.reading_count or 0,
            quality_score=r.quality_score or 0.0,
            quality_band="insufficient" if (r.quality_score or 0.0) < 0.50 else
                         "low" if (r.quality_score or 0.0) < 0.70 else
                         "medium" if (r.quality_score or 0.0) < 0.85 else "high",
            learned_from_ts=r.learned_from_ts,
            learned_to_ts=r.learned_to_ts,
            status=r.status,
            baseline_version=r.baseline_version,
        )
        for r in latest
    ]


async def load_recent_anomaly_events_for_device(
    db: AsyncSession,
    tenant_id: str,
    device_id: str,
    limit: int = 50,
) -> list[AnomalyCandidate]:
    from app.models.device import MachineAnomalyEvent

    result = await db.execute(
        select(MachineAnomalyEvent).where(
            and_(
                MachineAnomalyEvent.tenant_id == tenant_id,
                MachineAnomalyEvent.device_id == device_id,
            )
        ).order_by(MachineAnomalyEvent.occurred_at.desc()).limit(limit)
    )
    rows = result.scalars().all()

    events = []
    for r in reversed(rows):
        correlated: tuple[str, ...] = ()
        if r.correlated_signals_json:
            try:
                parsed = json.loads(r.correlated_signals_json)
                correlated = tuple(parsed)
            except (json.JSONDecodeError, TypeError):
                pass

        events.append(AnomalyCandidate(
            signal_field=r.signal_field,
            signal_value=r.signal_value,
            baseline_mean=r.baseline_mean,
            baseline_std=r.baseline_std,
            z_score=r.z_score,
            anomaly_type=r.anomaly_type,
            severity=r.severity,
            confidence=r.confidence or 0.0,
            supply_related=r.supply_related,
            startup_adjacent=r.startup_adjacent,
            mode_change=r.mode_change,
            recurring=r.recurring,
            time_window=r.time_window or "5min",
            correlated_signals=correlated,
            baseline_version=r.baseline_version,
            occurred_at=r.occurred_at,
            ended_at=r.ended_at,
            duration_seconds=r.duration_seconds,
        ))
    return events


async def load_prior_week_total_for_device(
    db: AsyncSession,
    tenant_id: str,
    device_id: str,
    prior_week_start: date,
) -> Optional[int]:
    from app.models.device import MachineAnomalyWeeklyCount

    result = await db.execute(
        select(MachineAnomalyWeeklyCount.total_count).where(
            and_(
                MachineAnomalyWeeklyCount.tenant_id == tenant_id,
                MachineAnomalyWeeklyCount.device_id == device_id,
                MachineAnomalyWeeklyCount.week_start_date == prior_week_start,
            )
        )
    )
    row = result.scalar_one_or_none()
    return row


async def load_feature_windows_for_anomaly(
    db: AsyncSession,
    tenant_id: str,
    device_id: str,
    limit: int = 10,
    *,
    minimum_days: int | None = None,
    interval_seconds: int = 300,
) -> list:
    from app.services.degradation.service import (
        load_feature_windows_for_baseline,
        load_feature_windows_for_device,
    )

    if minimum_days is not None:
        return await load_feature_windows_for_baseline(
            db,
            tenant_id,
            device_id,
            minimum_days=minimum_days,
            interval_seconds=interval_seconds,
        )
    recent_windows = await load_feature_windows_for_device(db, tenant_id, device_id)
    return recent_windows[-max(1, limit):]


async def persist_anomaly_baselines(
    db: AsyncSession,
    tenant_id: str,
    device_id: str,
    new_baselines: Sequence[AnomalyFieldBaseline],
) -> int:
    from app.models.device import MachineAnomalyBaseline

    persisted = 0
    for bl in new_baselines:
        existing_result = await db.execute(
            select(MachineAnomalyBaseline).where(
                and_(
                    MachineAnomalyBaseline.tenant_id == tenant_id,
                    MachineAnomalyBaseline.device_id == device_id,
                    MachineAnomalyBaseline.field_name == bl.field_name,
                )
            ).order_by(MachineAnomalyBaseline.baseline_version.desc())
        )
        existing_rows = existing_result.scalars().all()
        latest = existing_rows[0] if existing_rows else None
        active = next((row for row in existing_rows if row.status == "active"), None)

        bl_dict = build_anomaly_baseline_dict(bl, tenant_id, device_id)

        # Keep the latest candidate row up to date until it becomes usable.
        # This avoids unique-key collisions on (tenant, device, field, time_window, version)
        # while still preserving versioned inserts for active-baseline replacements.
        if active is None and latest is not None:
            for key, value in bl_dict.items():
                if key in {"tenant_id", "device_id", "field_name", "time_window", "created_at"}:
                    continue
                setattr(latest, key, value)
            persisted += 1
            continue

        if active is not None:
            improvement = bl.quality_score - (active.quality_score or 0.0)
            if bl.status != "active" or improvement < _BASELINE_CHURN_HYSTERESIS:
                continue
            active.status = "retired"
            bl_dict["baseline_version"] = (latest.baseline_version if latest is not None else active.baseline_version) + 1

        db.add(MachineAnomalyBaseline(**bl_dict))
        persisted += 1

    await db.flush()
    return persisted


async def persist_anomaly_event(
    db: AsyncSession,
    event_dict: dict,
) -> bool:
    from app.models.device import MachineAnomalyEvent

    tenant_id = event_dict["tenant_id"]
    device_id = event_dict["device_id"]
    signal_field = event_dict["signal_field"]
    occurred_at = event_dict.get("occurred_at")

    if occurred_at is not None:
        dup = await db.execute(
            select(MachineAnomalyEvent.id).where(
                and_(
                    MachineAnomalyEvent.tenant_id == tenant_id,
                    MachineAnomalyEvent.device_id == device_id,
                    MachineAnomalyEvent.signal_field == signal_field,
                    MachineAnomalyEvent.occurred_at == occurred_at,
                )
            )
        )
        if dup.scalar_one_or_none() is not None:
            return False

    db.add(MachineAnomalyEvent(**event_dict))
    await db.flush()
    return True


async def update_anomaly_event(
    db: AsyncSession,
    event_id: int,
    event_dict: dict,
) -> None:
    from app.models.device import MachineAnomalyEvent

    result = await db.execute(
        select(MachineAnomalyEvent).where(MachineAnomalyEvent.id == event_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        return

    for key in ("ended_at", "duration_seconds", "z_score", "signal_value",
                "anomaly_type", "merged_window_count", "confidence",
                "supply_related", "startup_adjacent", "mode_change",
                "recurring", "correlated_signals_json"):
        if key in event_dict and event_dict[key] is not None:
            setattr(row, key, event_dict[key])

    await db.flush()


async def persist_daily_count(
    db: AsyncSession,
    count_dict: dict,
) -> None:
    from app.models.device import MachineAnomalyDailyCount

    tenant_id = count_dict["tenant_id"]
    device_id = count_dict["device_id"]
    target_date = count_dict["date"]

    result = await db.execute(
        select(MachineAnomalyDailyCount).where(
            and_(
                MachineAnomalyDailyCount.tenant_id == tenant_id,
                MachineAnomalyDailyCount.device_id == device_id,
                MachineAnomalyDailyCount.date == target_date,
            )
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        for key, value in count_dict.items():
            if key not in ("tenant_id", "device_id", "date"):
                setattr(existing, key, value)
        existing.updated_at = datetime.now(timezone.utc)
    else:
        db.add(MachineAnomalyDailyCount(**count_dict))
    await db.flush()


async def delete_daily_count(
    db: AsyncSession,
    tenant_id: str,
    device_id: str,
    target_date: date,
) -> None:
    from app.models.device import MachineAnomalyDailyCount

    await db.execute(
        delete(MachineAnomalyDailyCount).where(
            and_(
                MachineAnomalyDailyCount.tenant_id == tenant_id,
                MachineAnomalyDailyCount.device_id == device_id,
                MachineAnomalyDailyCount.date == target_date,
            )
        )
    )
    await db.flush()


async def persist_weekly_count(
    db: AsyncSession,
    count_dict: dict,
) -> None:
    from app.models.device import MachineAnomalyWeeklyCount

    tenant_id = count_dict["tenant_id"]
    device_id = count_dict["device_id"]
    week_start = count_dict["week_start_date"]

    result = await db.execute(
        select(MachineAnomalyWeeklyCount).where(
            and_(
                MachineAnomalyWeeklyCount.tenant_id == tenant_id,
                MachineAnomalyWeeklyCount.device_id == device_id,
                MachineAnomalyWeeklyCount.week_start_date == week_start,
            )
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        for key, value in count_dict.items():
            if key not in ("tenant_id", "device_id", "week_start_date"):
                setattr(existing, key, value)
        existing.updated_at = datetime.now(timezone.utc)
    else:
        db.add(MachineAnomalyWeeklyCount(**count_dict))
    await db.flush()


async def refresh_anomaly_baselines_for_device(
    db: AsyncSession,
    tenant_id: str,
    device_id: str,
    minimum_days: int = 7,
    interval_seconds: int = 300,
) -> int:
    windows = await load_feature_windows_for_anomaly(
        db,
        tenant_id,
        device_id,
        minimum_days=minimum_days,
        interval_seconds=interval_seconds,
    )
    if not windows:
        return 0

    new_baselines: list[AnomalyFieldBaseline] = []
    for field_name in SUPPORTED_FIELDS:
        bl = learn_anomaly_baseline(windows, field_name, minimum_days=minimum_days)
        new_baselines.append(bl)

    return await persist_anomaly_baselines(db, tenant_id, device_id, new_baselines)


async def detect_device_anomalies(
    db: AsyncSession,
    tenant_id: str,
    device_id: str,
    max_open_event_age_hours: int = 24,
) -> dict:
    baselines = await load_active_anomaly_baselines_for_device(db, tenant_id, device_id)
    if not baselines:
        return {"new_events": 0, "extended_events": 0, "closed_events": 0}

    windows = await load_feature_windows_for_anomaly(db, tenant_id, device_id, limit=3)
    if not windows:
        return {"new_events": 0, "extended_events": 0, "closed_events": 0}

    latest = windows[-1]
    prior_events_raw = await load_recent_anomaly_events_for_device(db, tenant_id, device_id)

    open_events = [e for e in prior_events_raw if e.ended_at is None]

    candidates = detect_anomalies(
        baselines,
        latest.window,
        running_state=latest.running_state,
        prior_events=open_events if open_events else None,
    )

    current_window_start = latest.window_start
    current_window_end = latest.window_end

    extended, new = merge_events(
        candidates,
        open_events,
        current_window_start=current_window_start,
        current_window_end=current_window_end,
    )

    extended_count = 0
    from app.models.device import MachineAnomalyEvent
    for ev in extended:
        match_row = await db.execute(
            select(MachineAnomalyEvent).where(
                and_(
                    MachineAnomalyEvent.tenant_id == tenant_id,
                    MachineAnomalyEvent.device_id == device_id,
                    MachineAnomalyEvent.signal_field == ev.signal_field,
                    MachineAnomalyEvent.severity == ev.severity,
                    MachineAnomalyEvent.ended_at == None,
                )
            ).limit(1)
        )
        row = match_row.scalar_one_or_none()
        if row is not None:
            ev_dict = build_anomaly_event_dict(ev, tenant_id, device_id)
            await update_anomaly_event(db, row.id, ev_dict)
            extended_count += 1

    new_count = 0
    for ev in new:
        ev_dict = build_anomaly_event_dict(ev, tenant_id, device_id)
        inserted = await persist_anomaly_event(db, ev_dict)
        if inserted:
            new_count += 1

    closed_count = 0
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_open_event_age_hours)
    from app.models.device import MachineAnomalyEvent as _MAE
    stale_result = await db.execute(
        select(_MAE).where(
            and_(
                _MAE.tenant_id == tenant_id,
                _MAE.device_id == device_id,
                _MAE.ended_at == None,
                _MAE.occurred_at < cutoff,
            )
        )
    )
    stale_rows = stale_result.scalars().all()
    for row in stale_rows:
        row.ended_at = cutoff
        if row.occurred_at is not None:
            row.duration_seconds = int((cutoff - row.occurred_at).total_seconds())
        closed_count += 1
    if stale_rows:
        await db.flush()

    return {
        "new_events": new_count,
        "extended_events": extended_count,
        "closed_events": closed_count,
    }


async def aggregate_daily_counts_for_device(
    db: AsyncSession,
    tenant_id: str,
    device_id: str,
    target_date: date,
) -> Optional[DailyCountResult]:
    from app.models.device import MachineAnomalyEvent, MachineAnomalyDailyCount

    platform_tz = get_platform_tz()
    local_midnight = datetime(target_date.year, target_date.month, target_date.day, tzinfo=platform_tz)
    day_start = local_midnight.astimezone(timezone.utc)
    day_end = (local_midnight + timedelta(days=1)).astimezone(timezone.utc)

    result = await db.execute(
        select(MachineAnomalyEvent).where(
            and_(
                MachineAnomalyEvent.tenant_id == tenant_id,
                MachineAnomalyEvent.device_id == device_id,
                MachineAnomalyEvent.occurred_at >= day_start,
                MachineAnomalyEvent.occurred_at < day_end,
            )
        )
    )
    day_event_rows = result.scalars().all()

    day_events = []
    for r in day_event_rows:
        correlated: tuple[str, ...] = ()
        if r.correlated_signals_json:
            try:
                parsed = json.loads(r.correlated_signals_json)
                correlated = tuple(parsed)
            except (json.JSONDecodeError, TypeError):
                pass
        day_events.append(AnomalyCandidate(
            signal_field=r.signal_field,
            severity=r.severity,
            confidence=r.confidence or 0.0,
            supply_related=r.supply_related,
            startup_adjacent=r.startup_adjacent,
            mode_change=r.mode_change,
            recurring=r.recurring,
            correlated_signals=correlated,
        ))

    daily_result = aggregate_daily_counts(day_events, target_date)

    if daily_result is None:
        existing = await db.execute(
            select(MachineAnomalyDailyCount).where(
                and_(
                    MachineAnomalyDailyCount.tenant_id == tenant_id,
                    MachineAnomalyDailyCount.device_id == device_id,
                    MachineAnomalyDailyCount.date == target_date,
                )
            )
        )
        stale = existing.scalar_one_or_none()
        if stale is not None:
            await delete_daily_count(db, tenant_id, device_id, target_date)
        return None

    count_dict = build_daily_count_dict(daily_result, tenant_id, device_id)
    await persist_daily_count(db, count_dict)
    return daily_result


async def aggregate_weekly_counts_for_device(
    db: AsyncSession,
    tenant_id: str,
    device_id: str,
    week_start_date: date,
) -> Optional[dict]:
    from app.models.device import MachineAnomalyDailyCount

    week_end = week_start_date + timedelta(days=6)
    result = await db.execute(
        select(MachineAnomalyDailyCount).where(
            and_(
                MachineAnomalyDailyCount.tenant_id == tenant_id,
                MachineAnomalyDailyCount.device_id == device_id,
                MachineAnomalyDailyCount.date >= week_start_date,
                MachineAnomalyDailyCount.date <= week_end,
            )
        )
    )
    daily_rows = result.scalars().all()

    if not daily_rows:
        return None

    from .types import DailyCountResult as DCR
    dailies = [
        DCR(
            date=r.date,
            total_count=r.total_count,
            mild_count=r.mild_count,
            strong_count=r.strong_count,
            severe_count=r.severe_count,
            supply_related_count=r.supply_related_count,
            top_signal=r.top_signal,
            avg_confidence=r.avg_confidence,
        )
        for r in daily_rows
    ]

    prior_week_start = week_start_date - timedelta(days=7)
    prior_total = await load_prior_week_total_for_device(
        db, tenant_id, device_id, prior_week_start,
    )

    weekly_result = aggregate_weekly_counts(dailies, week_start_date, prior_total)
    if weekly_result is None:
        return None

    count_dict = build_weekly_count_dict(weekly_result, tenant_id, device_id)
    await persist_weekly_count(db, count_dict)
    return count_dict


async def cleanup_old_anomaly_rows(
    db: AsyncSession,
    retention_days: int,
) -> dict[str, int]:
    from app.models.device import (
        MachineAnomalyEvent,
        MachineAnomalyDailyCount,
        MachineAnomalyWeeklyCount,
        MachineAnomalyBaseline,
    )

    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    total_deleted = 0

    result = await db.execute(
        delete(MachineAnomalyEvent).where(MachineAnomalyEvent.created_at < cutoff)
    )
    total_deleted += result.rowcount

    daily_cutoff = datetime.now(timezone.utc) - timedelta(days=_DAILY_COUNT_RETENTION_DAYS)
    result = await db.execute(
        delete(MachineAnomalyDailyCount).where(
            MachineAnomalyDailyCount.created_at < daily_cutoff
        )
    )
    total_deleted += result.rowcount

    weekly_cutoff = datetime.now(timezone.utc) - timedelta(days=_WEEKLY_COUNT_RETENTION_DAYS)
    result = await db.execute(
        delete(MachineAnomalyWeeklyCount).where(
            MachineAnomalyWeeklyCount.created_at < weekly_cutoff
        )
    )
    total_deleted += result.rowcount

    retired_cutoff = datetime.now(timezone.utc) - timedelta(days=_RETIRED_BASELINE_RETENTION_DAYS)
    result = await db.execute(
        delete(MachineAnomalyBaseline).where(
            and_(
                MachineAnomalyBaseline.status == "retired",
                MachineAnomalyBaseline.created_at < retired_cutoff,
            )
        )
    )
    total_deleted += result.rowcount

    insuff_cutoff = datetime.now(timezone.utc) - timedelta(days=_INSUFFICIENT_CANDIDATE_RETENTION_DAYS)
    result = await db.execute(
        delete(MachineAnomalyBaseline).where(
            and_(
                MachineAnomalyBaseline.status == "candidate",
                MachineAnomalyBaseline.quality_score < _INSUFFICIENT_QUALITY_THRESHOLD,
                MachineAnomalyBaseline.created_at < insuff_cutoff,
            )
        )
    )
    total_deleted += result.rowcount

    await db.flush()
    return {"deleted": total_deleted}
