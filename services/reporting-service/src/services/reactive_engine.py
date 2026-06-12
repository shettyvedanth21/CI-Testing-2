import math
from typing import Any

from services.shared.telemetry_normalization import normalize_telemetry_sample


def calculate_reactive(
    rows: list[dict],
    phase_type: str,
    device_power_config: dict[str, Any] | None = None,
) -> dict:
    if not rows:
        return {
            "success": False,
            "error_code": "INSUFFICIENT_REACTIVE_DATA",
            "error_message": "reactive_power or (power + power_factor) required."
        }
    
    first_row = rows[0]
    
    if "reactive_power" in first_row:
        energy_varh = 0.0
        
        for i in range(len(rows) - 1):
            delta_seconds = (rows[i + 1]["timestamp"] - rows[i]["timestamp"]).total_seconds()
            avg_reactive_var = (rows[i]["reactive_power"] + rows[i + 1]["reactive_power"]) / 2
            energy_varh += avg_reactive_var * delta_seconds / 3600
        
        total_kvarh = energy_varh / 1000
        pfs = [
            normalize_telemetry_sample(r, device_power_config or {}).pf_business or 0
            for r in rows
            if "power_factor" in r or "pf" in r
        ]
        
        pf_below_threshold = sum(1 for pf in pfs if pf < 0.90)
        pf_below_threshold_pct = (pf_below_threshold / len(pfs) * 100) if pfs else 0
        
        pf_distribution = {"good": 0, "acceptable": 0, "poor": 0}
        for pf in pfs:
            if pf >= 0.95:
                pf_distribution["good"] += 1
            elif pf >= 0.85:
                pf_distribution["acceptable"] += 1
            else:
                pf_distribution["poor"] += 1
        
        return {
            "success": True,
            "data": {
                "total_kvarh": round(total_kvarh, 4),
                "avg_power_factor": round(sum(pfs) / len(pfs), 4) if pfs else 0,
                "min_power_factor": round(min(pfs), 4) if pfs else 0,
                "pf_below_threshold_pct": round(pf_below_threshold_pct, 2),
                "pf_distribution": pf_distribution
            }
        }
    
    if ("power" in first_row or "active_power" in first_row) and ("power_factor" in first_row or "pf" in first_row):
        derived_rows = []
        
        for row in rows:
            normalized = normalize_telemetry_sample(row, device_power_config or {})
            if normalized.pf_business is not None and normalized.pf_business > 0 and normalized.business_power_w > 0:
                reactive_w = normalized.business_power_w * math.tan(math.acos(normalized.pf_business))
                derived_rows.append({
                    "timestamp": normalized.timestamp,
                    "reactive_power": reactive_w
                })
        
        if not derived_rows:
            return {
                "success": False,
                "error_code": "INSUFFICIENT_REACTIVE_DATA",
                "error_message": "reactive_power or (power + power_factor) required."
            }
        
        energy_varh = 0.0
        for i in range(len(derived_rows) - 1):
            delta_seconds = (derived_rows[i + 1]["timestamp"] - derived_rows[i]["timestamp"]).total_seconds()
            avg_reactive_var = (derived_rows[i]["reactive_power"] + derived_rows[i + 1]["reactive_power"]) / 2
            energy_varh += avg_reactive_var * delta_seconds / 3600
        
        total_kvarh = energy_varh / 1000
        pfs = [
            normalize_telemetry_sample(r, device_power_config or {}).pf_business or 0
            for r in rows
            if "power_factor" in r or "pf" in r
        ]
        
        pf_below_threshold = sum(1 for pf in pfs if pf < 0.90)
        pf_below_threshold_pct = (pf_below_threshold / len(pfs) * 100) if pfs else 0
        
        pf_distribution = {"good": 0, "acceptable": 0, "poor": 0}
        for pf in pfs:
            if pf >= 0.95:
                pf_distribution["good"] += 1
            elif pf >= 0.85:
                pf_distribution["acceptable"] += 1
            else:
                pf_distribution["poor"] += 1
        
        return {
            "success": True,
            "data": {
                "total_kvarh": round(total_kvarh, 4),
                "avg_power_factor": round(sum(pfs) / len(pfs), 4) if pfs else 0,
                "min_power_factor": round(min(pfs), 4) if pfs else 0,
                "pf_below_threshold_pct": round(pf_below_threshold_pct, 2),
                "pf_distribution": pf_distribution
            }
        }
    
    return {
        "success": False,
        "error_code": "INSUFFICIENT_REACTIVE_DATA",
        "error_message": "reactive_power or (power + power_factor) required."
    }
