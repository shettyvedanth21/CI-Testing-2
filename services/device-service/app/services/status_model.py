"""Canonical operational status model for dashboard and fleet UX.

`runtime_status` remains the authoritative binary telemetry-freshness state.
`load_state` remains the richer live-projection load classification while the
device is running and may still expose internal states like `unloaded`.
`current_band` remains the additive electrical band derived from thresholds.

Dashboard and fleet UX should consume `operational_status`, which normalizes
those raw signals into the operator-facing set:
`unknown`, `stopped`, `idle`, `running`, `overconsumption`.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import and_, case

from app.models.device import RuntimeStatus

OPERATIONAL_STATUS_UNKNOWN = "unknown"
OPERATIONAL_STATUS_STOPPED = "stopped"
OPERATIONAL_STATUS_IDLE = "idle"
OPERATIONAL_STATUS_RUNNING = "running"
OPERATIONAL_STATUS_OVERCONSUMPTION = "overconsumption"

OPERATIONAL_STATUS_VALUES = (
    OPERATIONAL_STATUS_UNKNOWN,
    OPERATIONAL_STATUS_STOPPED,
    OPERATIONAL_STATUS_IDLE,
    OPERATIONAL_STATUS_RUNNING,
    OPERATIONAL_STATUS_OVERCONSUMPTION,
)


def resolve_operational_status(
    *,
    runtime_status: Optional[str],
    load_state: Optional[str],
    current_band: Optional[str] = None,
    has_telemetry: bool = False,
) -> str:
    normalized_runtime = (runtime_status or "").strip().lower()
    normalized_load = (load_state or "").strip().lower()
    normalized_band = (current_band or "").strip().lower()

    if normalized_runtime != RuntimeStatus.RUNNING.value:
        return OPERATIONAL_STATUS_STOPPED if has_telemetry else OPERATIONAL_STATUS_UNKNOWN

    if normalized_load == OPERATIONAL_STATUS_OVERCONSUMPTION or normalized_band == OPERATIONAL_STATUS_OVERCONSUMPTION:
        return OPERATIONAL_STATUS_OVERCONSUMPTION
    if normalized_load == OPERATIONAL_STATUS_IDLE or normalized_band == OPERATIONAL_STATUS_IDLE:
        return OPERATIONAL_STATUS_IDLE
    if normalized_load == OPERATIONAL_STATUS_RUNNING or normalized_band == "in_load":
        return OPERATIONAL_STATUS_RUNNING

    # `unloaded` and any unresolved live state remain visible in detail views
    # through `load_state`/`current_band`, but fleet/home UX treats them as
    # unresolved operator-facing status instead of flattening them into running.
    return OPERATIONAL_STATUS_UNKNOWN


def operational_status_sql(runtime_status_expr, load_state_expr, authoritative_ts_expr):
    return case(
        (
            runtime_status_expr != RuntimeStatus.RUNNING.value,
            case(
                (authoritative_ts_expr.is_not(None), OPERATIONAL_STATUS_STOPPED),
                else_=OPERATIONAL_STATUS_UNKNOWN,
            ),
        ),
        (
            and_(
                runtime_status_expr == RuntimeStatus.RUNNING.value,
                load_state_expr == OPERATIONAL_STATUS_OVERCONSUMPTION,
            ),
            OPERATIONAL_STATUS_OVERCONSUMPTION,
        ),
        (
            and_(
                runtime_status_expr == RuntimeStatus.RUNNING.value,
                load_state_expr == OPERATIONAL_STATUS_IDLE,
            ),
            OPERATIONAL_STATUS_IDLE,
        ),
        (
            and_(
                runtime_status_expr == RuntimeStatus.RUNNING.value,
                load_state_expr == OPERATIONAL_STATUS_RUNNING,
            ),
            OPERATIONAL_STATUS_RUNNING,
        ),
        else_=OPERATIONAL_STATUS_UNKNOWN,
    )
