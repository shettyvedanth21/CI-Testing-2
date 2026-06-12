from datetime import datetime
from typing import Optional
from sqlalchemy import or_, select

from src.models import TenantTariff, TenantTariffVersion
from services.shared.scoped_repository import TenantScopedRepository
from services.shared.tenant_context import TenantContext


class TariffRepository(TenantScopedRepository[TenantTariff]):
    model = TenantTariff

    def __init__(self, db, ctx: TenantContext | None = None, allow_cross_tenant: bool = False):
        effective_ctx = ctx or TenantContext.system("svc:reporting-service")
        super().__init__(db, effective_ctx, allow_cross_tenant=allow_cross_tenant or ctx is None)
        self.db = db

    def _effective_tenant_id(self, tenant_id: str | None = None) -> str | None:
        if tenant_id is not None:
            tenant_id = tenant_id.strip() or None
        else:
            tenant_id = self._tenant_id
        return tenant_id

    def _scope_select(self, statement, tenant_id: str | None = None):
        effective_tenant_id = self._effective_tenant_id(tenant_id)
        if effective_tenant_id is not None and self._has_tenant_column():
            statement = statement.where(getattr(self.model, "tenant_id") == effective_tenant_id)
        return statement
    
    async def get_tariff(self, tenant_id: str | None = None, *_: object, **__: object) -> Optional[TenantTariff]:
        result = await self.db.execute(self._scope_select(select(TenantTariff), tenant_id=tenant_id))
        return result.scalar_one_or_none()

    async def list_versions(self, tenant_id: str | None = None) -> list[TenantTariffVersion]:
        effective_tenant_id = self._effective_tenant_id(tenant_id)
        if effective_tenant_id is None:
            return []
        result = await self.db.execute(
            select(TenantTariffVersion)
            .where(TenantTariffVersion.tenant_id == effective_tenant_id)
            .order_by(
                TenantTariffVersion.version_number.asc(),
                TenantTariffVersion.created_at.asc(),
            )
        )
        return list(result.scalars().all())

    async def get_version_by_id(
        self,
        version_id: int,
        tenant_id: str | None = None,
    ) -> Optional[TenantTariffVersion]:
        effective_tenant_id = self._effective_tenant_id(tenant_id)
        if effective_tenant_id is None:
            return None

        result = await self.db.execute(
            select(TenantTariffVersion)
            .where(TenantTariffVersion.id == int(version_id))
            .where(TenantTariffVersion.tenant_id == effective_tenant_id)
        )
        return result.scalar_one_or_none()

    async def get_effective_version(
        self,
        tenant_id: str | None = None,
        effective_at: datetime | None = None,
    ) -> Optional[TenantTariffVersion]:
        effective_tenant_id = self._effective_tenant_id(tenant_id)
        if effective_tenant_id is None:
            return None

        at = effective_at or datetime.utcnow()
        statement = (
            select(TenantTariffVersion)
            .where(TenantTariffVersion.tenant_id == effective_tenant_id)
            .where(TenantTariffVersion.effective_start_at <= at)
            .where(
                or_(
                    TenantTariffVersion.effective_end_at.is_(None),
                    TenantTariffVersion.effective_end_at > at,
                )
            )
            .order_by(TenantTariffVersion.effective_start_at.desc(), TenantTariffVersion.version_number.desc())
        )
        result = await self.db.execute(statement)
        return result.scalars().first()

    async def create_tariff_version(
        self,
        *,
        tenant_id: str,
        data: dict,
        effective_start_at: datetime | None = None,
        change_reason: str | None = None,
        created_by: str | None = None,
    ) -> TenantTariffVersion:
        effective_tenant_id = self._effective_tenant_id(tenant_id)
        if effective_tenant_id is None:
            raise ValueError("Tenant scope is required to create a tariff version")

        start_at = effective_start_at or datetime.utcnow()
        versions = await self.list_versions(effective_tenant_id)
        latest = versions[-1] if versions else None
        if latest is not None and start_at < latest.effective_start_at:
            raise ValueError("effective_start_at cannot precede the latest tariff version start")
        if latest is not None and latest.effective_end_at is not None and start_at < latest.effective_end_at:
            raise ValueError("effective_start_at overlaps an existing closed tariff version window")

        version = TenantTariffVersion(
            tenant_id=effective_tenant_id,
            version_number=(int(latest.version_number) + 1) if latest is not None else 1,
            effective_start_at=start_at,
            effective_end_at=None,
            energy_rate_per_kwh=float(data.get("energy_rate_per_kwh", 0)),
            demand_charge_per_kw=float(data.get("demand_charge_per_kw", 0)),
            reactive_penalty_rate=float(data.get("reactive_penalty_rate", 0)),
            fixed_monthly_charge=float(data.get("fixed_monthly_charge", 0)),
            power_factor_threshold=float(data.get("power_factor_threshold", 0.90)),
            currency=str(data.get("currency", "INR")),
            change_reason=change_reason,
            created_by=created_by,
            created_at=datetime.utcnow(),
        )
        self.db.add(version)
        await self.db.flush()

        if latest is not None and latest.effective_end_at is None:
            latest.effective_end_at = start_at
            latest.superseded_by_version_id = version.id

        return version
    
    async def upsert_tariff(
        self,
        data: dict,
        tenant_id: str | None = None,
        **_: object,
    ) -> TenantTariff:
        effective_tenant_id = self._effective_tenant_id(tenant_id or data.get("tenant_id"))
        if effective_tenant_id is None:
            raise ValueError("Tenant scope is required to upsert a tariff")

        existing = await self.get_tariff(effective_tenant_id)
        effective_start_at = data.get("effective_start_at")
        change_reason = data.get("change_reason")
        created_by = data.get("created_by") or data.get("updated_by")

        normalized_values = {
            "energy_rate_per_kwh": float(data.get("energy_rate_per_kwh", 0)),
            "demand_charge_per_kw": float(data.get("demand_charge_per_kw", 0)),
            "reactive_penalty_rate": float(data.get("reactive_penalty_rate", 0)),
            "fixed_monthly_charge": float(data.get("fixed_monthly_charge", 0)),
            "power_factor_threshold": float(data.get("power_factor_threshold", 0.90)),
            "currency": str(data.get("currency", "INR")),
        }

        version_needed = existing is None
        if existing is not None:
            version_needed = any(
                [
                    float(existing.energy_rate_per_kwh) != normalized_values["energy_rate_per_kwh"],
                    float(existing.demand_charge_per_kw) != normalized_values["demand_charge_per_kw"],
                    float(existing.reactive_penalty_rate) != normalized_values["reactive_penalty_rate"],
                    float(existing.fixed_monthly_charge) != normalized_values["fixed_monthly_charge"],
                    float(existing.power_factor_threshold) != normalized_values["power_factor_threshold"],
                    str(existing.currency) != normalized_values["currency"],
                ]
            )

        if existing:
            if "energy_rate_per_kwh" in data:
                existing.energy_rate_per_kwh = float(data["energy_rate_per_kwh"])
            if "demand_charge_per_kw" in data:
                existing.demand_charge_per_kw = float(data["demand_charge_per_kw"])
            if "reactive_penalty_rate" in data:
                existing.reactive_penalty_rate = float(data["reactive_penalty_rate"])
            if "fixed_monthly_charge" in data:
                existing.fixed_monthly_charge = float(data["fixed_monthly_charge"])
            if "power_factor_threshold" in data:
                existing.power_factor_threshold = float(data["power_factor_threshold"])
            if "currency" in data:
                existing.currency = str(data["currency"])
            existing.updated_at = datetime.utcnow()
            if version_needed:
                await self.create_tariff_version(
                    tenant_id=effective_tenant_id,
                    data=normalized_values,
                    effective_start_at=effective_start_at,
                    change_reason=change_reason,
                    created_by=created_by,
                )
            await self.db.commit()
            await self.db.refresh(existing)
            return existing
        else:
            tariff = TenantTariff(
                tenant_id=effective_tenant_id,
                energy_rate_per_kwh=float(data.get("energy_rate_per_kwh", 0)),
                demand_charge_per_kw=float(data.get("demand_charge_per_kw", 0)),
                reactive_penalty_rate=float(data.get("reactive_penalty_rate", 0)),
                fixed_monthly_charge=float(data.get("fixed_monthly_charge", 0)),
                power_factor_threshold=float(data.get("power_factor_threshold", 0.90)),
                currency=data.get("currency", "INR"),
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            tariff = await self.create(tariff)
            await self.create_tariff_version(
                tenant_id=effective_tenant_id,
                data=normalized_values,
                effective_start_at=effective_start_at,
                change_reason=change_reason,
                created_by=created_by,
            )
            await self.db.commit()
            return tariff

    async def activate_version(
        self,
        *,
        version_id: int,
        tenant_id: str,
        activated_by: str | None = None,
        effective_start_at: datetime | None = None,
    ) -> TenantTariff:
        effective_tenant_id = self._effective_tenant_id(tenant_id)
        if effective_tenant_id is None:
            raise ValueError("Tenant scope is required to activate a tariff version")

        version = await self.get_version_by_id(version_id, effective_tenant_id)
        if version is None:
            raise LookupError("Tariff version not found")

        current_version = await self.get_effective_version(effective_tenant_id)
        current_tariff = await self.get_tariff(effective_tenant_id)
        if current_version is not None and current_tariff is not None and current_version.id == version.id:
            return current_tariff

        return await self.upsert_tariff(
            tenant_id=effective_tenant_id,
            data={
                "tenant_id": effective_tenant_id,
                "energy_rate_per_kwh": float(version.energy_rate_per_kwh),
                "demand_charge_per_kw": float(version.demand_charge_per_kw),
                "reactive_penalty_rate": float(version.reactive_penalty_rate),
                "fixed_monthly_charge": float(version.fixed_monthly_charge),
                "power_factor_threshold": float(version.power_factor_threshold),
                "currency": str(version.currency),
                "effective_start_at": effective_start_at,
                "change_reason": f"reactivated version {version.version_number}",
                "created_by": activated_by,
                "updated_by": activated_by,
            },
        )
