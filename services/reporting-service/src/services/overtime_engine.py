from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Optional

from services.shared.energy_accounting import build_samples

from src.utils.localization import format_platform_timestamp, get_platform_tz


@dataclass
class OvertimeDailyBreakdown:
    date: str
    overtime_minutes: float
    overtime_hours: float
    overtime_kwh: float
    overtime_cost: float | None


@dataclass
class OvertimeWindowBreakdown:
    date: str
    window_start: str
    window_end: str
    window_start_iso: str
    window_end_iso: str
    overtime_minutes: float
    overtime_hours: float
    overtime_kwh: float
    overtime_cost: float | None
    shift_status: str


@dataclass
class OvertimeComputationResult:
    configured: bool
    shift_count: int
    total_overtime_minutes: float
    total_overtime_hours: float
    total_overtime_kwh: float
    total_overtime_cost: float | None
    currency: str
    tariff_rate_used: float | None
    daily_breakdown: list[dict[str, Any]]
    window_breakdown: list[dict[str, Any]]
    warnings: list[str]


def _parse_time(value: Any) -> Optional[time]:
    if value is None:
        return None
    if isinstance(value, time):
        return value
    if isinstance(value, str):
        try:
            parts = value.strip().split(":")
            if len(parts) >= 2:
                return time(int(parts[0]), int(parts[1]))
        except Exception:
            return None
    return None


def _normalize_shift(shift: Any) -> dict[str, Any]:
    if isinstance(shift, dict):
        return shift
    return {
        "shift_start": getattr(shift, "shift_start", None),
        "shift_end": getattr(shift, "shift_end", None),
        "day_of_week": getattr(shift, "day_of_week", None),
        "is_active": getattr(shift, "is_active", True),
    }


def _active_shifts(shifts: list[Any]) -> tuple[list[dict[str, Any]], list[str]]:
    active: list[dict[str, Any]] = []
    warnings: list[str] = []

    for shift in shifts or []:
        normalized = _normalize_shift(shift)
        if not normalized.get("is_active", True):
            continue

        start = _parse_time(normalized.get("shift_start"))
        end = _parse_time(normalized.get("shift_end"))
        if start is None or end is None:
            warnings.append("SHIFT_CONFIGURATION_INVALID: a shift with missing start/end time was skipped")
            continue
        if start == end:
            warnings.append("SHIFT_CONFIGURATION_INVALID: a shift with identical start/end time was skipped")
            continue

        normalized["shift_start"] = start
        normalized["shift_end"] = end
        normalized["day_of_week"] = normalized.get("day_of_week")
        active.append(normalized)

    return active, warnings


def _day_windows(local_day: date, shifts: list[dict[str, Any]]) -> list[tuple[datetime, datetime]]:
    tz = get_platform_tz()
    weekday = local_day.weekday()
    windows: list[tuple[datetime, datetime]] = []

    for shift in shifts:
        shift_day = shift.get("day_of_week")
        start = shift.get("shift_start")
        end = shift.get("shift_end")
        if start is None or end is None:
            continue

        def _add_window(base_day: date, start_time: time, end_time: time) -> None:
            start_dt = datetime.combine(base_day, start_time, tzinfo=tz)
            end_dt = datetime.combine(base_day, end_time, tzinfo=tz)
            if end_dt <= start_dt:
                end_dt += timedelta(days=1)
            windows.append((start_dt, end_dt))

        if shift_day is None or shift_day == weekday:
            _add_window(local_day, start, end)

        if (shift_day is None or shift_day == (weekday - 1) % 7) and end <= start:
            start_dt = datetime.combine(local_day, time.min, tzinfo=tz)
            end_dt = datetime.combine(local_day, end, tzinfo=tz)
            windows.append((start_dt, end_dt))

    windows.sort(key=lambda item: item[0])
    merged: list[tuple[datetime, datetime]] = []
    for start_dt, end_dt in windows:
        if not merged:
            merged.append((start_dt, end_dt))
            continue
        prev_start, prev_end = merged[-1]
        if start_dt <= prev_end:
            merged[-1] = (prev_start, max(prev_end, end_dt))
        else:
            merged.append((start_dt, end_dt))
    return merged


def _overlap_seconds(start: datetime, end: datetime, windows: list[tuple[datetime, datetime]]) -> float:
    overlap = 0.0
    for w_start, w_end in windows:
        seg_start = max(start, w_start)
        seg_end = min(end, w_end)
        if seg_end > seg_start:
            overlap += (seg_end - seg_start).total_seconds()
    return max(0.0, overlap)


def _segment_by_day(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    segments: list[tuple[datetime, datetime]] = []
    cursor = start
    while cursor < end:
        next_midnight = datetime.combine((cursor.date() + timedelta(days=1)), time.min, tzinfo=cursor.tzinfo)
        segment_end = min(end, next_midnight)
        if segment_end > cursor:
            segments.append((cursor, segment_end))
        cursor = segment_end
    return segments


def _outside_shift_segments(
    start: datetime,
    end: datetime,
    windows: list[tuple[datetime, datetime]],
) -> list[tuple[datetime, datetime]]:
    if end <= start:
        return []

    cursor = start
    outside: list[tuple[datetime, datetime]] = []

    for w_start, w_end in windows:
        if w_end <= cursor:
            continue
        if w_start >= end:
            break
        if w_start > cursor:
            outside_end = min(w_start, end)
            if outside_end > cursor:
                outside.append((cursor, outside_end))
        cursor = max(cursor, min(w_end, end))
        if cursor >= end:
            break

    if cursor < end:
        outside.append((cursor, end))

    return outside


def _merge_window_breakdown(rows: list[dict[str, Any]], tariff_rate: float | None) -> list[dict[str, Any]]:
    if not rows:
        return []

    def _sort_key(row: dict[str, Any]) -> tuple[str, str]:
        return str(row.get("date") or ""), str(row.get("window_start_iso") or "")

    merged: list[dict[str, Any]] = []
    for row in sorted(rows, key=_sort_key):
        if not merged:
            merged.append(dict(row))
            continue

        current = merged[-1]
        same_day = current.get("date") == row.get("date")
        same_status = current.get("shift_status") == row.get("shift_status")
        contiguous = current.get("window_end_iso") == row.get("window_start_iso")

        if same_day and same_status and contiguous:
            current["window_end_iso"] = row.get("window_end_iso")
            current["window_end"] = row.get("window_end")
            current["overtime_minutes"] = round(
                float(current.get("overtime_minutes") or 0.0) + float(row.get("overtime_minutes") or 0.0),
                2,
            )
            current["overtime_hours"] = round(
                float(current.get("overtime_hours") or 0.0) + float(row.get("overtime_hours") or 0.0),
                4,
            )
            current["overtime_kwh"] = round(
                float(current.get("overtime_kwh") or 0.0) + float(row.get("overtime_kwh") or 0.0),
                4,
            )
            current["overtime_cost"] = (
                round(float(current.get("overtime_kwh") or 0.0) * tariff_rate, 2) if tariff_rate is not None else None
            )
            continue

        merged.append(dict(row))

    for row in merged:
        row["overtime_minutes"] = round(float(row.get("overtime_minutes") or 0.0), 2)
        row["overtime_hours"] = round(float(row.get("overtime_hours") or 0.0), 4)
        row["overtime_kwh"] = round(float(row.get("overtime_kwh") or 0.0), 4)
        row["overtime_cost"] = (
            round(float(row.get("overtime_kwh") or 0.0) * tariff_rate, 2) if tariff_rate is not None else None
        )

    return merged


def compute_overtime_breakdown(
    rows: list[dict[str, Any]],
    shifts: list[Any],
    tariff_rate: float | None,
    currency: str = "INR",
) -> OvertimeComputationResult:
    active_shifts, shift_warnings = _active_shifts(shifts)
    configured = bool(active_shifts)
    warnings = list(shift_warnings)

    if not configured:
        warnings.append("SHIFT_NOT_CONFIGURED: overtime charging skipped because no active shift schedule was found")
        return OvertimeComputationResult(
            configured=False,
            shift_count=0,
            total_overtime_minutes=0.0,
            total_overtime_hours=0.0,
            total_overtime_kwh=0.0,
            total_overtime_cost=0.0 if tariff_rate is not None else None,
            currency=(currency or "INR").upper(),
            tariff_rate_used=tariff_rate,
            daily_breakdown=[],
            window_breakdown=[],
            warnings=warnings,
        )

    intervals = build_samples(rows, get_platform_tz(), max_gap_sec=900.0)

    daily: dict[str, dict[str, float | str | None]] = {}
    windows: list[dict[str, Any]] = []
    total_overtime_sec = 0.0
    total_overtime_kwh = 0.0
    saw_pf_estimated = False

    for interval in intervals:
        if interval.duration_sec <= 0 or interval.power_kw is None:
            continue

        interval_end = interval.ts_local + timedelta(seconds=interval.duration_sec)
        total_energy_kwh = max(0.0, float(interval.power_kw) * (interval.duration_sec / 3600.0))
        if total_energy_kwh <= 0:
            continue
        if interval.power_estimated:
            saw_pf_estimated = True

        for segment_start, segment_end in _segment_by_day(interval.ts_local, interval_end):
            segment_duration_sec = (segment_end - segment_start).total_seconds()
            if segment_duration_sec <= 0:
                continue

            day_windows = _day_windows(segment_start.date(), active_shifts)
            inside_shift_sec = _overlap_seconds(segment_start, segment_end, day_windows)
            overtime_sec = max(0.0, segment_duration_sec - inside_shift_sec)
            if overtime_sec <= 0:
                bucket = daily.setdefault(
                    segment_start.date().isoformat(),
                    {
                        "date": segment_start.date().isoformat(),
                        "overtime_minutes": 0.0,
                        "overtime_hours": 0.0,
                        "overtime_kwh": 0.0,
                        "overtime_cost": None,
                    },
                )
                continue

            energy_share_kwh = total_energy_kwh * (segment_duration_sec / interval.duration_sec)
            overtime_energy_kwh = energy_share_kwh * (overtime_sec / segment_duration_sec)
            outside_segments = _outside_shift_segments(segment_start, segment_end, day_windows)

            bucket = daily.setdefault(
                segment_start.date().isoformat(),
                {
                    "date": segment_start.date().isoformat(),
                    "overtime_minutes": 0.0,
                    "overtime_hours": 0.0,
                    "overtime_kwh": 0.0,
                    "overtime_cost": None,
                },
            )
            bucket["overtime_minutes"] = float(bucket["overtime_minutes"] or 0.0) + overtime_sec / 60.0
            bucket["overtime_hours"] = float(bucket["overtime_hours"] or 0.0) + overtime_sec / 3600.0
            bucket["overtime_kwh"] = float(bucket["overtime_kwh"] or 0.0) + overtime_energy_kwh

            total_overtime_sec += overtime_sec
            total_overtime_kwh += overtime_energy_kwh

            for outside_start, outside_end in outside_segments:
                outside_duration_sec = (outside_end - outside_start).total_seconds()
                if outside_duration_sec <= 0:
                    continue
                window_energy_kwh = energy_share_kwh * (outside_duration_sec / segment_duration_sec)
                windows.append(
                    {
                        "date": outside_start.date().isoformat(),
                        "window_start": format_platform_timestamp(outside_start, include_tz=False),
                        "window_end": format_platform_timestamp(outside_end, include_tz=False),
                        "window_start_iso": outside_start.isoformat(),
                        "window_end_iso": outside_end.isoformat(),
                        "overtime_minutes": outside_duration_sec / 60.0,
                        "overtime_hours": outside_duration_sec / 3600.0,
                        "overtime_kwh": window_energy_kwh,
                        "overtime_cost": (
                            round(window_energy_kwh * tariff_rate, 2) if tariff_rate is not None else None
                        ),
                        "shift_status": "Overtime",
                    }
                )

    if saw_pf_estimated:
        warnings.append("POWER_FACTOR_ASSUMED_0.85: overtime energy was estimated for some intervals")

    sorted_days = []
    for key in sorted(daily.keys()):
        row = daily[key]
        if tariff_rate is not None:
            row["overtime_cost"] = round(float(row["overtime_kwh"] or 0.0) * tariff_rate, 2)
        else:
            row["overtime_cost"] = None
        row["overtime_minutes"] = round(float(row["overtime_minutes"] or 0.0), 2)
        row["overtime_hours"] = round(float(row["overtime_hours"] or 0.0), 4)
        row["overtime_kwh"] = round(float(row["overtime_kwh"] or 0.0), 4)
        sorted_days.append(row)

    total_overtime_hours = total_overtime_sec / 3600.0
    total_overtime_minutes = total_overtime_sec / 60.0
    total_overtime_cost = (
        round(
            sum(float(row.get("overtime_cost") or 0.0) for row in sorted_days),
            2,
        )
        if tariff_rate is not None
        else None
    )

    merged_windows = _merge_window_breakdown(windows, tariff_rate)

    return OvertimeComputationResult(
        configured=True,
        shift_count=len(active_shifts),
        total_overtime_minutes=round(total_overtime_minutes, 2),
        total_overtime_hours=round(total_overtime_hours, 4),
        total_overtime_kwh=round(total_overtime_kwh, 4),
        total_overtime_cost=total_overtime_cost,
        currency=(currency or "INR").upper(),
        tariff_rate_used=tariff_rate,
        daily_breakdown=sorted_days,
        window_breakdown=merged_windows,
        warnings=warnings,
    )
