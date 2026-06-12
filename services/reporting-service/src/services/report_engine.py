from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd
from services.shared.telemetry_normalization import (
    NormalizedIntervalEnergy,
    NormalizedTelemetrySample,
    build_device_power_config,
    compute_interval_energy_delta,
    effective_business_power_w,
    normalize_telemetry_sample,
)


@dataclass
class DeviceComputationResult:
    device_id: str
    device_name: str
    data_source_type: str
    availability: dict[str, bool]
    method: str
    quality: str
    warnings: list[str]
    error: str | None
    total_kwh: float | None
    peak_demand_kw: float | None
    peak_timestamp: str | None
    average_load_kw: float | None
    load_factor_pct: float | None
    load_factor_band: str | None
    total_hours: float
    daily_breakdown: list[dict[str, Any]]
    overtime_breakdown: list[dict[str, Any]]
    overtime_summary: dict[str, Any] | None
    power_factor: dict[str, Any] | None
    reactive: dict[str, Any] | None
    power_unit_input: str
    power_unit_normalized_to: str
    normalization_applied: bool


def _to_df(rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows).copy()
    if "timestamp" not in df.columns and "_time" in df.columns:
        df = df.rename(columns={"_time": "timestamp"})
    if "timestamp" not in df.columns:
        return pd.DataFrame()

    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    df = df.dropna(subset=["timestamp"]).sort_values("timestamp")

    numeric_candidates = [
        "energy_kwh",
        "power",
        "current",
        "voltage",
        "power_factor",
        "frequency",
        "kvar",
        "reactive_power",
        "run_hours",
    ]
    for col in numeric_candidates:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df.reset_index(drop=True)


def _availability(df: pd.DataFrame) -> dict[str, bool]:
    fields = [
        "energy_kwh",
        "power",
        "current",
        "voltage",
        "power_factor",
        "frequency",
        "kvar",
        "reactive_power",
        "run_hours",
    ]
    return {f: (f in df.columns and df[f].notna().sum() > 0) for f in fields}


def _series_with_time(df: pd.DataFrame, col: str) -> tuple[np.ndarray, np.ndarray]:
    sub = df[["timestamp", col]].dropna()
    if sub.empty:
        return np.array([]), np.array([])
    # timestamp() keeps epoch seconds precision across datetime backends.
    ts = sub["timestamp"].map(lambda x: x.timestamp()).to_numpy(dtype=float)
    vals = sub[col].to_numpy(dtype=float)
    return ts, vals


def _integrate_kwh(ts_sec: np.ndarray, power_kw: np.ndarray) -> tuple[float | None, float, list[str]]:
    if len(ts_sec) < 2 or len(power_kw) < 2:
        return None, 0.0, []

    warnings: list[str] = []
    total_kwh = 0.0
    total_hours = 0.0
    saw_zero_gap = False
    saw_negative_gap = False

    for idx in range(1, len(ts_sec)):
        dt_sec = float(ts_sec[idx] - ts_sec[idx - 1])
        if dt_sec < 0:
            saw_negative_gap = True
            continue
        if dt_sec == 0:
            saw_zero_gap = True
            continue
        dt_hours = dt_sec / 3600.0
        total_hours += dt_hours
        total_kwh += ((float(power_kw[idx - 1]) + float(power_kw[idx])) / 2.0) * dt_hours

    if saw_negative_gap:
        warnings.append("NON_MONOTONIC_TIMESTAMPS: non-monotonic samples skipped during integration")
    if saw_zero_gap:
        warnings.append("TIMESTAMP_GAP_SKIPPED: duplicate timestamp samples skipped during integration")
    if total_hours <= 0:
        return None, 0.0, warnings
    return max(total_kwh, 0.0), max(total_hours, 0.0), warnings


def _load_factor_band(load_factor_pct: float | None) -> str | None:
    if load_factor_pct is None:
        return None
    if load_factor_pct < 30:
        return "poor"
    if load_factor_pct <= 70:
        return "moderate"
    return "good"


def _dedupe_warnings(values: list[str]) -> list[str]:
    seen = set()
    out: list[str] = []
    for item in values:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _build_normalized_row(
    sample: NormalizedTelemetrySample,
    original_row: dict[str, Any],
) -> dict[str, Any]:
    effective_power_w = effective_business_power_w(sample)
    quality_flags = set(sample.quality_flags)
    if sample.raw_source_power_field is None and effective_power_w > 0:
        quality_flags.add("power_derived_from_vi_pf")
        if sample.pf_business is None:
            quality_flags.add("pf_untrusted")
    normalized_row = dict(original_row)
    normalized_row.update({
        "timestamp": sample.timestamp,
        "energy_kwh": sample.energy_counter_kwh,
        "power": effective_power_w,
        "active_power": sample.raw_active_power_w,
        "net_power_w": sample.net_power_w,
        "import_power_w": sample.import_power_w,
        "export_power_w": sample.export_power_w,
        "current": sample.current_a,
        "voltage": sample.voltage_v,
        "power_factor": sample.pf_business,
        "pf_signed": sample.pf_signed,
        "quality_flags": list(sorted(quality_flags)),
        "raw_source_power_field": sample.raw_source_power_field,
    })
    return normalized_row


def _compute_overlap_subtraction(interval_details: list[NormalizedIntervalEnergy]) -> float:
    overlapping_fallback_kwh = 0.0
    for jump_idx in range(len(interval_details)):
        delta = interval_details[jump_idx]
        if delta.reason_code == "counter_accepted" and (delta.counter_delta_kwh or 0) > 0:
            for back_idx in range(jump_idx - 1, -1, -1):
                back_delta = interval_details[back_idx]
                if (
                    "counter_noise_floor_applied" in back_delta.quality_flags
                    and back_delta.energy_delta_method != "counter"
                    and back_delta.business_energy_delta_kwh > 0
                ):
                    overlapping_fallback_kwh += back_delta.business_energy_delta_kwh
                else:
                    break
    return overlapping_fallback_kwh


def _compute_from_df(
    df: pd.DataFrame,
    device_id: str,
    device_name: str,
    data_source_type: str,
    device_power_config: dict[str, Any] | None = None,
    include_daily: bool = True,
) -> DeviceComputationResult:
    warnings: list[str] = []
    error: str | None = None
    method = "insufficient"
    quality = "insufficient"
    total_kwh: float | None = None
    total_hours = 0.0
    power_unit_input = "unknown"
    power_unit_normalized_to = "kW"
    normalization_applied = False

    raw_rows = df.to_dict(orient="records")
    power_config = build_device_power_config(device_power_config or {})
    samples: list[NormalizedTelemetrySample] = []
    norm_rows_for_df: list[dict[str, Any]] = []
    for row in raw_rows:
        sample = normalize_telemetry_sample(row, power_config)
        if sample.timestamp is not None:
            samples.append(sample)
            norm_rows_for_df.append(_build_normalized_row(sample, row))
    samples.sort(key=lambda s: s.timestamp)
    working_df = _to_df(norm_rows_for_df) if norm_rows_for_df else pd.DataFrame()

    avail = _availability(working_df)

    if len(samples) >= 2:
        total_energy = 0.0
        total_hours = 0.0
        interval_details: list[NormalizedIntervalEnergy] = []
        for idx in range(1, len(samples)):
            delta = compute_interval_energy_delta(
                samples[idx - 1],
                samples[idx],
                max_fallback_gap_seconds=900.0,
            )
            total_energy += delta.business_energy_delta_kwh
            if delta.coverage_seconds > 0:
                total_hours += delta.elapsed_seconds / 3600.0
            interval_details.append(delta)

        overlapping_fallback_kwh = _compute_overlap_subtraction(interval_details)
        total_energy -= overlapping_fallback_kwh

        if total_hours > 0:
            total_kwh = round(max(total_energy, 0.0), 4)
            if total_kwh == 0.0:
                method = "insufficient"
                quality = "insufficient"
                error = "No computable energy — all telemetry intervals exceeded the maximum gap threshold"
            else:
                has_counter = any(
                    d.reason_code == "counter_accepted" and (d.counter_delta_kwh or 0) > 0
                    for d in interval_details
                )
                has_power = any(d.energy_delta_method == "power_integration" for d in interval_details)
                has_vi = any(d.energy_delta_method.startswith("derived_vi") for d in interval_details)
                if has_counter:
                    method = "counter_integration"
                    quality = "billing_grade"
                elif has_power:
                    method = "normalized_business_power"
                    quality = "high"
                elif has_vi:
                    method = "derived_vi_pf"
                    quality = "medium"
                else:
                    method = "normalized_business_power"
                    quality = "high"
            normalization_applied = True
            power_unit_input = "W"
            power_unit_normalized_to = "kW"

    # Priority 5: insufficient
    if total_kwh is None and avail["current"] and not avail["voltage"]:
        method = "insufficient_current_only"
        quality = "insufficient"
        error = "Insufficient telemetry — voltage required for energy calculation"

    if total_kwh is None and error is None:
        method = "insufficient_missing_fields"
        quality = "insufficient"
        error = "Insufficient telemetry — need one of: energy_kwh, power, or (current + voltage)"

    # Peak demand
    peak_demand_kw: float | None = None
    peak_timestamp: str | None = None
    if avail["power"]:
        sub = working_df[["timestamp", "power"]].copy()
        sub["power"] = pd.to_numeric(sub["power"], errors="coerce")
        sub = sub.dropna(subset=["power"])
        sub = sub[sub["power"] > 0.0]
        if not sub.empty:
            peak_idx = sub["power"].astype(float).idxmax()
            peak_demand_kw = round(float(sub.loc[peak_idx, "power"]) / 1000.0, 4)
            peak_timestamp = sub.loc[peak_idx, "timestamp"].isoformat()
            power_unit_input = "W"
            power_unit_normalized_to = "kW"
            normalization_applied = True

    average_load_kw: float | None = None
    load_factor_pct: float | None = None
    load_factor_band: str | None = None
    if total_kwh is not None and total_hours > 0:
        average_load_kw = round(total_kwh / total_hours, 4)
    if average_load_kw is not None and peak_demand_kw and peak_demand_kw > 0:
        load_factor_pct = round(max(0.0, min(100.0, (average_load_kw / peak_demand_kw) * 100.0)), 2)
        load_factor_band = _load_factor_band(load_factor_pct)

    # Daily breakdown
    daily_breakdown: list[dict[str, Any]] = []
    if include_daily and len(samples) >= 2:
        day_samples_map: dict[Any, list[NormalizedTelemetrySample]] = {}
        for s in samples:
            day_key = s.timestamp.date()
            if day_key not in day_samples_map:
                day_samples_map[day_key] = []
            day_samples_map[day_key].append(s)

        sorted_days = sorted(day_samples_map.keys())
        for i in range(1, len(sorted_days)):
            prev_day_samps = day_samples_map[sorted_days[i - 1]]
            curr_day_samps = day_samples_map[sorted_days[i]]
            if prev_day_samps and curr_day_samps:
                last_prev = prev_day_samps[-1]
                if curr_day_samps[0].timestamp > last_prev.timestamp:
                    curr_day_samps.insert(0, last_prev)

        for day in sorted(day_samples_map.keys()):
            day_samps = sorted(day_samples_map[day], key=lambda s: s.timestamp)
            if len(day_samps) < 2:
                continue
            day_energy = 0.0
            day_hours = 0.0
            day_details: list[NormalizedIntervalEnergy] = []
            for idx in range(1, len(day_samps)):
                delta = compute_interval_energy_delta(
                    day_samps[idx - 1],
                    day_samps[idx],
                    max_fallback_gap_seconds=900.0,
                )
                day_energy += delta.business_energy_delta_kwh
                if delta.coverage_seconds > 0:
                    day_hours += delta.elapsed_seconds / 3600.0
                day_details.append(delta)

            day_overlap = _compute_overlap_subtraction(day_details)
            day_energy -= day_overlap

            day_kwh: float | None = None
            if day_hours > 0:
                day_kwh = round(max(day_energy, 0.0), 4)

            day_peak_kw: float | None = None
            if avail["power"] and not working_df.empty:
                day_rows_df = working_df[working_df["timestamp"].dt.date == day]
                if not day_rows_df.empty:
                    sub = day_rows_df[["timestamp", "power"]].copy()
                    sub["power"] = pd.to_numeric(sub["power"], errors="coerce")
                    sub = sub.dropna(subset=["power"])
                    sub = sub[sub["power"] > 0.0]
                    if not sub.empty:
                        peak_idx = sub["power"].astype(float).idxmax()
                        day_peak_kw = round(float(sub.loc[peak_idx, "power"]) / 1000.0, 4)

            day_avg_kw: float | None = None
            if day_kwh is not None and day_hours > 0:
                day_avg_kw = round(day_kwh / day_hours, 4)

            if day_kwh is None or day_kwh == 0.0:
                day_method = "insufficient"
                day_quality = "insufficient"
            else:
                day_has_counter = any(
                    d.reason_code == "counter_accepted" and (d.counter_delta_kwh or 0) > 0
                    for d in day_details
                )
                day_method = "counter_integration" if day_has_counter else "normalized_business_power"
                day_quality = "billing_grade" if day_has_counter else "high"

            daily_breakdown.append({
                "date": str(day),
                "energy_kwh": day_kwh,
                "peak_demand_kw": day_peak_kw,
                "average_load_kw": day_avg_kw,
                "quality": day_quality,
                "method": day_method,
                "warnings": [],
            })

    power_factor = None
    if avail["power_factor"]:
        pf = pd.to_numeric(working_df["power_factor"], errors="coerce").dropna()
        if not pf.empty:
            avg_pf = float(pf.mean())
            min_pf = float(pf.min())
            if avg_pf < 0.85:
                status = "poor"
                recommendation = "Install capacitor banks to improve power factor above 0.95"
            elif avg_pf < 0.92:
                status = "moderate"
                recommendation = "Consider power factor correction"
            else:
                status = "good"
                recommendation = None
            power_factor = {
                "average": round(avg_pf, 4),
                "min": round(min_pf, 4),
                "status": status,
                "recommendation": recommendation,
            }

    reactive = None
    reactive_field = "kvar" if avail["kvar"] else "reactive_power" if avail["reactive_power"] else None
    if reactive_field:
        ts_sec, kvar_vals = _series_with_time(working_df, reactive_field)
        total_kvarh, _, _ = _integrate_kwh(ts_sec, kvar_vals)
        if total_kvarh is not None:
            ratio = None
            if total_kwh and total_kwh > 0:
                ratio = round(float(total_kvarh / total_kwh), 4)
            reactive = {
                "total_kvarh": round(float(total_kvarh), 4),
                "reactive_ratio": ratio,
                "field_used": reactive_field,
            }

    return DeviceComputationResult(
        device_id=device_id,
        device_name=device_name,
        data_source_type=data_source_type,
        availability=avail,
        method=method,
        quality=quality,
        warnings=_dedupe_warnings(warnings),
        error=error,
        total_kwh=total_kwh,
        peak_demand_kw=peak_demand_kw,
        peak_timestamp=peak_timestamp,
        average_load_kw=average_load_kw,
        load_factor_pct=load_factor_pct,
        load_factor_band=load_factor_band,
        total_hours=round(total_hours, 4),
        daily_breakdown=daily_breakdown,
        overtime_breakdown=[],
        overtime_summary=None,
        power_factor=power_factor,
        reactive=reactive,
        power_unit_input=power_unit_input,
        power_unit_normalized_to=power_unit_normalized_to,
        normalization_applied=normalization_applied,
    )


def compute_device_report(
    rows: list[dict[str, Any]],
    device_id: str,
    device_name: str,
    data_source_type: str,
    device_power_config: dict[str, Any] | None = None,
) -> DeviceComputationResult:
    df = _to_df(rows)
    if df.empty:
        return DeviceComputationResult(
            device_id=device_id,
            device_name=device_name,
            data_source_type=data_source_type,
            availability={},
            method="no_data",
            quality="insufficient",
            warnings=[],
            error="No telemetry data available for selected period",
            total_kwh=None,
            peak_demand_kw=None,
            peak_timestamp=None,
            average_load_kw=None,
            load_factor_pct=None,
            load_factor_band=None,
            total_hours=0.0,
            daily_breakdown=[],
            overtime_breakdown=[],
            overtime_summary=None,
            power_factor=None,
            reactive=None,
            power_unit_input="unknown",
            power_unit_normalized_to="kW",
            normalization_applied=False,
        )
    return _compute_from_df(df, device_id, device_name, data_source_type, device_power_config=device_power_config)
