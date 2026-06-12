from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import AsyncSessionLocal
from src.models.tenant_emission_factors import TenantEmissionFactor

logger = logging.getLogger(__name__)

_PLATFORM_DEFAULT_TENANT_ID = "__platform_default__"
_TTL_SECONDS = 300
_CALCULATION_VERSION = "co2_report_v1"


class EmissionFactorCache:
    _value: dict[str | None, dict[str, Any]] = {}
    _expires_at: dict[str | None, datetime] = {}
    _ttl_seconds: int = _TTL_SECONDS

    @classmethod
    async def get(cls, tenant_id: str | None = None) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        expires_at = cls._expires_at.get(tenant_id)
        if expires_at and now < expires_at:
            cached = cls._value.get(tenant_id)
            if cached is not None:
                return {**cached, "cache": "hit"}

        try:
            factor = await cls._read_from_db(tenant_id)
            if factor is not None:
                cls._value[tenant_id] = factor
                cls._expires_at[tenant_id] = now + timedelta(seconds=cls._ttl_seconds)
                return {**factor, "cache": "miss"}
        except Exception as exc:
            logger.warning(
                "emission_factor_db_read_failed",
                extra={"tenant_id": tenant_id, "error": str(exc)},
            )

        stale = cls._value.get(tenant_id)
        if stale is not None:
            return {**stale, "cache": "stale", "stale": True}

        return {
            "configured": False,
            "factor_value": None,
            "factor_unit": "kg_co2_per_kwh",
            "method": None,
            "country": None,
            "region": None,
            "source_name": None,
            "source_version": None,
            "factor_year": None,
            "factor_source": "unconfigured",
            "cache": "empty",
        }

    @classmethod
    async def _read_from_db(cls, tenant_id: str | None) -> Optional[dict[str, Any]]:
        async with AsyncSessionLocal() as session:
            row = None
            if tenant_id is not None:
                result = await session.execute(
                    select(TenantEmissionFactor).where(
                        TenantEmissionFactor.tenant_id == tenant_id,
                        TenantEmissionFactor.is_active.is_(True),
                    )
                )
                row = result.scalar_one_or_none()

            if row is None:
                result = await session.execute(
                    select(TenantEmissionFactor).where(
                        TenantEmissionFactor.tenant_id == _PLATFORM_DEFAULT_TENANT_ID,
                        TenantEmissionFactor.is_active.is_(True),
                    )
                )
                row = result.scalar_one_or_none()

            if row is None:
                return None

            source = "tenant_default"
            if row.tenant_id == _PLATFORM_DEFAULT_TENANT_ID:
                source = "platform_default"

            return {
                "configured": True,
                "factor_value": float(row.factor_value),
                "factor_unit": row.factor_unit or "kg_co2_per_kwh",
                "method": row.method or "location_based",
                "country": row.country or "IN",
                "region": row.region or "all_india_grid",
                "source_name": row.source_name,
                "source_version": row.source_version,
                "factor_year": row.factor_year,
                "factor_source": source,
            }

    @classmethod
    def invalidate(cls, tenant_id: str | None = None) -> None:
        cls._expires_at.pop(tenant_id, None)
        if tenant_id is None:
            cls._value.clear()
            cls._expires_at.clear()


def build_report_co2_overview(
    *,
    total_kwh: float,
    per_device: list[dict[str, Any]],
    overtime_summary: dict[str, Any] | None,
    energy_basis: str,
    factor_payload: dict[str, Any],
) -> dict[str, Any]:
    configured = bool(factor_payload.get("configured"))
    factor_value = factor_payload.get("factor_value")

    if not configured or factor_value is None:
        return {
            "available": False,
            "reason": "emission_factor_not_configured",
            "factor_source": factor_payload.get("factor_source", "unconfigured"),
            "calculation_version": _CALCULATION_VERSION,
        }

    factor_value = float(factor_value)
    if factor_value <= 0:
        return {
            "available": False,
            "reason": "emission_factor_not_configured",
            "factor_source": factor_payload.get("factor_source", "unconfigured"),
            "calculation_version": _CALCULATION_VERSION,
        }

    total_co2_kg = round(total_kwh * factor_value, 4)

    per_device_co2: list[dict[str, Any]] = []
    for device in per_device:
        device_kwh = device.get("total_kwh")
        if isinstance(device_kwh, (int, float)):
            device_co2_kg = round(float(device_kwh) * factor_value, 4)
        else:
            device_co2_kg = None

        overtime = device.get("overtime") or {}
        overtime_kwh = overtime.get("total_overtime_kwh")
        off_shift_co2_kg = (
            round(float(overtime_kwh) * factor_value, 4)
            if isinstance(overtime_kwh, (int, float)) and float(overtime_kwh) > 0
            else None
        )

        per_device_co2.append({
            "device_id": device.get("device_id"),
            "device_name": device.get("device_name"),
            "co2_kg": device_co2_kg,
            "energy_basis_kwh": round(float(device_kwh), 4) if isinstance(device_kwh, (int, float)) else None,
            "energy_basis": device.get("energy_basis", "normalized_telemetry"),
            "off_shift_co2_kg": off_shift_co2_kg,
        })

    off_shift_co2_kg: float | None = None
    off_shift_energy_basis_kwh: float | None = None
    off_shift_available = False
    if overtime_summary is not None:
        overtime_kwh = overtime_summary.get("total_kwh")
        if isinstance(overtime_kwh, (int, float)) and float(overtime_kwh) >= 0:
            off_shift_co2_kg = round(float(overtime_kwh) * factor_value, 4)
            off_shift_energy_basis_kwh = round(float(overtime_kwh), 4)
            off_shift_available = overtime_summary.get("configured_devices", 0) > 0

    co2_overview = {
        "available": True,
        "reason": None,
        "calculation_version": _CALCULATION_VERSION,
        "total_co2_kg": total_co2_kg,
        "total_energy_basis_kwh": round(total_kwh, 4),
        "energy_basis": energy_basis,
        "off_shift_co2_kg": off_shift_co2_kg,
        "off_shift_energy_basis_kwh": off_shift_energy_basis_kwh,
        "off_shift_available": off_shift_available,
        "per_device": per_device_co2,
        "factor": {
            "value": factor_value,
            "unit": factor_payload.get("factor_unit", "kg_co2_per_kwh"),
            "method": factor_payload.get("method", "location_based"),
            "country": factor_payload.get("country", "IN"),
            "region": factor_payload.get("region", "all_india_grid"),
            "source": factor_payload.get("source_name", ""),
            "source_version": factor_payload.get("source_version"),
            "factor_year": factor_payload.get("factor_year"),
        },
        "factor_source": factor_payload.get("factor_source", "unknown"),
    }

    _check_co2_parity(co2_overview)

    return co2_overview


def _check_co2_parity(co2_overview: dict[str, Any]) -> None:
    if not co2_overview.get("available"):
        return

    total_co2 = co2_overview.get("total_co2_kg")
    if total_co2 is None:
        return

    per_device_sum = sum(
        float(d.get("co2_kg") or 0.0)
        for d in co2_overview.get("per_device", [])
        if d.get("co2_kg") is not None
    )

    tolerance = max(0.01, abs(total_co2) * 1e-4)
    drift = abs(per_device_sum - total_co2)

    if drift > tolerance:
        logger.warning(
            "report_co2_parity_drift",
            extra={
                "total_co2_kg": total_co2,
                "per_device_sum_co2_kg": round(per_device_sum, 4),
                "drift_kg": round(drift, 6),
                "tolerance_kg": round(tolerance, 6),
            },
        )
