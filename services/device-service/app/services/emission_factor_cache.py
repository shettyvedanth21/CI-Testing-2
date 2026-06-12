"""In-memory emission factor cache for Scope 2 CO2 calculations.

Reads from the local device-service MySQL tenant_emission_factors table.
No cross-service calls. Falls back to __platform_default__ row if no
tenant-specific row exists.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models.device import TenantEmissionFactor

logger = logging.getLogger(__name__)

_PLATFORM_DEFAULT_TENANT_ID = "__platform_default__"
_TTL_SECONDS = 300
_CALCULATION_VERSION = "co2_scope2_v1"


class EmissionFactorCache:
    """Class-method based emission factor cache with TTL.

    Follows the same pattern as TariffCache in idle_running.py but
    reads from local MySQL instead of making an HTTP call.
    """

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


def build_co2_overview(
    *,
    tenant_id: str | None,
    today_energy_kwh: float,
    today_loss_kwh: float,
    today_loss_available: bool,
    month_energy_kwh: float,
    factor_payload: dict[str, Any],
) -> dict[str, Any]:
    """Build the co2_overview payload from energy/loss values and cached factor.

    This is a pure computation function with no I/O.
    """
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

    today_co2_kg = round(today_energy_kwh * factor_value, 4)
    today_avoidable_co2_kg: Optional[float] = None
    today_avoidable_co2_available = False
    today_avoidable_co2_reason: Optional[str] = None

    if today_loss_available:
        today_avoidable_co2_kg = round(today_loss_kwh * factor_value, 4)
        today_avoidable_co2_available = True
    else:
        today_avoidable_co2_reason = "loss_data_not_current_day"

    month_co2_kg = round(month_energy_kwh * factor_value, 4)

    return {
        "available": True,
        "today": {
            "energy_kwh": round(today_energy_kwh, 4),
            "co2_kg": today_co2_kg,
            "loss_kwh": round(today_loss_kwh, 4),
            "avoidable_co2_kg": today_avoidable_co2_kg,
            "available": True,
            "avoidable_co2_available": today_avoidable_co2_available,
            "avoidable_co2_reason": today_avoidable_co2_reason,
        },
        "week": {
            "available": False,
            "reason": "weekly_projection_not_supported",
        },
        "month": {
            "energy_kwh": round(month_energy_kwh, 4),
            "co2_kg": month_co2_kg,
            "available": True,
            "avoidable_co2_available": False,
            "avoidable_co2_reason": "monthly_loss_projection_not_supported",
        },
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
        "calculation_version": _CALCULATION_VERSION,
    }
