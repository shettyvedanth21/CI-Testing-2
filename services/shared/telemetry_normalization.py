from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

try:
    from services.shared.telemetry_contract import is_phase_diagnostic_field
except ModuleNotFoundError:  # pragma: no cover - service-local test path fallback
    from shared.telemetry_contract import is_phase_diagnostic_field  # type: ignore

NORMALIZATION_VERSION = "signed-power-v1"
INTERVAL_ENERGY_ALGORITHM_VERSION = "canonical-interval-v1"
DEFAULT_ENERGY_FLOW_MODE = "consumption_only"
DEFAULT_POLARITY_MODE = "normal"
ACTIVE_POWER_CONFLICT_TOLERANCE_W = 1.0
DEFAULT_FALLBACK_PF = 0.85
DEFAULT_COUNTER_RELATIVE_TOLERANCE = 0.35
DEFAULT_COUNTER_ABSOLUTE_TOLERANCE_KWH = 0.05
DEFAULT_HARD_MAX_POWER_KW = 10_000.0
DEFAULT_MAX_COUNTER_GAP_SECONDS = 120.0
DEFAULT_MAX_FALLBACK_GAP_SECONDS = 120.0
DEFAULT_COUNTER_NOISE_FLOOR_KWH = 0.001
DEFAULT_COUNTER_RESET_NEAR_ZERO_KWH = 0.25

UTC = ZoneInfo("UTC")


@dataclass(frozen=True)
class DevicePowerConfig:
    energy_flow_mode: str = DEFAULT_ENERGY_FLOW_MODE
    polarity_mode: str = DEFAULT_POLARITY_MODE


@dataclass(frozen=True)
class NormalizedTelemetrySample:
    timestamp: datetime
    raw_power_w: Optional[float]
    raw_active_power_w: Optional[float]
    raw_power_factor: Optional[float]
    raw_current_a: Optional[float]
    raw_voltage_v: Optional[float]
    raw_energy_kwh: Optional[float]
    raw_source_power_field: Optional[str]
    raw_source_pf_field: Optional[str]
    raw_source_energy_field: Optional[str]
    net_power_w: Optional[float]
    import_power_w: float
    export_power_w: float
    business_power_w: float
    pf_signed: Optional[float]
    pf_business: Optional[float]
    current_a: Optional[float]
    voltage_v: Optional[float]
    energy_counter_kwh: Optional[float]
    power_direction: str
    quality_flags: tuple[str, ...]
    normalization_version: str = NORMALIZATION_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "raw_power_w": self.raw_power_w,
            "raw_active_power_w": self.raw_active_power_w,
            "raw_power_factor": self.raw_power_factor,
            "raw_current_a": self.raw_current_a,
            "raw_voltage_v": self.raw_voltage_v,
            "raw_energy_kwh": self.raw_energy_kwh,
            "raw_source_power_field": self.raw_source_power_field,
            "raw_source_pf_field": self.raw_source_pf_field,
            "raw_source_energy_field": self.raw_source_energy_field,
            "net_power_w": self.net_power_w,
            "import_power_w": self.import_power_w,
            "export_power_w": self.export_power_w,
            "business_power_w": self.business_power_w,
            "pf_signed": self.pf_signed,
            "pf_business": self.pf_business,
            "current_a": self.current_a,
            "voltage_v": self.voltage_v,
            "energy_counter_kwh": self.energy_counter_kwh,
            "power_direction": self.power_direction,
            "quality_flags": list(self.quality_flags),
            "normalization_version": self.normalization_version,
        }


@dataclass(frozen=True)
class NormalizedIntervalEnergy:
    business_energy_delta_kwh: float
    import_energy_delta_kwh: float
    export_energy_delta_kwh: float
    counter_delta_kwh: Optional[float]
    energy_delta_method: str
    quality_flags: tuple[str, ...]
    quality_class: str = "unbillable"
    reason_code: str = "insufficient_inputs"
    elapsed_seconds: float = 0.0
    fallback_delta_kwh: Optional[float] = None
    implied_avg_kw: Optional[float] = None
    comparison_power_kw: Optional[float] = None
    coverage_seconds: float = 0.0
    algorithm_version: str = INTERVAL_ENERGY_ALGORITHM_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "business_energy_delta_kwh": self.business_energy_delta_kwh,
            "import_energy_delta_kwh": self.import_energy_delta_kwh,
            "export_energy_delta_kwh": self.export_energy_delta_kwh,
            "counter_delta_kwh": self.counter_delta_kwh,
            "energy_delta_method": self.energy_delta_method,
            "quality_flags": list(self.quality_flags),
            "quality_class": self.quality_class,
            "reason_code": self.reason_code,
            "elapsed_seconds": self.elapsed_seconds,
            "fallback_delta_kwh": self.fallback_delta_kwh,
            "implied_avg_kw": self.implied_avg_kw,
            "comparison_power_kw": self.comparison_power_kw,
            "coverage_seconds": self.coverage_seconds,
            "algorithm_version": self.algorithm_version,
        }


def _measured_business_power_w(sample: NormalizedTelemetrySample) -> Optional[float]:
    if sample.raw_active_power_w is None and sample.raw_power_w is None:
        return None
    return max(float(sample.business_power_w or 0.0), 0.0)


def _integrate_power_delta(
    previous_w: float,
    current_w: float,
    dt_hours: float,
) -> float:
    return max((max(previous_w, 0.0) + max(current_w, 0.0)) / 2.0, 0.0) * dt_hours / 1000.0


def _build_interval_energy(
    *,
    business_kwh: float,
    export_kwh: float,
    counter_delta_kwh: Optional[float],
    fallback_delta_kwh: Optional[float],
    energy_delta_method: str,
    quality_flags: set[str],
    quality_class: str,
    reason_code: str,
    elapsed_seconds: float,
    implied_avg_kw: Optional[float],
    comparison_power_kw: Optional[float],
    coverage_seconds: float,
) -> NormalizedIntervalEnergy:
    return NormalizedIntervalEnergy(
        business_energy_delta_kwh=business_kwh,
        import_energy_delta_kwh=business_kwh,
        export_energy_delta_kwh=export_kwh,
        counter_delta_kwh=counter_delta_kwh,
        energy_delta_method=energy_delta_method,
        quality_flags=tuple(sorted(quality_flags)),
        quality_class=quality_class,
        reason_code=reason_code,
        elapsed_seconds=elapsed_seconds,
        fallback_delta_kwh=fallback_delta_kwh,
        implied_avg_kw=implied_avg_kw,
        comparison_power_kw=comparison_power_kw,
        coverage_seconds=coverage_seconds,
    )


def effective_business_power_w(
    sample: NormalizedTelemetrySample,
    *,
    fallback_pf: float = DEFAULT_FALLBACK_PF,
) -> float:
    """Return the canonical business power sample used for business KPIs.

    Explicit active-power aliases remain authoritative. When active power is not
    present, derive a business-compatible import power from normalized current,
    voltage, and business PF so KPI series stay aligned with interval-energy
    fallback behavior.
    """

    if sample.raw_source_power_field is not None:
        return max(float(sample.business_power_w or 0.0), 0.0)

    if sample.current_a is None or sample.voltage_v is None:
        return max(float(sample.business_power_w or 0.0), 0.0)

    pf = sample.pf_business if sample.pf_business is not None else fallback_pf
    return max(float(sample.current_a) * float(sample.voltage_v) * float(pf), 0.0)


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def parse_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def build_device_power_config(source: Any) -> DevicePowerConfig:
    if isinstance(source, DevicePowerConfig):
        return source
    if isinstance(source, dict):
        energy_flow_mode = str(
            source.get("energy_flow_mode") or DEFAULT_ENERGY_FLOW_MODE
        ).strip() or DEFAULT_ENERGY_FLOW_MODE
        polarity_mode = str(
            source.get("polarity_mode") or DEFAULT_POLARITY_MODE
        ).strip() or DEFAULT_POLARITY_MODE
        return DevicePowerConfig(
            energy_flow_mode=energy_flow_mode,
            polarity_mode=polarity_mode,
        )
    return DevicePowerConfig(
        energy_flow_mode=str(getattr(source, "energy_flow_mode", DEFAULT_ENERGY_FLOW_MODE) or DEFAULT_ENERGY_FLOW_MODE),
        polarity_mode=str(getattr(source, "polarity_mode", DEFAULT_POLARITY_MODE) or DEFAULT_POLARITY_MODE),
    )


def _resolve_active_power_w(payload: dict[str, Any]) -> tuple[Optional[float], Optional[str], list[str]]:
    flags: list[str] = []
    resolved_value: Optional[float] = None
    resolved_field: Optional[str] = None
    candidates: list[tuple[str, float]] = []
    precedence = ("active_power_kw", "power_kw", "active_power", "power")
    for field in precedence:
        raw_value = _safe_float(payload.get(field))
        if raw_value is None:
            continue
        watts = raw_value * 1000.0 if field.endswith("_kw") or field == "power_kw" else raw_value
        candidates.append((field, watts))
        if resolved_field is None:
            resolved_field = field
            resolved_value = watts

    if len(candidates) > 1:
        flags.append("active_power_alias_used")
        baseline = candidates[0][1]
        if any(abs(value - baseline) > ACTIVE_POWER_CONFLICT_TOLERANCE_W for _, value in candidates[1:]):
            flags.append("active_power_conflict")

    raw_power_w = _safe_float(payload.get("power"))
    return resolved_value if resolved_value is not None else raw_power_w, resolved_field, flags


def _resolve_pf(payload: dict[str, Any]) -> tuple[Optional[float], Optional[str]]:
    for field in ("power_factor", "pf", "cos_phi", "powerfactor"):
        value = _safe_float(payload.get(field))
        if value is not None:
            return value, field
    return None, None


def _resolve_current(payload: dict[str, Any]) -> Optional[float]:
    for field in ("current", "phase_current"):
        value = _safe_float(payload.get(field))
        if value is not None:
            return value
    return None


def _resolve_voltage(payload: dict[str, Any]) -> Optional[float]:
    for field in ("voltage",):
        value = _safe_float(payload.get(field))
        if value is not None:
            return value
    return None


def _resolve_energy_counter(payload: dict[str, Any]) -> tuple[Optional[float], Optional[str]]:
    for field in ("energy_kwh", "kwh", "energy"):
        value = _safe_float(payload.get(field))
        if value is not None:
            return value, field
    return None, None


def normalize_telemetry_sample(
    payload: dict[str, Any],
    config_source: Any,
) -> NormalizedTelemetrySample:
    config = build_device_power_config(config_source)
    flags: list[str] = []

    timestamp = parse_timestamp(payload.get("timestamp") or datetime.now(UTC).isoformat())
    raw_power_w = _safe_float(payload.get("power"))
    raw_active_power_w, raw_source_power_field, alias_flags = _resolve_active_power_w(payload)
    flags.extend(alias_flags)

    raw_power_factor, raw_source_pf_field = _resolve_pf(payload)
    raw_current_a = _resolve_current(payload)
    raw_voltage_v = _resolve_voltage(payload)
    raw_energy_kwh, raw_source_energy_field = _resolve_energy_counter(payload)

    if any(is_phase_diagnostic_field(field) and _safe_float(value) is not None for field, value in payload.items()):
        flags.append("phase_diagnostics_present")

    if raw_active_power_w is None and raw_power_w is None:
        flags.append("power_missing")

    polarity_sign = -1.0 if config.polarity_mode == "inverted" else 1.0
    if config.polarity_mode == "inverted":
        flags.append("polarity_inverted_applied")

    net_power_w = None if raw_active_power_w is None else (polarity_sign * raw_active_power_w)
    if net_power_w is not None and net_power_w < 0:
        flags.append("signed_power_seen")

    import_power_w = max(net_power_w or 0.0, 0.0)
    export_power_w = max(-(net_power_w or 0.0), 0.0) if config.energy_flow_mode == "bidirectional" else 0.0
    if export_power_w > 0:
        flags.append("export_seen")

    business_power_w = import_power_w
    power_direction = "unknown"
    if net_power_w is not None:
        if net_power_w > 0:
            power_direction = "import"
        elif net_power_w < 0:
            power_direction = "export"
        else:
            power_direction = "zero"

    pf_signed = None if raw_power_factor is None else (polarity_sign * raw_power_factor)
    if pf_signed is not None and pf_signed < 0:
        flags.append("signed_pf_seen")
    pf_business = None
    if pf_signed is not None:
        magnitude = abs(pf_signed)
        if 0 < magnitude <= 1:
            pf_business = magnitude
        else:
            flags.append("pf_untrusted")

    current_a = abs(raw_current_a) if raw_current_a is not None else None
    voltage_v = abs(raw_voltage_v) if raw_voltage_v is not None else None
    if raw_current_a is not None and raw_current_a < 0:
        flags.append("raw_current_negative_seen")
    if raw_voltage_v is not None and raw_voltage_v < 0:
        flags.append("raw_voltage_negative_seen")

    return NormalizedTelemetrySample(
        timestamp=timestamp,
        raw_power_w=raw_power_w,
        raw_active_power_w=raw_active_power_w,
        raw_power_factor=raw_power_factor,
        raw_current_a=raw_current_a,
        raw_voltage_v=raw_voltage_v,
        raw_energy_kwh=raw_energy_kwh,
        raw_source_power_field=raw_source_power_field,
        raw_source_pf_field=raw_source_pf_field,
        raw_source_energy_field=raw_source_energy_field,
        net_power_w=net_power_w,
        import_power_w=import_power_w,
        export_power_w=export_power_w,
        business_power_w=business_power_w,
        pf_signed=pf_signed,
        pf_business=pf_business,
        current_a=current_a,
        voltage_v=voltage_v,
        energy_counter_kwh=raw_energy_kwh,
        power_direction=power_direction,
        quality_flags=tuple(sorted(set(flags))),
    )


def compute_interval_energy_delta(
    previous: Optional[NormalizedTelemetrySample],
    current: NormalizedTelemetrySample,
    *,
    max_fallback_gap_seconds: float = DEFAULT_MAX_FALLBACK_GAP_SECONDS,
    fallback_pf: float = DEFAULT_FALLBACK_PF,
    max_counter_gap_seconds: Optional[float] = None,
    counter_relative_tolerance: float = DEFAULT_COUNTER_RELATIVE_TOLERANCE,
    counter_absolute_tolerance_kwh: float = DEFAULT_COUNTER_ABSOLUTE_TOLERANCE_KWH,
    hard_max_kw: float = DEFAULT_HARD_MAX_POWER_KW,
    counter_noise_floor_kwh: float = DEFAULT_COUNTER_NOISE_FLOOR_KWH,
    counter_reset_near_zero_kwh: float = DEFAULT_COUNTER_RESET_NEAR_ZERO_KWH,
    allow_assumed_pf_fallback: bool = False,
) -> NormalizedIntervalEnergy:
    flags = set(current.quality_flags)
    if previous is None:
        flags.add("first_sample")
        return _build_interval_energy(
            business_kwh=0.0,
            export_kwh=0.0,
            counter_delta_kwh=None,
            fallback_delta_kwh=None,
            energy_delta_method="none",
            quality_flags=flags,
            quality_class="invalid",
            reason_code="first_sample",
            elapsed_seconds=0.0,
            implied_avg_kw=None,
            comparison_power_kw=None,
            coverage_seconds=0.0,
        )

    dt_sec = (current.timestamp - previous.timestamp).total_seconds()
    effective_max_counter_gap_seconds = (
        max_counter_gap_seconds
        if max_counter_gap_seconds is not None
        else max(max_fallback_gap_seconds, DEFAULT_MAX_COUNTER_GAP_SECONDS)
    )
    if dt_sec <= 0:
        flags.add("duplicate_or_out_of_order")
        return _build_interval_energy(
            business_kwh=0.0,
            export_kwh=0.0,
            counter_delta_kwh=None,
            fallback_delta_kwh=None,
            energy_delta_method="none",
            quality_flags=flags,
            quality_class="invalid",
            reason_code="non_positive_elapsed",
            elapsed_seconds=dt_sec,
            implied_avg_kw=None,
            comparison_power_kw=None,
            coverage_seconds=0.0,
        )

    dt_hours = dt_sec / 3600.0

    measured_previous_w = _measured_business_power_w(previous)
    measured_current_w = _measured_business_power_w(current)
    measured_fallback_delta_kwh: Optional[float] = None
    measured_comparison_power_kw: Optional[float] = None
    measured_export_kwh = 0.0
    if measured_previous_w is not None and measured_current_w is not None:
        measured_fallback_delta_kwh = _integrate_power_delta(measured_previous_w, measured_current_w, dt_hours)
        measured_export_kwh = _integrate_power_delta(previous.export_power_w, current.export_power_w, dt_hours)
        measured_comparison_power_kw = max((measured_previous_w + measured_current_w) / 2.0 / 1000.0, 0.0)

    vi_fallback_delta_kwh: Optional[float] = None
    vi_comparison_power_kw: Optional[float] = None
    vi_method = "derived_vi_pf"
    vi_flags: set[str] = set()
    if (
        current.current_a is not None
        and current.voltage_v is not None
        and previous.current_a is not None
        and previous.voltage_v is not None
    ):
        current_pf = current.pf_business
        previous_pf = previous.pf_business
        if current_pf is not None and previous_pf is not None:
            current_vi_w = current.current_a * current.voltage_v * current_pf
            previous_vi_w = previous.current_a * previous.voltage_v * previous_pf
            vi_fallback_delta_kwh = _integrate_power_delta(previous_vi_w, current_vi_w, dt_hours)
            vi_comparison_power_kw = max((previous_vi_w + current_vi_w) / 2.0 / 1000.0, 0.0)
            vi_flags.add("power_derived_from_vi_pf")
        elif allow_assumed_pf_fallback:
            current_vi_w = current.current_a * current.voltage_v * fallback_pf
            previous_vi_w = previous.current_a * previous.voltage_v * fallback_pf
            vi_fallback_delta_kwh = _integrate_power_delta(previous_vi_w, current_vi_w, dt_hours)
            vi_comparison_power_kw = max((previous_vi_w + current_vi_w) / 2.0 / 1000.0, 0.0)
            vi_method = "derived_vi_assumed_pf"
            vi_flags.update({"power_derived_from_vi_pf", "pf_untrusted"})

    fallback_delta_kwh = measured_fallback_delta_kwh
    fallback_export_kwh = measured_export_kwh
    fallback_method = "power_integration"
    fallback_reason_code = "fallback_measured_power"
    fallback_flags: set[str] = {"fallback_integration"}
    comparison_power_kw = measured_comparison_power_kw

    if fallback_delta_kwh is None and vi_fallback_delta_kwh is not None:
        fallback_delta_kwh = vi_fallback_delta_kwh
        fallback_export_kwh = 0.0
        fallback_method = vi_method
        fallback_reason_code = "fallback_vipf" if vi_method == "derived_vi_pf" else "fallback_assumed_pf"
        fallback_flags = set(vi_flags)
        comparison_power_kw = vi_comparison_power_kw

    counter_delta: Optional[float] = None
    if current.energy_counter_kwh is not None and previous.energy_counter_kwh is not None:
        counter_delta = current.energy_counter_kwh - previous.energy_counter_kwh
        if abs(counter_delta) <= counter_noise_floor_kwh:
            flags.add("counter_noise_floor_applied")
            if fallback_delta_kwh is not None and fallback_delta_kwh > 0:
                flags.update(fallback_flags)
                if fallback_method == "derived_vi_pf" and (
                    current.pf_business is None or previous.pf_business is None
                ):
                    flags.add("pf_untrusted")
                return _build_interval_energy(
                    business_kwh=fallback_delta_kwh,
                    export_kwh=fallback_export_kwh,
                    counter_delta_kwh=0.0,
                    fallback_delta_kwh=fallback_delta_kwh,
                    energy_delta_method=fallback_method,
                    quality_flags=flags,
                    quality_class="estimated",
                    reason_code=fallback_reason_code,
                    elapsed_seconds=dt_sec,
                    implied_avg_kw=0.0,
                    comparison_power_kw=comparison_power_kw,
                    coverage_seconds=dt_sec,
                )
            return _build_interval_energy(
                business_kwh=0.0,
                export_kwh=0.0,
                counter_delta_kwh=0.0,
                fallback_delta_kwh=fallback_delta_kwh,
                energy_delta_method="counter",
                quality_flags=flags,
                quality_class="billing_grade",
                reason_code="counter_accepted",
                elapsed_seconds=dt_sec,
                implied_avg_kw=0.0,
                comparison_power_kw=comparison_power_kw,
                coverage_seconds=dt_sec,
            )
        if counter_delta < 0:
            if (
                previous.energy_counter_kwh is not None
                and previous.energy_counter_kwh > counter_reset_near_zero_kwh
                and current.energy_counter_kwh is not None
                and current.energy_counter_kwh <= counter_reset_near_zero_kwh
            ):
                flags.add("counter_reset_detected")
            else:
                flags.add("counter_reverse_seen")
            counter_delta = None
        elif dt_sec > effective_max_counter_gap_seconds:
            flags.add("counter_gap_exceeded")
        else:
            implied_avg_kw = counter_delta / dt_hours
            if implied_avg_kw > hard_max_kw:
                flags.add("counter_implausible_hard_max")
            else:
                tolerance_kwh: Optional[float] = None
                if fallback_delta_kwh is not None:
                    tolerance_kwh = max(
                        counter_absolute_tolerance_kwh,
                        abs(fallback_delta_kwh) * counter_relative_tolerance,
                    )
                if fallback_delta_kwh is not None and abs(counter_delta - fallback_delta_kwh) > tolerance_kwh:
                    flags.add("counter_implausible_vs_power")
                else:
                    quality_class = "billing_grade" if fallback_delta_kwh is not None else "counter_only"
                    return _build_interval_energy(
                        business_kwh=counter_delta,
                        export_kwh=0.0,
                        counter_delta_kwh=counter_delta,
                        fallback_delta_kwh=fallback_delta_kwh,
                        energy_delta_method="counter",
                        quality_flags=flags,
                        quality_class=quality_class,
                        reason_code="counter_accepted",
                        elapsed_seconds=dt_sec,
                        implied_avg_kw=implied_avg_kw,
                        comparison_power_kw=comparison_power_kw,
                        coverage_seconds=dt_sec,
                    )
    else:
        flags.add("counter_missing")

    if dt_sec > max_fallback_gap_seconds:
        flags.add("long_gap_fallback_blocked")
        reason_code = "fallback_gap_exceeded"
        if "counter_reset_detected" in flags:
            reason_code = "counter_reset_detected"
        elif "counter_reverse_seen" in flags:
            reason_code = "counter_negative"
        elif "counter_implausible_hard_max" in flags:
            reason_code = "counter_implausible_vs_hard_max"
        elif "counter_implausible_vs_power" in flags:
            reason_code = "counter_implausible_vs_power"
        return _build_interval_energy(
            business_kwh=0.0,
            export_kwh=0.0,
            counter_delta_kwh=counter_delta,
            fallback_delta_kwh=fallback_delta_kwh,
            energy_delta_method="none",
            quality_flags=flags,
            quality_class="gap_exceeded",
            reason_code=reason_code,
            elapsed_seconds=dt_sec,
            implied_avg_kw=(counter_delta / dt_hours) if counter_delta is not None else None,
            comparison_power_kw=comparison_power_kw,
            coverage_seconds=0.0,
        )

    if fallback_delta_kwh is not None:
        flags.update(fallback_flags)
        if fallback_method == "derived_vi_pf" and (
            current.pf_business is None or previous.pf_business is None
        ):
            flags.add("pf_untrusted")
        return _build_interval_energy(
            business_kwh=fallback_delta_kwh,
            export_kwh=fallback_export_kwh,
            counter_delta_kwh=counter_delta,
            fallback_delta_kwh=fallback_delta_kwh,
            energy_delta_method=fallback_method,
            quality_flags=flags,
            quality_class="estimated",
            reason_code=fallback_reason_code,
            elapsed_seconds=dt_sec,
            implied_avg_kw=(counter_delta / dt_hours) if counter_delta is not None else None,
            comparison_power_kw=comparison_power_kw,
            coverage_seconds=dt_sec,
        )

    flags.add("insufficient_power_for_fallback")
    reason_code = "insufficient_inputs"
    if "counter_reset_detected" in flags:
        reason_code = "counter_reset_detected"
    elif "counter_reverse_seen" in flags:
        reason_code = "counter_negative"
    elif "counter_implausible_hard_max" in flags:
        reason_code = "counter_implausible_vs_hard_max"
    elif "counter_implausible_vs_power" in flags:
        reason_code = "counter_implausible_vs_power"
    elif "counter_gap_exceeded" in flags:
        reason_code = "counter_gap_exceeded"
    return _build_interval_energy(
        business_kwh=0.0,
        export_kwh=0.0,
        counter_delta_kwh=counter_delta,
        fallback_delta_kwh=fallback_delta_kwh,
        energy_delta_method="none",
        quality_flags=flags,
        quality_class="unbillable",
        reason_code=reason_code,
        elapsed_seconds=dt_sec,
        implied_avg_kw=(counter_delta / dt_hours) if counter_delta is not None else None,
        comparison_power_kw=comparison_power_kw,
        coverage_seconds=0.0,
    )
