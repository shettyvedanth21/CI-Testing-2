from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.config import is_production_environment
from src.repositories.tariff_repository import TariffRepository
from src.services.tenant_scope import build_service_tenant_context


def _expected_bootstrap_values() -> dict[str, float | str]:
    return {
        "energy_rate_per_kwh": float(settings.LOCAL_BOOTSTRAP_TARIFF_RATE),
        "demand_charge_per_kw": float(settings.LOCAL_BOOTSTRAP_TARIFF_DEMAND_CHARGE_PER_KW),
        "reactive_penalty_rate": float(settings.LOCAL_BOOTSTRAP_TARIFF_REACTIVE_PENALTY_RATE),
        "fixed_monthly_charge": float(settings.LOCAL_BOOTSTRAP_TARIFF_FIXED_MONTHLY_CHARGE),
        "power_factor_threshold": float(settings.LOCAL_BOOTSTRAP_TARIFF_POWER_FACTOR_THRESHOLD),
        "currency": settings.LOCAL_BOOTSTRAP_TARIFF_CURRENCY,
    }


def _version_values(version: object) -> dict[str, float | str]:
    return {
        "energy_rate_per_kwh": float(version.energy_rate_per_kwh),
        "demand_charge_per_kw": float(version.demand_charge_per_kw),
        "reactive_penalty_rate": float(version.reactive_penalty_rate),
        "fixed_monthly_charge": float(version.fixed_monthly_charge),
        "power_factor_threshold": float(version.power_factor_threshold),
        "currency": str(version.currency),
    }


def validate_local_bootstrap_contract() -> None:
    if settings.LOCAL_BOOTSTRAP_ENABLED and is_production_environment():
        raise RuntimeError("STARTUP BLOCKED: LOCAL_BOOTSTRAP_ENABLED cannot be true in production.")


async def ensure_local_tariff_bootstrap(db: AsyncSession) -> dict[str, bool]:
    validate_local_bootstrap_contract()

    if not settings.LOCAL_BOOTSTRAP_ENABLED:
        return {"tariff_created": False, "tariff_updated": False}

    tenant_id = settings.LOCAL_BOOTSTRAP_TENANT_ID.strip()
    configured_effective_start_at = settings.local_bootstrap_tariff_effective_start_at
    effective_start_at = (
        configured_effective_start_at.replace(tzinfo=None)
        if configured_effective_start_at.tzinfo
        else configured_effective_start_at
    )
    tariff_payload = {
        "tenant_id": tenant_id,
        "energy_rate_per_kwh": settings.LOCAL_BOOTSTRAP_TARIFF_RATE,
        "demand_charge_per_kw": settings.LOCAL_BOOTSTRAP_TARIFF_DEMAND_CHARGE_PER_KW,
        "reactive_penalty_rate": settings.LOCAL_BOOTSTRAP_TARIFF_REACTIVE_PENALTY_RATE,
        "fixed_monthly_charge": settings.LOCAL_BOOTSTRAP_TARIFF_FIXED_MONTHLY_CHARGE,
        "power_factor_threshold": settings.LOCAL_BOOTSTRAP_TARIFF_POWER_FACTOR_THRESHOLD,
        "currency": settings.LOCAL_BOOTSTRAP_TARIFF_CURRENCY,
        "effective_start_at": effective_start_at,
        "change_reason": "local_bootstrap",
        "created_by": "svc:reporting-service",
    }

    repo = TariffRepository(db, build_service_tenant_context(tenant_id))
    existing = await repo.get_tariff(tenant_id)
    existing_versions = await repo.list_versions(tenant_id)
    existing_version_count = len(existing_versions)
    expected_values = _expected_bootstrap_values()

    matching_versions = [
        version
        for version in existing_versions
        if (
            (version.effective_start_at.replace(tzinfo=None) if version.effective_start_at.tzinfo else version.effective_start_at)
            == effective_start_at
        )
    ]
    later_versions = [
        version
        for version in existing_versions
        if (
            (version.effective_start_at.replace(tzinfo=None) if version.effective_start_at.tzinfo else version.effective_start_at)
            > effective_start_at
        )
    ]

    if len(matching_versions) > 1:
        raise RuntimeError("STARTUP BLOCKED: Local tariff bootstrap baseline is duplicated.")

    bootstrap_version = matching_versions[0] if matching_versions else None
    if bootstrap_version is not None:
        if _version_values(bootstrap_version) != expected_values:
            raise RuntimeError(
                "STARTUP BLOCKED: Local tariff bootstrap baseline does not match the configured demo tariff."
            )
    elif later_versions:
        raise RuntimeError(
            "STARTUP BLOCKED: Local tariff bootstrap baseline is missing before later tariff revisions."
        )
    else:
        await repo.upsert_tariff(tenant_id=tenant_id, data=tariff_payload)

    current_versions = await repo.list_versions(tenant_id)
    bootstrap_versions = [
        version
        for version in current_versions
        if (
            (version.effective_start_at.replace(tzinfo=None) if version.effective_start_at.tzinfo else version.effective_start_at)
            == effective_start_at
        )
    ]
    if len(bootstrap_versions) != 1:
        raise RuntimeError("STARTUP BLOCKED: Local tariff bootstrap did not converge to a single deterministic baseline.")

    if _version_values(bootstrap_versions[0]) != expected_values:
        raise RuntimeError(
            "STARTUP BLOCKED: Local tariff bootstrap baseline does not match the configured demo tariff."
        )

    return {
        "tariff_created": existing is None,
        "tariff_updated": existing is not None and len(current_versions) > existing_version_count,
    }
