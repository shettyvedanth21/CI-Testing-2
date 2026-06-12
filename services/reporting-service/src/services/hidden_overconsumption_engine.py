from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd
from services.shared.telemetry_normalization import (
    effective_business_power_w,
    normalize_telemetry_sample,
)


def _iter_days(start_date: date, end_date: date) -> list[date]:
    if end_date < start_date:
        return []
    days: list[date] = []
    cursor = start_date
    while cursor <= end_date:
        days.append(cursor)
        cursor += timedelta(days=1)
    return days


def _normalize_power_rows(
    rows: list[dict[str, Any]],
    device_power_config: dict[str, Any] | None = None,
) -> pd.DataFrame:
    normalized_rows: list[dict[str, Any]] = []
    for row in rows or []:
        sample = normalize_telemetry_sample(row, device_power_config or {})
        power_w = effective_business_power_w(sample)
        normalized_rows.append(
            {
                "timestamp": sample.timestamp,
                "power_w": float(power_w),
            }
        )

    if not normalized_rows:
        return pd.DataFrame(columns=["timestamp", "power_w"])

    df = pd.DataFrame(normalized_rows)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    df["power_w"] = pd.to_numeric(df["power_w"], errors="coerce")
    df = df.dropna(subset=["timestamp", "power_w"])
    if df.empty:
        return pd.DataFrame(columns=["timestamp", "power_w"])
    return df.sort_values("timestamp").reset_index(drop=True)


def _day_covered_duration_hours(day_df: pd.DataFrame) -> float:
    if day_df.empty or len(day_df) < 2:
        return 0.0

    ts_values = day_df["timestamp"].to_numpy()
    covered_seconds = 0.0
    for idx in range(1, len(ts_values)):
        dt_seconds = float((ts_values[idx] - ts_values[idx - 1]).total_seconds())
        if dt_seconds > 0:
            covered_seconds += dt_seconds
    return max(covered_seconds / 3600.0, 0.0)


def calculate_device_hidden_overconsumption_insight(
    *,
    rows: list[dict[str, Any]],
    start_date: date,
    end_date: date,
    device_id: str | None = None,
    device_name: str | None = None,
    daily_actual_energy_kwh: dict[str, float] | None = None,
    tariff_rate: float | None = None,
    device_power_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    actual_map = daily_actual_energy_kwh or {}
    power_df = _normalize_power_rows(rows, device_power_config=device_power_config)
    selected_days = _iter_days(start_date, end_date)

    breakdown: list[dict[str, Any]] = []
    weighted_p75_numerator = 0.0
    weighted_p75_denominator = 0.0

    for day in selected_days:
        day_key = day.isoformat()
        day_df = power_df[power_df["timestamp"].dt.date == day] if not power_df.empty else pd.DataFrame()

        actual_kwh = max(float(actual_map.get(day_key, 0.0) or 0.0), 0.0)

        sample_count = int(len(day_df))
        covered_duration_hours = _day_covered_duration_hours(day_df) if sample_count >= 2 else 0.0

        p75_power_w: float | None = None
        if sample_count > 0:
            p75_power_w = float(np.percentile(day_df["power_w"].to_numpy(dtype=float), 75))

        baseline_energy_kwh: float | None = None
        hidden_kwh = 0.0
        if p75_power_w is not None and covered_duration_hours > 0.0 and sample_count >= 2:
            baseline_energy_kwh = max((p75_power_w / 1000.0) * covered_duration_hours, 0.0)
            hidden_kwh = max(actual_kwh - baseline_energy_kwh, 0.0)
            weighted_p75_numerator += p75_power_w * covered_duration_hours
            weighted_p75_denominator += covered_duration_hours

        rounded_actual_kwh = round(actual_kwh, 4)
        rounded_baseline_kwh = round(baseline_energy_kwh, 4) if baseline_energy_kwh is not None else None
        difference_vs_baseline_kwh = (
            round(rounded_actual_kwh - rounded_baseline_kwh, 4)
            if rounded_baseline_kwh is not None
            else None
        )
        status = "Unavailable"
        if difference_vs_baseline_kwh is not None:
            if difference_vs_baseline_kwh > 0:
                status = "Above Baseline"
            elif difference_vs_baseline_kwh < 0:
                status = "Below Baseline"
            else:
                status = "Within Baseline"
        rounded_hidden_kwh = round(hidden_kwh, 4)
        hidden_cost = round(rounded_hidden_kwh * tariff_rate, 2) if tariff_rate is not None else None

        breakdown.append(
            {
                "date": day_key,
                "device_id": device_id,
                "device_name": device_name or device_id,
                "actual_energy_kwh": rounded_actual_kwh,
                "p75_power_baseline_w": round(p75_power_w, 4) if p75_power_w is not None else None,
                "baseline_energy_kwh": rounded_baseline_kwh,
                "difference_vs_baseline_kwh": difference_vs_baseline_kwh,
                "status": status,
                "hidden_overconsumption_kwh": rounded_hidden_kwh,
                "hidden_overconsumption_cost": hidden_cost,
                "sample_count": sample_count,
                "covered_duration_hours": round(covered_duration_hours, 4),
                "tariff_rate_used": tariff_rate,
            }
        )

    aggregate_p75_reference_w = (
        round(weighted_p75_numerator / weighted_p75_denominator, 4)
        if weighted_p75_denominator > 0
        else None
    )
    total_actual = round(sum(float(row.get("actual_energy_kwh") or 0.0) for row in breakdown), 4)
    total_baseline = round(
        sum(float(row.get("baseline_energy_kwh") or 0.0) for row in breakdown if row.get("baseline_energy_kwh") is not None),
        4,
    )
    total_hidden = round(sum(float(row.get("hidden_overconsumption_kwh") or 0.0) for row in breakdown), 4)
    total_hidden_cost = (
        round(
            sum(
                float(row.get("hidden_overconsumption_cost") or 0.0)
                for row in breakdown
                if row.get("hidden_overconsumption_cost") is not None
            ),
            2,
        )
        if tariff_rate is not None
        else None
    )
    summary = {
        "selected_days": len(selected_days),
        "total_actual_energy_kwh": total_actual,
        "aggregate_p75_baseline_reference": aggregate_p75_reference_w,
        "total_baseline_energy_kwh": total_baseline,
        "total_hidden_overconsumption_kwh": total_hidden,
        "total_hidden_overconsumption_cost": total_hidden_cost,
        "tariff_rate_used": tariff_rate,
    }

    return {
        "summary": summary,
        "daily_breakdown": breakdown,
        "aggregation_rule": {
            "total_baseline_energy_kwh": "sum(daily_baseline_energy_kwh where available)",
            "total_hidden_overconsumption_kwh": "sum(daily_hidden_overconsumption_kwh)",
            "total_hidden_overconsumption_cost": "sum(daily_hidden_overconsumption_cost) when tariff exists",
            "aggregate_p75_baseline_reference": "duration-weighted mean of daily p75 baselines in W",
        },
    }


def aggregate_hidden_overconsumption_insight(
    *,
    per_device_insights: list[dict[str, Any]],
    start_date: date,
    end_date: date,
    tariff_rate: float | None = None,
) -> dict[str, Any]:
    selected_days = [d.isoformat() for d in _iter_days(start_date, end_date)]
    by_day = {
        day: {
            "date": day,
            "actual_energy_kwh": 0.0,
            "baseline_energy_kwh": 0.0,
            "hidden_overconsumption_kwh": 0.0,
            "hidden_overconsumption_cost": 0.0 if tariff_rate is not None else None,
            "sample_count": 0,
            "covered_duration_hours": 0.0,
            "_weighted_p75_numerator": 0.0,
        }
        for day in selected_days
    }

    for insight in per_device_insights or []:
        for row in insight.get("daily_breakdown", []) or []:
            day_key = str(row.get("date") or "")
            if day_key not in by_day:
                continue
            target = by_day[day_key]

            actual = float(row.get("actual_energy_kwh") or 0.0)
            baseline = float(row.get("baseline_energy_kwh") or 0.0)
            hidden = float(row.get("hidden_overconsumption_kwh") or 0.0)
            sample_count = int(row.get("sample_count") or 0)
            covered_hours = float(row.get("covered_duration_hours") or 0.0)
            p75_power_w = row.get("p75_power_baseline_w")

            target["actual_energy_kwh"] += actual
            target["baseline_energy_kwh"] += baseline
            target["hidden_overconsumption_kwh"] += hidden
            target["sample_count"] += sample_count
            target["covered_duration_hours"] += covered_hours
            if target["hidden_overconsumption_cost"] is not None:
                target["hidden_overconsumption_cost"] += float(row.get("hidden_overconsumption_cost") or 0.0)
            if isinstance(p75_power_w, (int, float)) and covered_hours > 0.0:
                target["_weighted_p75_numerator"] += float(p75_power_w) * covered_hours

    device_breakdown: list[dict[str, Any]] = []
    for insight in per_device_insights or []:
        for row in insight.get("daily_breakdown", []) or []:
            device_breakdown.append(
                {
                    "date": str(row.get("date") or ""),
                    "device_id": row.get("device_id"),
                    "device_name": row.get("device_name") or row.get("device_id"),
                    "actual_energy_kwh": round(float(row.get("actual_energy_kwh") or 0.0), 4),
                    "p75_power_baseline_w": (
                        round(float(row.get("p75_power_baseline_w")), 4)
                        if isinstance(row.get("p75_power_baseline_w"), (int, float))
                        else None
                    ),
                    "baseline_energy_kwh": (
                        round(float(row.get("baseline_energy_kwh")), 4)
                        if isinstance(row.get("baseline_energy_kwh"), (int, float))
                        else None
                    ),
                    "difference_vs_baseline_kwh": (
                        round(float(row.get("difference_vs_baseline_kwh")), 4)
                        if isinstance(row.get("difference_vs_baseline_kwh"), (int, float))
                        else None
                    ),
                    "status": row.get("status") or "Unavailable",
                    "hidden_overconsumption_kwh": round(float(row.get("hidden_overconsumption_kwh") or 0.0), 4),
                    "hidden_overconsumption_cost": (
                        round(float(row.get("hidden_overconsumption_cost")), 2)
                        if row.get("hidden_overconsumption_cost") is not None
                        else None
                    ),
                    "sample_count": int(row.get("sample_count") or 0),
                    "covered_duration_hours": round(float(row.get("covered_duration_hours") or 0.0), 4),
                    "tariff_rate_used": row.get("tariff_rate_used"),
                }
            )
    device_breakdown.sort(
        key=lambda row: (
            str(row.get("date") or ""),
            str(row.get("device_name") or row.get("device_id") or ""),
            str(row.get("device_id") or ""),
        )
    )

    daily_breakdown: list[dict[str, Any]] = []
    weighted_p75_numerator = 0.0
    weighted_p75_denominator = 0.0

    for day_key in selected_days:
        row = by_day[day_key]
        covered_hours = float(row["covered_duration_hours"])
        day_p75 = (
            row["_weighted_p75_numerator"] / covered_hours
            if covered_hours > 0.0
            else None
        )
        weighted_p75_numerator += float(row["_weighted_p75_numerator"])
        weighted_p75_denominator += covered_hours

        daily_breakdown.append(
            {
                "date": day_key,
                "actual_energy_kwh": round(float(row["actual_energy_kwh"]), 4),
                "p75_power_baseline_w": round(day_p75, 4) if day_p75 is not None else None,
                "baseline_energy_kwh": round(float(row["baseline_energy_kwh"]), 4),
                "hidden_overconsumption_kwh": round(float(row["hidden_overconsumption_kwh"]), 4),
                "hidden_overconsumption_cost": (
                    round(float(row["hidden_overconsumption_cost"]), 2)
                    if tariff_rate is not None and row["hidden_overconsumption_cost"] is not None
                    else None
                ),
                "sample_count": int(row["sample_count"]),
                "covered_duration_hours": round(covered_hours, 4),
                "tariff_rate_used": tariff_rate,
            }
        )

    total_actual = round(sum(float(row.get("actual_energy_kwh") or 0.0) for row in daily_breakdown), 4)
    total_baseline = round(
        sum(float(row.get("baseline_energy_kwh") or 0.0) for row in daily_breakdown if row.get("baseline_energy_kwh") is not None),
        4,
    )
    total_hidden = round(sum(float(row.get("hidden_overconsumption_kwh") or 0.0) for row in daily_breakdown), 4)
    total_cost = (
        round(
            sum(
                float(row.get("hidden_overconsumption_cost") or 0.0)
                for row in daily_breakdown
                if row.get("hidden_overconsumption_cost") is not None
            ),
            2,
        )
        if tariff_rate is not None
        else None
    )
    aggregate_p75_reference_w = (
        round(weighted_p75_numerator / weighted_p75_denominator, 4)
        if weighted_p75_denominator > 0.0
        else None
    )
    summary = {
        "selected_days": len(selected_days),
        "total_actual_energy_kwh": round(total_actual, 4),
        "aggregate_p75_baseline_reference": aggregate_p75_reference_w,
        "total_baseline_energy_kwh": round(total_baseline, 4),
        "total_hidden_overconsumption_kwh": round(total_hidden, 4),
        "total_hidden_overconsumption_cost": total_cost,
        "tariff_rate_used": tariff_rate,
    }

    insight_text = None
    if summary["total_hidden_overconsumption_kwh"] > 0:
        insight_text = (
            f"Hidden overconsumption above P75 baseline is "
            f"{summary['total_hidden_overconsumption_kwh']:.2f} kWh"
        )
        if summary["total_hidden_overconsumption_cost"] is not None:
            insight_text += f" (estimated cost {summary['total_hidden_overconsumption_cost']:.2f})."
        else:
            insight_text += "."

    return {
        "summary": summary,
        "daily_breakdown": daily_breakdown,
        "device_breakdown": device_breakdown,
        "aggregation_rule": {
            "total_baseline_energy_kwh": "sum(daily_baseline_energy_kwh)",
            "total_hidden_overconsumption_kwh": "sum(daily_hidden_overconsumption_kwh)",
            "total_hidden_overconsumption_cost": "sum(daily_hidden_overconsumption_cost) when tariff exists",
            "aggregate_p75_baseline_reference": "duration-weighted mean of aggregated daily p75 baselines in W",
        },
        "insight_text": insight_text,
    }
