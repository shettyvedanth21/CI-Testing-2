from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
from typing import Any, Optional
from zoneinfo import ZoneInfo

from services.shared.telemetry_normalization import compute_interval_energy_delta, normalize_telemetry_sample

UTC = ZoneInfo("UTC")

CURRENT_ALIASES = ["current", "phase_current"]
VOLTAGE_ALIASES = ["voltage"]
PF_ALIASES = ["power_factor", "pf"]
POWER_ALIASES = ["power_kw", "kw", "power", "active_power"]
ENERGY_ALIASES = ["energy_kwh", "kwh", "energy"]


@dataclass
class NormalizedInterval:
    ts: datetime
    duration_sec: float
    current_a: Optional[float]
    voltage_v: Optional[float]
    power_kw: Optional[float]
    pf: Optional[float]
    energy_kwh_counter: Optional[float]


def normalize_key(key: str) -> str:
    return key.strip().lower().replace("-", "_")


def is_numeric_value(value: Any) -> bool:
    if value is None:
        return False
    try:
        f = float(value)
        return not (math.isnan(f) or math.isinf(f))
    except Exception:
        return False


def safe_dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except Exception:
        return None


def find_field(row: dict[str, Any], aliases: list[str], contains_token: Optional[str] = None) -> Optional[str]:
    norm = {normalize_key(k): k for k in row.keys()}
    for a in aliases:
        if a in norm and is_numeric_value(row.get(norm[a])):
            return norm[a]
    if contains_token:
        for nk in sorted(norm.keys()):
            ok = norm[nk]
            if contains_token in nk and is_numeric_value(row.get(ok)):
                return ok
    return None


def extract_current(row: dict[str, Any]) -> tuple[Optional[float], Optional[str]]:
    key = find_field(row, CURRENT_ALIASES)
    if key and is_numeric_value(row.get(key)):
        return float(row[key]), key
    return None, None


def extract_voltage(row: dict[str, Any]) -> tuple[Optional[float], Optional[str]]:
    key = find_field(row, VOLTAGE_ALIASES)
    if key and is_numeric_value(row.get(key)):
        return float(row[key]), key
    return None, None


def extract_pf(row: dict[str, Any]) -> Optional[float]:
    key = find_field(row, PF_ALIASES, contains_token="power_factor")
    if key and is_numeric_value(row.get(key)):
        return float(row[key])
    return None


def extract_power_kw(row: dict[str, Any]) -> tuple[Optional[float], Optional[str], str, bool]:
    key = find_field(row, POWER_ALIASES, contains_token="power")
    if not key or not is_numeric_value(row.get(key)):
        return None, None, "unknown", False
    val = float(row[key])
    nk = normalize_key(key)
    if nk in {"power", "active_power"}:
        return val / 1000.0, key, "W", True
    if nk in {"kw", "power_kw"} or "kw" in nk:
        return val, key, "kW", False
    return val, key, "unknown", False


def extract_energy_kwh(row: dict[str, Any]) -> Optional[float]:
    key = find_field(row, ENERGY_ALIASES, contains_token="energy")
    if key and is_numeric_value(row.get(key)):
        return float(row[key])
    return None


def build_normalized_intervals(
    rows: list[dict[str, Any]],
    max_gap_seconds: float = 900.0,
    device_power_config: Optional[dict[str, Any]] = None,
) -> tuple[list[NormalizedInterval], dict[str, Any]]:
    sorted_rows = sorted(rows, key=lambda x: safe_dt(x.get("timestamp") or x.get("_time")) or datetime.min)
    intervals: list[NormalizedInterval] = []
    metadata = {
        "current_field_used": None,
        "power_unit_input": "unknown",
        "normalization_applied": False,
        "saw_zero_gap": False,
        "saw_negative_gap": False,
        "saw_large_gap": False,
        "large_gap_count": 0,
        "large_gap_total_sec": 0.0,
        "large_gap_max_sec": 0.0,
    }

    normalized_samples = [
        normalize_telemetry_sample(row, device_power_config or {})
        for row in sorted_rows
        if safe_dt(row.get("timestamp") or row.get("_time")) is not None
    ]

    sample_index = 0
    for i, row in enumerate(sorted_rows):
        ts = safe_dt(row.get("timestamp") or row.get("_time"))
        if ts is None:
            continue

        duration_sec = 0.0
        if i < len(sorted_rows) - 1:
            n_ts = safe_dt(sorted_rows[i + 1].get("timestamp") or sorted_rows[i + 1].get("_time"))
            if n_ts is not None:
                duration_sec = (n_ts - ts).total_seconds()
                if duration_sec < 0:
                    metadata["saw_negative_gap"] = True
                    duration_sec = 0.0
                elif duration_sec == 0:
                    metadata["saw_zero_gap"] = True
                elif duration_sec > max_gap_seconds:
                    metadata["saw_large_gap"] = True
                    metadata["large_gap_count"] += 1
                    metadata["large_gap_total_sec"] += duration_sec
                    metadata["large_gap_max_sec"] = max(metadata["large_gap_max_sec"], duration_sec)
                    duration_sec = 0.0

        normalized = normalized_samples[sample_index]
        current = normalized.current_a
        current_src = "normalized"
        voltage = normalized.voltage_v
        pf = normalized.pf_business
        power_kw = (normalized.business_power_w / 1000.0) if normalized.business_power_w > 0 else None
        power_src = normalized.raw_source_power_field
        p_unit_in = "W" if normalized.raw_source_power_field in {"power", "active_power"} else "kW"
        p_norm = normalized.raw_source_power_field in {"power", "active_power"}
        energy_kwh_counter = normalized.energy_counter_kwh

        if metadata["current_field_used"] is None and current_src:
            metadata["current_field_used"] = current_src
        if power_src and metadata["power_unit_input"] == "unknown":
            metadata["power_unit_input"] = p_unit_in
        if p_norm:
            metadata["normalization_applied"] = True

        sample_index += 1
        intervals.append(
            NormalizedInterval(
                ts=ts,
                duration_sec=max(0.0, duration_sec),
                current_a=current,
                voltage_v=voltage,
                power_kw=power_kw,
                pf=pf,
                energy_kwh_counter=energy_kwh_counter,
            )
        )

    return intervals, metadata
