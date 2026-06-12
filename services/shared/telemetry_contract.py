from __future__ import annotations

from typing import Iterable

BUSINESS_TELEMETRY_FIELDS = frozenset(
    {
        "active_power",
        "active_power_kw",
        "apparent_power",
        "cos_phi",
        "current",
        "energy",
        "energy_kwh",
        "frequency",
        "kva",
        "kvar",
        "kwh",
        "pf",
        "power",
        "power_factor",
        "power_kw",
        "powerfactor",
        "reactive_power",
        "run_hours",
        "temperature",
        "thd",
        "voltage",
    }
)

DIAGNOSTIC_PHASE_TELEMETRY_FIELDS = frozenset(
    {
        "current_l1",
        "current_l2",
        "current_l3",
        "i_l1",
        "i_l2",
        "i_l3",
        "power_l1",
        "power_l2",
        "power_l3",
        "power_factor_l1",
        "power_factor_l2",
        "power_factor_l3",
        "pf_l1",
        "pf_l2",
        "pf_l3",
        "voltage_l1",
        "voltage_l2",
        "voltage_l3",
        "v_l1",
        "v_l2",
        "v_l3",
    }
)

NON_TELEMETRY_NUMERIC_FIELDS = frozenset(
    {
        "_start",
        "_stop",
        "_time",
        "_value",
        "day",
        "day_of_week",
        "day_of_year",
        "enrichment_status",
        "hour",
        "index",
        "minute",
        "month",
        "quarter",
        "schema_version",
        "second",
        "table",
        "timestamp",
        "unnamed: 0",
        "week",
        "week_of_year",
        "year",
    }
)


def normalize_telemetry_field_name(field: object) -> str:
    return str(field or "").strip().lower()


def is_business_telemetry_field(field: object) -> bool:
    return normalize_telemetry_field_name(field) in BUSINESS_TELEMETRY_FIELDS


def is_phase_diagnostic_field(field: object) -> bool:
    return normalize_telemetry_field_name(field) in DIAGNOSTIC_PHASE_TELEMETRY_FIELDS


def is_rule_selectable_metric(field: object) -> bool:
    normalized = normalize_telemetry_field_name(field)
    return bool(normalized) and normalized not in NON_TELEMETRY_NUMERIC_FIELDS and not is_phase_diagnostic_field(normalized)


def is_analytics_business_feature(field: object) -> bool:
    normalized = normalize_telemetry_field_name(field)
    return bool(normalized) and normalized not in NON_TELEMETRY_NUMERIC_FIELDS and not is_phase_diagnostic_field(normalized)


def filter_rule_selectable_metrics(fields: Iterable[object]) -> list[str]:
    return [str(field) for field in fields if is_rule_selectable_metric(field)]


def filter_analytics_business_features(fields: Iterable[object]) -> list[str]:
    return [str(field) for field in fields if is_analytics_business_feature(field)]
