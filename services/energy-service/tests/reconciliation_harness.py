from __future__ import annotations

import csv
import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "services" / "energy-service"))
sys.path.insert(1, str(ROOT))

from services.shared.energy_accounting import aggregate_window  # noqa: E402
from services.shared.telemetry_normalization import (  # noqa: E402
    INTERVAL_ENERGY_ALGORITHM_VERSION,
    NORMALIZATION_VERSION,
    compute_interval_energy_delta,
    normalize_telemetry_sample,
)


@dataclass(frozen=True)
class ReconciliationSurfaceTotals:
    energy_kwh: float
    idle_kwh: float = 0.0
    offhours_kwh: float = 0.0
    overconsumption_kwh: float = 0.0
    loss_kwh: float = 0.0
    peak_kw: float | None = None
    load_factor_pct: float | None = None
    method: str = "unknown"


@dataclass(frozen=True)
class ReconciliationComparison:
    reporting: ReconciliationSurfaceTotals
    canonical: ReconciliationSurfaceTotals

    @property
    def drift_kwh(self) -> float:
        return round(self.canonical.energy_kwh - self.reporting.energy_kwh, 6)

    @property
    def drift_ratio(self) -> float:
        if self.reporting.energy_kwh <= 0:
            return 0.0
        return round(self.drift_kwh / self.reporting.energy_kwh, 6)


def parse_influx_annotated_csv(path: str | Path) -> list[dict[str, Any]]:
    """Parse an Influx annotated CSV export into regular telemetry rows."""

    rows: list[dict[str, Any]] = []
    with Path(path).open(newline="") as handle:
        reader = csv.reader(handle)
        header: list[str] | None = None
        for raw in reader:
            if raw and raw[0] == "":
                header = raw
                break
        if header is None:
            raise ValueError("Influx annotated CSV header row was not found")

        for raw in reader:
            if len(raw) != len(header):
                continue
            item = dict(zip(header, raw))
            timestamp = item.get("_time") or item.get("timestamp")
            if not timestamp:
                continue
            rows.append(
                {
                    "timestamp": timestamp,
                    "device_id": item.get("device_id"),
                    "tenant_id": item.get("tenant_id"),
                    "current": _to_number(item.get("current")),
                    "current_l1": _to_number(item.get("current_l1")),
                    "current_l2": _to_number(item.get("current_l2")),
                    "current_l3": _to_number(item.get("current_l3")),
                    "energy_kwh": _to_number(item.get("energy_kwh")),
                    "frequency": _to_number(item.get("frequency")),
                    "power": _to_number(item.get("power")),
                    "power_factor": _to_number(item.get("power_factor")),
                    "voltage": _to_number(item.get("voltage")),
                    "voltage_l1": _to_number(item.get("voltage_l1")),
                    "voltage_l2": _to_number(item.get("voltage_l2")),
                    "voltage_l3": _to_number(item.get("voltage_l3")),
                    "voltage_line": _to_number(item.get("voltage_line")),
                }
            )
    return rows


def compare_financial_surfaces(
    rows: list[dict[str, Any]],
    *,
    shifts: list[dict[str, Any]] | None = None,
    idle_threshold: float | None = None,
    over_threshold: float | None = None,
    platform_tz: ZoneInfo = ZoneInfo("Asia/Kolkata"),
) -> ReconciliationComparison:
    return ReconciliationComparison(
        reporting=reporting_surface_totals(rows),
        canonical=canonical_surface_totals(
            rows,
            shifts=shifts or [],
            idle_threshold=idle_threshold,
            over_threshold=over_threshold,
            platform_tz=platform_tz,
        ),
    )


def reporting_surface_totals(rows: list[dict[str, Any]]) -> ReconciliationSurfaceTotals:
    """Run the reporting-service financial path currently used by device totals."""

    module = _load_reporting_engine()
    result = module._compute_from_df(  # type: ignore[attr-defined]
        pd.DataFrame(rows),
        "RECONCILE-DEVICE",
        "Reconciliation Device",
        "metered",
        include_daily=False,
    )
    return ReconciliationSurfaceTotals(
        energy_kwh=round(float(result.total_kwh or 0.0), 6),
        peak_kw=result.peak_demand_kw,
        load_factor_pct=result.load_factor_pct,
        method=str(result.method),
    )


def canonical_surface_totals(
    rows: list[dict[str, Any]],
    *,
    shifts: list[dict[str, Any]],
    idle_threshold: float | None,
    over_threshold: float | None,
    platform_tz: ZoneInfo,
) -> ReconciliationSurfaceTotals:
    """Run the canonical counter-aware interval accounting used for financial truth."""

    sorted_rows = sorted(rows, key=lambda row: str(row.get("timestamp") or ""))
    previous = None
    energy_kwh = 0.0
    for row in sorted_rows:
        current = normalize_telemetry_sample(row, {})
        if previous is not None:
            delta = compute_interval_energy_delta(
                previous,
                current,
                max_fallback_gap_seconds=900.0,
                max_counter_gap_seconds=900.0,
            )
            energy_kwh += float(delta.business_energy_delta_kwh or 0.0)
        previous = current

    accounting = aggregate_window(
        sorted_rows,
        platform_tz=platform_tz,
        shifts=shifts,
        idle_threshold=idle_threshold,
        over_threshold=over_threshold,
        config_source={},
        max_gap_sec=900.0,
    )
    return ReconciliationSurfaceTotals(
        energy_kwh=round(energy_kwh, 6),
        idle_kwh=round(float(accounting.total.idle_kwh or 0.0), 6),
        offhours_kwh=round(float(accounting.total.offhours_kwh or 0.0), 6),
        overconsumption_kwh=round(float(accounting.total.overconsumption_kwh or 0.0), 6),
        loss_kwh=round(float(accounting.total.total_loss_kwh or 0.0), 6),
        method=f"{INTERVAL_ENERGY_ALGORITHM_VERSION}/{NORMALIZATION_VERSION}",
    )


def _load_reporting_engine():
    module_name = "_reconciliation_reporting_engine"
    if module_name in sys.modules:
        return sys.modules[module_name]
    path = ROOT / "services" / "reporting-service" / "src" / "services" / "report_engine.py"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load reporting engine from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _to_number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except Exception:
        return None
