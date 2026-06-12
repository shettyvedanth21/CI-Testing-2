"""Shared threshold-rule property contract for create/update/evaluate flows."""

from __future__ import annotations

from typing import Mapping


_CANONICAL_PROPERTY_ALIASES: dict[str, tuple[str, ...]] = {
    "power": ("power", "active_power"),
    "power_kw": ("power_kw", "active_power_kw"),
    "current": ("current",),
    "voltage": ("voltage",),
    "temperature": ("temperature",),
    "energy": ("energy", "energy_kwh", "kwh"),
    "frequency": ("frequency",),
    "power_factor": ("power_factor", "powerfactor", "pf", "cos_phi"),
    "reactive_power": ("reactive_power", "kvar"),
    "apparent_power": ("apparent_power", "kva"),
    "run_hours": ("run_hours",),
    "thd": ("thd",),
}

_ALIASES_TO_CANONICAL: dict[str, str] = {
    alias: canonical
    for canonical, aliases in _CANONICAL_PROPERTY_ALIASES.items()
    for alias in aliases
}


def normalize_threshold_property_name(value: str) -> str:
    normalized = str(value or "").strip().lower()
    canonical = _ALIASES_TO_CANONICAL.get(normalized)
    if canonical is None:
        supported = ", ".join(sorted(_CANONICAL_PROPERTY_ALIASES))
        raise ValueError(
            f"Unsupported threshold property '{normalized or value}'. "
            f"Supported properties: {supported}"
        )
    return canonical


def resolve_threshold_property_value(
    dynamic_fields: Mapping[str, float],
    property_name: str,
) -> float:
    canonical = normalize_threshold_property_name(property_name)
    normalized_dynamic_fields = {
        str(key).strip().lower(): float(value)
        for key, value in dynamic_fields.items()
        if isinstance(value, (int, float))
    }
    for candidate in _CANONICAL_PROPERTY_ALIASES[canonical]:
        if candidate in normalized_dynamic_fields:
            return normalized_dynamic_fields[candidate]
    raise ValueError(f"Telemetry payload does not include property: {canonical}")


def supported_threshold_properties() -> tuple[str, ...]:
    return tuple(sorted(_CANONICAL_PROPERTY_ALIASES))
