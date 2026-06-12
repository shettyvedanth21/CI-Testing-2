from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import case

from app.models.device import RuntimeStatus, TELEMETRY_TIMEOUT_SECONDS


def normalize_utc_timestamp(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def resolve_runtime_status(
    observed_at: Optional[datetime],
    *,
    now_utc: Optional[datetime] = None,
) -> str:
    normalized = normalize_utc_timestamp(observed_at)
    if normalized is None:
        return RuntimeStatus.STOPPED.value
    reference = now_utc or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    else:
        reference = reference.astimezone(timezone.utc)
    age_seconds = (reference - normalized).total_seconds()
    return RuntimeStatus.RUNNING.value if age_seconds <= TELEMETRY_TIMEOUT_SECONDS else RuntimeStatus.STOPPED.value


def resolve_runtime_timeout_ended_at(
    observed_at: Optional[datetime],
    *,
    timeout_seconds: int = TELEMETRY_TIMEOUT_SECONDS,
) -> Optional[datetime]:
    normalized = normalize_utc_timestamp(observed_at)
    if normalized is None:
        return None
    return normalized + timedelta(seconds=max(int(timeout_seconds), 0))


def resolve_load_state(
    load_state: Optional[str],
    observed_at: Optional[datetime],
    *,
    now_utc: Optional[datetime] = None,
) -> str:
    return load_state or "unknown" if resolve_runtime_status(observed_at, now_utc=now_utc) == RuntimeStatus.RUNNING.value else "unknown"


def runtime_status_sql(authoritative_ts_expr, *, now_utc: Optional[datetime] = None):
    reference = now_utc or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    else:
        reference = reference.astimezone(timezone.utc)
    cutoff = reference - timedelta(seconds=TELEMETRY_TIMEOUT_SECONDS)
    return case(
        (
            authoritative_ts_expr.is_not(None) & (authoritative_ts_expr >= cutoff),
            RuntimeStatus.RUNNING.value,
        ),
        else_=RuntimeStatus.STOPPED.value,
    )


def load_state_sql(load_state_expr, authoritative_ts_expr, *, now_utc: Optional[datetime] = None):
    runtime_expr = runtime_status_sql(authoritative_ts_expr, now_utc=now_utc)
    return case(
        (runtime_expr == RuntimeStatus.RUNNING.value, load_state_expr),
        else_="unknown",
    )
