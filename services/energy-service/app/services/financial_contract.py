from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Literal

from services.shared.telemetry_normalization import (
    INTERVAL_ENERGY_ALGORITHM_VERSION,
    NORMALIZATION_VERSION,
)


CANONICAL_FINANCIAL_CONTRACT_VERSION = "canonical-financial-v1"
CANONICAL_COST_FORMULA_VERSION = "kwh-times-tariff-v1"
CANONICAL_SOURCE = "energy-service.canonical-aggregate"

CostSource = Literal["persisted_aggregate", "tariff_snapshot_derived", "unavailable"]
PeriodType = Literal["device_day", "device_range", "device_month", "fleet_day", "fleet_month"]


@dataclass(frozen=True)
class TariffSnapshot:
    rate_per_kwh: Decimal | None
    currency: str = "INR"
    source: str | None = None
    version_id: str | int | None = None
    fetched_at: str | None = None

    @property
    def configured(self) -> bool:
        return self.rate_per_kwh is not None

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> "TariffSnapshot":
        payload = payload or {}
        rate = payload.get("rate")
        if rate is None:
            rate = payload.get("rate_per_kwh")
        if rate is None:
            rate = payload.get("energy_rate_per_kwh")
        return cls(
            rate_per_kwh=_decimal_or_none(rate),
            currency=str(payload.get("currency") or "INR"),
            source=payload.get("source"),
            version_id=payload.get("version_id"),
            fetched_at=str(payload.get("fetched_at")) if payload.get("fetched_at") is not None else None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "rate_per_kwh": _float_or_none(self.rate_per_kwh),
            "currency": self.currency,
            "source": self.source,
            "version_id": self.version_id,
            "fetched_at": self.fetched_at,
        }


@dataclass(frozen=True)
class CanonicalFinancialTotals:
    energy_kwh: Decimal
    idle_kwh: Decimal = Decimal("0")
    offhours_kwh: Decimal = Decimal("0")
    overconsumption_kwh: Decimal = Decimal("0")
    loss_kwh: Decimal = Decimal("0")
    energy_cost: Decimal | None = None
    loss_cost: Decimal | None = None
    cost_source: CostSource = "unavailable"

    @property
    def bucket_loss_kwh(self) -> Decimal:
        return self.idle_kwh + self.offhours_kwh + self.overconsumption_kwh

    def to_dict(self) -> dict[str, Any]:
        return {
            "energy_kwh": _float(self.energy_kwh, places=6),
            "idle_kwh": _float(self.idle_kwh, places=6),
            "offhours_kwh": _float(self.offhours_kwh, places=6),
            "overconsumption_kwh": _float(self.overconsumption_kwh, places=6),
            "loss_kwh": _float(self.loss_kwh, places=6),
            "energy_cost": _float_or_none(self.energy_cost, places=4),
            "loss_cost": _float_or_none(self.loss_cost, places=4),
            "cost_source": self.cost_source,
        }


@dataclass(frozen=True)
class CanonicalFinancialContract:
    tenant_id: str
    period_type: PeriodType
    period_start: date
    period_end: date
    totals: CanonicalFinancialTotals
    device_id: str | None = None
    timezone_name: str = "Asia/Kolkata"
    tariff: TariffSnapshot = field(default_factory=TariffSnapshot)
    quality_flags: tuple[str, ...] = field(default_factory=tuple)
    calculation_version: str = CANONICAL_FINANCIAL_CONTRACT_VERSION
    source: str = CANONICAL_SOURCE
    algorithm_version: str = INTERVAL_ENERGY_ALGORITHM_VERSION
    normalization_version: str = NORMALIZATION_VERSION
    cost_formula_version: str = CANONICAL_COST_FORMULA_VERSION
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def validate(self) -> tuple[bool, tuple[str, ...]]:
        issues: list[str] = []
        if not self.tenant_id:
            issues.append("tenant_id_required")
        if self.period_end < self.period_start:
            issues.append("period_end_before_start")
        for name in ("energy_kwh", "idle_kwh", "offhours_kwh", "overconsumption_kwh", "loss_kwh"):
            if getattr(self.totals, name) < 0:
                issues.append(f"{name}_negative")
        if self.totals.loss_kwh > self.totals.energy_kwh + Decimal("0.000001"):
            issues.append("loss_exceeds_energy")
        bucket_delta = abs(self.totals.loss_kwh - self.totals.bucket_loss_kwh)
        if bucket_delta > Decimal("0.0001"):
            issues.append("loss_bucket_sum_mismatch")
        if self.totals.energy_cost is not None and self.totals.energy_cost < 0:
            issues.append("energy_cost_negative")
        if self.totals.loss_cost is not None and self.totals.loss_cost < 0:
            issues.append("loss_cost_negative")
        if self.totals.cost_source == "tariff_snapshot_derived" and not self.tariff.configured:
            issues.append("derived_cost_without_tariff")
        if self.totals.cost_source == "unavailable" and (self.totals.energy_cost is not None or self.totals.loss_cost is not None):
            issues.append("unavailable_cost_source_has_cost_values")
        return not issues, tuple(issues)

    def to_dict(self) -> dict[str, Any]:
        ok, issues = self.validate()
        return {
            "contract_version": self.calculation_version,
            "source": self.source,
            "tenant_id": self.tenant_id,
            "device_id": self.device_id,
            "period_type": self.period_type,
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "timezone": self.timezone_name,
            "currency": self.tariff.currency,
            "totals": self.totals.to_dict(),
            "tariff": self.tariff.to_dict(),
            "quality_flags": list(self.quality_flags),
            "algorithm_version": self.algorithm_version,
            "normalization_version": self.normalization_version,
            "cost_formula_version": self.cost_formula_version,
            "generated_at": self.generated_at.isoformat(),
            "warnings": list(self.warnings),
            "valid": ok,
            "validation_issues": list(issues),
        }


def build_canonical_financial_contract(
    *,
    tenant_id: str,
    period_type: PeriodType,
    period_start: date,
    period_end: date,
    energy_kwh: Any,
    idle_kwh: Any = 0,
    offhours_kwh: Any = 0,
    overconsumption_kwh: Any = 0,
    loss_kwh: Any | None = None,
    persisted_energy_cost: Any | None = None,
    persisted_loss_cost: Any | None = None,
    tariff: TariffSnapshot | dict[str, Any] | None = None,
    device_id: str | None = None,
    timezone_name: str = "Asia/Kolkata",
    quality_flags: list[str] | tuple[str, ...] | None = None,
) -> CanonicalFinancialContract:
    tariff_snapshot = tariff if isinstance(tariff, TariffSnapshot) else TariffSnapshot.from_payload(tariff)
    energy = _decimal(energy_kwh)
    idle = _decimal(idle_kwh)
    offhours = _decimal(offhours_kwh)
    over = _decimal(overconsumption_kwh)
    loss = _decimal(loss_kwh) if loss_kwh is not None else idle + offhours + over

    warnings: list[str] = []
    energy_cost = _decimal_or_none(persisted_energy_cost)
    loss_cost = _decimal_or_none(persisted_loss_cost)
    cost_source: CostSource

    if energy_cost is not None or loss_cost is not None:
        cost_source = "persisted_aggregate"
        if energy_cost is None and tariff_snapshot.configured:
            energy_cost = _money(energy * tariff_snapshot.rate_per_kwh)  # type: ignore[operator]
            warnings.append("energy_cost_derived_from_tariff_snapshot")
        if loss_cost is None and tariff_snapshot.configured:
            loss_cost = _money(loss * tariff_snapshot.rate_per_kwh)  # type: ignore[operator]
            warnings.append("loss_cost_derived_from_tariff_snapshot")
    elif tariff_snapshot.configured:
        cost_source = "tariff_snapshot_derived"
        energy_cost = _money(energy * tariff_snapshot.rate_per_kwh)  # type: ignore[operator]
        loss_cost = _money(loss * tariff_snapshot.rate_per_kwh)  # type: ignore[operator]
    else:
        cost_source = "unavailable"
        warnings.append("tariff_snapshot_unavailable")

    return CanonicalFinancialContract(
        tenant_id=tenant_id,
        device_id=device_id,
        period_type=period_type,
        period_start=period_start,
        period_end=period_end,
        timezone_name=timezone_name,
        tariff=tariff_snapshot,
        totals=CanonicalFinancialTotals(
            energy_kwh=_kwh(energy),
            idle_kwh=_kwh(idle),
            offhours_kwh=_kwh(offhours),
            overconsumption_kwh=_kwh(over),
            loss_kwh=_kwh(loss),
            energy_cost=energy_cost,
            loss_cost=loss_cost,
            cost_source=cost_source,
        ),
        quality_flags=tuple(str(flag) for flag in (quality_flags or ())),
        warnings=tuple(warnings),
    )


def _decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def _kwh(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _float(value: Decimal, *, places: int) -> float:
    quant = Decimal("1").scaleb(-places)
    return float(value.quantize(quant, rounding=ROUND_HALF_UP))


def _float_or_none(value: Decimal | None, *, places: int = 6) -> float | None:
    if value is None:
        return None
    return _float(value, places=places)
