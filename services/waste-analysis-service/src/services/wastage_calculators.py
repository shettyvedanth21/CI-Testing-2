from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional
from src.services.telemetry_normalizer import (
    extract_current,
    extract_power_kw,
    extract_voltage,
    safe_dt,
)
from services.shared.telemetry_normalization import normalize_telemetry_sample

MAX_GAP_MINUTES = 15
DEFAULT_PF = 0.85


@dataclass
class WastageResult:
    kwh: Optional[float] = None
    cost: Optional[float] = None
    duration_sec: Optional[int] = None
    skipped_reason: Optional[str] = None
    warnings: list[str] = field(default_factory=list)
    pf_estimated: bool = False
    config_source: Optional[str] = None
    config_used: Optional[dict[str, Any]] = None


def detect_state(current: Optional[float], voltage: Optional[float], threshold: Optional[float]) -> str:
    if current is None or voltage is None:
        return "unknown"
    if current <= 0 and voltage > 0:
        return "unloaded"
    if threshold is None:
        return "unknown" if current > 0 and voltage > 0 else "unknown"
    if 0 < current < threshold and voltage > 0:
        return "idle"
    if current >= threshold and voltage > 0:
        return "running"
    return "unknown"


def _get_power_kw(row: dict[str, Any]) -> Optional[float]:
    normalized = normalize_telemetry_sample(row, row)
    return (normalized.business_power_w / 1000.0) if normalized.business_power_w > 0 else None


def _get_current(row: dict[str, Any]) -> Optional[float]:
    return normalize_telemetry_sample(row, row).current_a


def _get_voltage(row: dict[str, Any]) -> Optional[float]:
    return normalize_telemetry_sample(row, row).voltage_v


def _compute_intervals_sec(rows: list[dict[str, Any]]) -> list[float]:
    n = len(rows)
    if n == 0:
        return []
    if n == 1:
        return [60.0]

    out = [60.0]
    max_gap = MAX_GAP_MINUTES * 60.0
    for i in range(1, n):
        cur = safe_dt(rows[i].get("timestamp"))
        prev = safe_dt(rows[i - 1].get("timestamp"))
        if not isinstance(cur, datetime) or not isinstance(prev, datetime):
            out.append(0.0)
            continue
        delta = (cur - prev).total_seconds()
        if delta <= 0 or delta > max_gap:
            out.append(0.0)
        else:
            out.append(delta)
    return out


def _inside_shift(ts: datetime, shift_start: str, shift_end: str) -> bool:
    try:
        sh, sm = [int(v) for v in shift_start.split(":")[:2]]
        eh, em = [int(v) for v in shift_end.split(":")[:2]]
    except Exception:
        return False

    t_m = ts.hour * 60 + ts.minute
    s_m = sh * 60 + sm
    e_m = eh * 60 + em

    if e_m <= s_m:
        return t_m >= s_m or t_m <= e_m
    return s_m <= t_m <= e_m


def _inside_any_shift(ts: datetime, shifts: list[dict[str, Any]]) -> bool:
    if not shifts:
        return False
    dow = ts.weekday()
    for s in shifts:
        day = s.get("day_of_week")
        if day is not None and day != dow:
            continue
        ss = s.get("shift_start")
        se = s.get("shift_end")
        if not ss or not se:
            continue
        if _inside_shift(ts, str(ss), str(se)):
            return True
    return False


def _is_in_window(ts: datetime, start_str: str, end_str: str) -> bool:
    return _inside_shift(ts, start_str, end_str)


def _shifts_overlap_window(shifts: list[dict[str, Any]], start_str: str, end_str: str, weekend: bool) -> bool:
    if not shifts:
        return False

    def _day_matches(day_of_week: Any) -> bool:
        if day_of_week is None:
            return True
        try:
            day = int(day_of_week)
        except Exception:
            return False
        return (day >= 5) if weekend else (day < 5)

    # Check overlap by sampling each hour marker across 24h for matching day buckets.
    for s in shifts:
        if not _day_matches(s.get("day_of_week")):
            continue
        ss = s.get("shift_start")
        se = s.get("shift_end")
        if not ss or not se:
            continue
        for hour in range(24):
            probe = datetime(2026, 1, 1, hour, 0)
            if _is_in_window(probe, start_str, end_str) and _inside_shift(probe, str(ss), str(se)):
                return True
    return False


def calculate_offhours(
    rows: list[dict[str, Any]],
    shift_start: Optional[str],
    shift_end: Optional[str],
    idle_threshold: Optional[float],
    tariff_rate: Optional[float],
    shifts: Optional[list[dict[str, Any]]] = None,
) -> WastageResult:
    shifts = shifts or []
    if not shifts and (not shift_start or not shift_end):
        return WastageResult(skipped_reason="Shift not configured for this device")
    if not rows:
        return WastageResult(skipped_reason="No telemetry data")

    intervals_sec = _compute_intervals_sec(rows)
    total_kwh = 0.0
    total_sec = 0
    warnings: list[str] = []
    pf_estimated = False

    for row, interval_sec in zip(rows, intervals_sec):
        if interval_sec <= 0:
            continue
        ts = row.get("timestamp")
        if not isinstance(ts, datetime):
            continue

        inside_shift = _inside_any_shift(ts, shifts) if shifts else _inside_shift(ts, str(shift_start), str(shift_end))
        if inside_shift:
            continue

        power_kw = _get_power_kw(row)
        current = _get_current(row)
        voltage = _get_voltage(row)

        threshold = float(idle_threshold) if idle_threshold is not None else 0.0
        is_running = (power_kw is not None and power_kw > 0) or (current is not None and current > 0)
        if not is_running:
            continue

        total_sec += int(interval_sec)

        if power_kw is not None and power_kw > 0:
            total_kwh += power_kw * (interval_sec / 3600.0)
        elif current is not None and current > 0 and voltage is not None:
            total_kwh += ((current * voltage * DEFAULT_PF) / 1000.0) * (interval_sec / 3600.0)
            pf_estimated = True
        else:
            warnings.append("Running detected outside shift but energy unavailable for some intervals")

    warnings = sorted(set(warnings))
    if total_sec == 0:
        return WastageResult(kwh=0.0, cost=0.0 if tariff_rate is not None else None, duration_sec=0, warnings=["No off-hours consumption detected"])

    if total_kwh <= 0:
        return WastageResult(kwh=None, cost=None, duration_sec=total_sec, pf_estimated=pf_estimated, warnings=warnings)

    return WastageResult(
        kwh=round(total_kwh, 4),
        cost=round(total_kwh * float(tariff_rate), 4) if tariff_rate is not None else None,
        duration_sec=total_sec,
        pf_estimated=pf_estimated,
        warnings=warnings,
    )


def calculate_overconsumption(
    rows: list[dict[str, Any]],
    overconsumption_threshold: Optional[float],
    tariff_rate: Optional[float],
) -> WastageResult:
    if overconsumption_threshold is None:
        return WastageResult(skipped_reason="Current threshold not configured for this device")
    if not rows:
        return WastageResult(skipped_reason="No telemetry data")

    intervals_sec = _compute_intervals_sec(rows)
    total_kwh = 0.0
    total_sec = 0
    warnings: list[str] = []
    pf_estimated = False

    thr = float(overconsumption_threshold)
    for row, interval_sec in zip(rows, intervals_sec):
        if interval_sec <= 0:
            continue

        current = _get_current(row)
        if current is None or current <= thr:
            continue

        excess_current = current - thr
        total_sec += int(interval_sec)

        power_kw = _get_power_kw(row)
        voltage = _get_voltage(row)

        if voltage is not None and voltage > 0:
            excess_power_kw = (excess_current * voltage * DEFAULT_PF) / 1000.0
            pf_estimated = True
        elif power_kw is not None and power_kw > 0:
            ratio = excess_current / max(current, 1e-9)
            excess_power_kw = power_kw * ratio
        else:
            warnings.append("Current exceeded threshold but voltage/power unavailable for some intervals")
            continue

        total_kwh += excess_power_kw * (interval_sec / 3600.0)

    warnings = sorted(set(warnings))
    if total_sec == 0:
        return WastageResult(kwh=0.0, cost=0.0 if tariff_rate is not None else None, duration_sec=0, warnings=["No overconsumption detected in this period"])

    if total_kwh <= 0:
        return WastageResult(kwh=None, cost=None, duration_sec=total_sec, pf_estimated=pf_estimated, warnings=warnings)

    return WastageResult(
        kwh=round(total_kwh, 4),
        cost=round(total_kwh * float(tariff_rate), 4) if tariff_rate is not None else None,
        duration_sec=total_sec,
        pf_estimated=pf_estimated,
        warnings=warnings,
    )


def calculate_unoccupied(
    rows: list[dict[str, Any]],
    running_current_threshold: Optional[float],
    tariff_rate: Optional[float],
    shifts: Optional[list[dict[str, Any]]] = None,
    weekday_window: Optional[tuple[str, str]] = None,
    weekend_window: Optional[tuple[str, str]] = None,
    config_source: Optional[str] = None,
) -> WastageResult:
    if not rows:
        return WastageResult(skipped_reason="No telemetry data")
    if not weekday_window or not weekend_window:
        return WastageResult(
            skipped_reason="Unoccupied window not configured",
            config_source=config_source,
        )

    shifts = shifts or []
    if _shifts_overlap_window(shifts, weekday_window[0], weekday_window[1], weekend=False) or _shifts_overlap_window(
        shifts, weekend_window[0], weekend_window[1], weekend=True
    ):
        return WastageResult(
            skipped_reason="Device shift overlaps unoccupied window - not flagged",
            config_source=config_source,
            config_used={
                "weekday_start": weekday_window[0],
                "weekday_end": weekday_window[1],
                "weekend_start": weekend_window[0],
                "weekend_end": weekend_window[1],
            },
        )

    intervals_sec = _compute_intervals_sec(rows)
    total_kwh = 0.0
    total_sec = 0
    pf_estimated = False
    warnings: list[str] = []
    energy_known_any = False

    threshold = float(running_current_threshold) if running_current_threshold is not None else 0.0

    for row, interval_sec in zip(rows, intervals_sec):
        if interval_sec <= 0:
            continue

        ts = row.get("timestamp")
        if not isinstance(ts, datetime):
            continue
        weekend = ts.weekday() >= 5
        w = weekend_window if weekend else weekday_window
        if w is None or not _is_in_window(ts, w[0], w[1]):
            continue

        power_kw = _get_power_kw(row)
        current = _get_current(row)
        voltage = _get_voltage(row)

        is_running = (power_kw is not None and power_kw > 0) or (current is not None and current > threshold)
        if not is_running:
            continue

        total_sec += int(interval_sec)

        if power_kw is not None and power_kw > 0:
            total_kwh += power_kw * (interval_sec / 3600.0)
            energy_known_any = True
        elif current is not None and current > threshold and voltage is not None:
            total_kwh += ((current * voltage * DEFAULT_PF) / 1000.0) * (interval_sec / 3600.0)
            energy_known_any = True
            pf_estimated = True

    if total_sec == 0:
        return WastageResult(
            kwh=0.0,
            cost=0.0 if tariff_rate is not None else None,
            duration_sec=0,
            warnings=["No unoccupied running detected"],
            config_source=config_source,
            config_used={
                "weekday_start": weekday_window[0],
                "weekday_end": weekday_window[1],
                "weekend_start": weekend_window[0],
                "weekend_end": weekend_window[1],
            },
        )

    if not energy_known_any:
        warnings.append("Machine ran in unoccupied window but energy could not be calculated (missing power/voltage)")
        return WastageResult(
            kwh=None,
            cost=None,
            duration_sec=total_sec,
            warnings=warnings,
            pf_estimated=pf_estimated,
            config_source=config_source,
            config_used={
                "weekday_start": weekday_window[0],
                "weekday_end": weekday_window[1],
                "weekend_start": weekend_window[0],
                "weekend_end": weekend_window[1],
            },
        )

    return WastageResult(
        kwh=round(total_kwh, 4),
        cost=round(total_kwh * float(tariff_rate), 4) if tariff_rate is not None else None,
        duration_sec=total_sec,
        pf_estimated=pf_estimated,
        warnings=warnings,
        config_source=config_source,
        config_used={
            "weekday_start": weekday_window[0],
            "weekday_end": weekday_window[1],
            "weekend_start": weekend_window[0],
            "weekend_end": weekend_window[1],
        },
    )
