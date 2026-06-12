from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any, Optional
from zoneinfo import ZoneInfo

from services.shared.telemetry_normalization import (
    DEFAULT_FALLBACK_PF,
    NormalizedTelemetrySample,
    build_device_power_config,
    compute_interval_energy_delta,
    normalize_telemetry_sample,
)

MAX_GAP_SEC = 900.0


@dataclass(frozen=True)
class IntervalSample:
    ts_utc: datetime
    ts_local: datetime
    duration_sec: float
    power_kw: Optional[float]
    power_estimated: bool
    current_a: Optional[float]
    voltage_v: Optional[float]
    pf: Optional[float]


@dataclass
class WindowTotals:
    energy_kwh: float = 0.0
    idle_kwh: float = 0.0
    offhours_kwh: float = 0.0
    overconsumption_kwh: float = 0.0
    total_loss_kwh: float = 0.0
    idle_duration_sec: int = 0
    offhours_duration_sec: int = 0
    overconsumption_duration_sec: int = 0
    pf_estimated: bool = False


@dataclass
class WindowAccounting:
    total: WindowTotals = field(default_factory=WindowTotals)
    by_day: dict[date, WindowTotals] = field(default_factory=dict)
    samples: int = 0


def parse_ts(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def _to_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=ZoneInfo("UTC"))


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def extract_power_kw(payload: dict[str, Any]) -> Optional[float]:
    normalized = normalize_telemetry_sample(payload, build_device_power_config(payload))
    if normalized.business_power_w <= 0:
        return None
    return normalized.business_power_w / 1000.0


def extract_current_a(payload: dict[str, Any]) -> Optional[float]:
    return normalize_telemetry_sample(payload, build_device_power_config(payload)).current_a


def extract_voltage_v(payload: dict[str, Any]) -> Optional[float]:
    return normalize_telemetry_sample(payload, build_device_power_config(payload)).voltage_v


def extract_pf(payload: dict[str, Any]) -> Optional[float]:
    return normalize_telemetry_sample(payload, build_device_power_config(payload)).pf_business


def _power_kw_and_estimate(payload: dict[str, Any]) -> tuple[Optional[float], bool]:
    normalized = normalize_telemetry_sample(payload, build_device_power_config(payload))
    if normalized.business_power_w > 0:
        return normalized.business_power_w / 1000.0, False
    if normalized.current_a is None or normalized.voltage_v is None:
        return None, False
    pf = normalized.pf_business
    pf_estimated = pf is None
    pf_value = pf if pf is not None else DEFAULT_FALLBACK_PF
    return (max(0.0, normalized.current_a * normalized.voltage_v * pf_value / 1000.0), pf_estimated)


def _normalize_shift_attr(shift: Any, key: str, default: Any = None) -> Any:
    if isinstance(shift, dict):
        return shift.get(key, default)
    return getattr(shift, key, default)


def _parse_shift_time(value: Any) -> Optional[time]:
    if value is None:
        return None
    if isinstance(value, time):
        return value
    if isinstance(value, str):
        try:
            parts = [int(v) for v in value.split(":")[:2]]
            if len(parts) == 2:
                return time(parts[0], parts[1])
        except Exception:
            return None
    return None


def is_inside_shift(local_ts: datetime, shifts: list[Any]) -> bool:
    if not shifts:
        return False
    now_t = local_ts.time()
    weekday = local_ts.weekday()
    for shift in shifts:
        if not _normalize_shift_attr(shift, "is_active", True):
            continue
        start = _parse_shift_time(_normalize_shift_attr(shift, "shift_start", None))
        end = _parse_shift_time(_normalize_shift_attr(shift, "shift_end", None))
        if start is None or end is None:
            continue
        day = _normalize_shift_attr(shift, "day_of_week", None)
        if end > start:
            if start <= now_t < end and (day is None or day == weekday):
                return True
        else:
            if now_t >= start and (day is None or day == weekday):
                return True
            if now_t < end and (day is None or day == (weekday - 1) % 7):
                return True
    return False


def split_loss_components(
    *,
    duration_sec: float,
    interval_energy_kwh: float,
    current_a: Optional[float],
    voltage_v: Optional[float],
    pf: Optional[float],
    idle_threshold: Optional[float],
    over_threshold: Optional[float],
    inside_shift: bool,
    power_kw: Optional[float] = None,
) -> tuple[float, float, float]:
    if duration_sec <= 0 or interval_energy_kwh <= 0:
        return 0.0, 0.0, 0.0

    # Outside-shift running is the canonical financial truth and always owns the full interval.
    if not inside_shift:
        return 0.0, interval_energy_kwh, 0.0

    idle_kwh = 0.0
    offhours_kwh = 0.0
    over_excess_kwh = 0.0

    is_idle = idle_threshold is not None and current_a is not None and current_a > 0.0 and current_a <= idle_threshold
    if is_idle:
        idle_kwh = interval_energy_kwh
        return idle_kwh, offhours_kwh, over_excess_kwh

    # Overconsumption intervals are treated as fully avoidable energy. The
    # business need is that a device running entirely in waste mode should have
    # today's energy and today's loss converge, not just the excess-above-threshold slice.
    if over_threshold is not None and current_a is not None and current_a > over_threshold and current_a > 0:
        over_excess_kwh = interval_energy_kwh

    return idle_kwh, offhours_kwh, over_excess_kwh


def build_samples(
    rows: list[dict[str, Any]],
    platform_tz: ZoneInfo,
    *,
    config_source: Any = None,
    max_gap_sec: float | None = MAX_GAP_SEC,
) -> list[IntervalSample]:
    points: list[tuple[datetime, dict[str, Any]]] = []
    for row in rows:
        ts = parse_ts(row.get("timestamp") or row.get("_time"))
        if ts is None:
            continue
        ts = _to_utc(ts)
        points.append((ts, row))

    points.sort(key=lambda item: item[0])
    if not points:
        return []

    samples: list[IntervalSample] = []
    power_config = build_device_power_config(config_source or {})
    normalized_points: list[tuple[datetime, NormalizedTelemetrySample]] = [
        (ts, normalize_telemetry_sample(row, power_config))
        for ts, row in points
    ]
    for idx, (ts, row) in enumerate(points):
        duration_sec = 0.0
        if idx < len(points) - 1:
            nxt_ts = points[idx + 1][0]
            duration_sec = max(0.0, (nxt_ts - ts).total_seconds())
            if max_gap_sec is not None and duration_sec > max_gap_sec:
                duration_sec = 0.0
        normalized = normalized_points[idx][1]
        next_normalized = normalized_points[idx + 1][1] if idx < len(points) - 1 else None
        delta = (
            compute_interval_energy_delta(normalized, next_normalized, max_fallback_gap_seconds=max_gap_sec or MAX_GAP_SEC)
            if next_normalized is not None
            else None
        )
        power_kw, power_estimated = _power_kw_and_estimate(row)
        if delta is not None and duration_sec > 0:
            power_kw = max(delta.business_energy_delta_kwh * 3600.0 / duration_sec, 0.0)
            # This flag feeds PF-assumption warnings. Active-power integration is
            # estimated energy, but it does not mean PF was assumed.
            power_estimated = delta.energy_delta_method == "derived_vi_assumed_pf"
        samples.append(
            IntervalSample(
                ts_utc=ts,
                ts_local=ts.astimezone(platform_tz),
                duration_sec=duration_sec,
                power_kw=power_kw,
                power_estimated=power_estimated,
                current_a=normalized.current_a,
                voltage_v=normalized.voltage_v,
                pf=normalized.pf_business,
            )
        )
    return samples


def aggregate_window(
    rows: list[dict[str, Any]],
    *,
    platform_tz: ZoneInfo,
    shifts: list[Any],
    idle_threshold: Optional[float],
    over_threshold: Optional[float],
    config_source: Any = None,
    max_gap_sec: float | None = MAX_GAP_SEC,
) -> WindowAccounting:
    accounting = WindowAccounting()
    samples = build_samples(rows, platform_tz, config_source=config_source, max_gap_sec=max_gap_sec)
    accounting.samples = len(samples)

    for sample in samples:
        if sample.duration_sec <= 0 or sample.power_kw is None:
            continue

        interval_energy = max(0.0, sample.power_kw * sample.duration_sec / 3600.0)
        if interval_energy <= 0:
            continue
        if sample.power_estimated:
            accounting.total.pf_estimated = True

        running = (sample.power_kw > 0.0) or (sample.current_a is not None and sample.current_a > 0.0)
        if not running:
            continue

        idle_kwh, offhours_kwh, over_excess_kwh = split_loss_components(
            duration_sec=sample.duration_sec,
            interval_energy_kwh=interval_energy,
            current_a=sample.current_a,
            voltage_v=sample.voltage_v,
            pf=sample.pf,
            idle_threshold=idle_threshold,
            over_threshold=over_threshold,
            inside_shift=is_inside_shift(sample.ts_local, shifts),
            power_kw=sample.power_kw,
        )

        day_bucket = sample.ts_local.date()
        day = accounting.by_day.setdefault(day_bucket, WindowTotals())
        day.energy_kwh += interval_energy
        day.idle_kwh += idle_kwh
        day.offhours_kwh += offhours_kwh
        day.overconsumption_kwh += over_excess_kwh
        day.total_loss_kwh += idle_kwh + offhours_kwh + over_excess_kwh
        if idle_kwh > 0:
            day.idle_duration_sec += int(sample.duration_sec)
        if offhours_kwh > 0:
            day.offhours_duration_sec += int(sample.duration_sec)
        if over_excess_kwh > 0:
            day.overconsumption_duration_sec += int(sample.duration_sec)
        if sample.power_estimated:
            day.pf_estimated = True

        accounting.total.energy_kwh += interval_energy
        accounting.total.idle_kwh += idle_kwh
        accounting.total.offhours_kwh += offhours_kwh
        accounting.total.overconsumption_kwh += over_excess_kwh
        accounting.total.total_loss_kwh += idle_kwh + offhours_kwh + over_excess_kwh
        if idle_kwh > 0:
            accounting.total.idle_duration_sec += int(sample.duration_sec)
        if offhours_kwh > 0:
            accounting.total.offhours_duration_sec += int(sample.duration_sec)
        if over_excess_kwh > 0:
            accounting.total.overconsumption_duration_sec += int(sample.duration_sec)
        if sample.power_estimated:
            accounting.total.pf_estimated = True

    return accounting
