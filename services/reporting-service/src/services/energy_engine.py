from __future__ import annotations

from collections import defaultdict
from typing import Any, List

from services.shared.telemetry_normalization import effective_business_power_w, normalize_telemetry_sample

from src.services.report_engine import compute_device_report


def calculate_energy(
    rows: List[dict],
    phase_type: str,
    device_power_config: dict[str, Any] | None = None,
) -> dict:
    if not rows:
        return {
            "success": False,
            "error_code": "NO_TELEMETRY_DATA",
            "error_message": "No telemetry data in selected range.",
        }

    # Comparison/local reporting must use the same physically plausible
    # normalized-business-power integration basis as the money-facing
    # consumption report path. The cumulative counter may be quantized or
    # anomalous and is not authoritative here.
    device_result = compute_device_report(
        rows=rows,
        device_id="comparison-device",
        device_name="comparison-device",
        data_source_type="metered",
        device_power_config=device_power_config,
    )
    if device_result.total_kwh is None:
        return {
            "success": False,
            "error_code": "INSUFFICIENT_TELEMETRY_DATA",
            "error_message": device_result.error or "Required parameters (power OR voltage/current/power_factor) not available.",
        }

    normalized_samples = [normalize_telemetry_sample(row, device_power_config or {}) for row in rows]
    power_series = [
        {
            "timestamp": sample.timestamp,
            "power_w": round(float(effective_business_power_w(sample)), 6),
        }
        for sample in normalized_samples
    ]
    powers = [float(point["power_w"]) for point in power_series]
    first_ts = normalized_samples[0].timestamp if normalized_samples else None
    last_ts = normalized_samples[-1].timestamp if normalized_samples else None

    daily_kwh = defaultdict(float)
    for day in device_result.daily_breakdown:
        day_key = str(day.get("date") or "")
        value = day.get("energy_kwh")
        if day_key and isinstance(value, (int, float)):
            daily_kwh[day_key] += float(value)

    return {
        "success": True,
        "data": {
            "total_kwh": round(float(device_result.total_kwh or 0.0), 2),
            "total_wh": round(float(device_result.total_kwh or 0.0) * 1000.0, 2),
            "avg_power_w": round(sum(powers) / len(powers), 2) if powers else 0.0,
            "peak_power_w": round(max(powers), 2) if powers else 0.0,
            "min_power_w": round(min(powers), 2) if powers else 0.0,
            "data_points": len(power_series),
            "computation_mode": "normalized_business_power",
            "energy_basis": "normalized_telemetry",
            "energy_quality": device_result.quality,
            "phase_type_used": phase_type,
            "duration_hours": round(float(device_result.total_hours or 0.0), 2),
            "daily_kwh": {day: round(value, 2) for day, value in daily_kwh.items()},
            "power_series": power_series,
            "warnings": list(device_result.warnings or []),
            "source_method": device_result.method,
            "peak_demand_kw": device_result.peak_demand_kw,
            "peak_demand_timestamp": device_result.peak_timestamp,
        }
    }
