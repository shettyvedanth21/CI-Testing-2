from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.repositories.tariff_repository import TariffRepository
from src.services.tenant_scope import build_service_tenant_context


@dataclass
class ResolvedTariff:
    rate: float | None
    currency: str
    fetched_at: str
    source: str
    version_id: int | None = None
    effective_start_at: str | None = None
    effective_end_at: str | None = None


async def resolve_tariff(
    session: AsyncSession,
    tenant_id: str | None,
    *,
    effective_at: datetime | None = None,
) -> ResolvedTariff:
    if tenant_id is None:
        raise ValueError("Tenant scope is required to resolve tariff settings")

    tenant_ctx = build_service_tenant_context(tenant_id)
    tenant_repo = TariffRepository(session, tenant_ctx)

    tariff_version = await tenant_repo.get_effective_version(tenant_id, effective_at=effective_at)
    if tariff_version:
        return ResolvedTariff(
            rate=float(tariff_version.energy_rate_per_kwh),
            currency=str(tariff_version.currency or "INR").upper(),
            fetched_at=(tariff_version.created_at or datetime.utcnow()).isoformat(),
            source="tenant_tariff_versions",
            version_id=int(tariff_version.id),
            effective_start_at=tariff_version.effective_start_at.isoformat()
            if tariff_version.effective_start_at
            else None,
            effective_end_at=tariff_version.effective_end_at.isoformat()
            if tariff_version.effective_end_at
            else None,
        )

    tenant_tariff = await tenant_repo.get_tariff(tenant_id)
    if tenant_tariff:
        return ResolvedTariff(
            rate=float(tenant_tariff.energy_rate_per_kwh),
            currency=str(tenant_tariff.currency or "INR").upper(),
            fetched_at=(tenant_tariff.updated_at or datetime.utcnow()).isoformat(),
            source="tenant_tariffs",
            version_id=None,
        )

    return ResolvedTariff(
        rate=None,
        currency="INR",
        fetched_at=datetime.utcnow().isoformat(),
        source="default_unconfigured",
        version_id=None,
    )
