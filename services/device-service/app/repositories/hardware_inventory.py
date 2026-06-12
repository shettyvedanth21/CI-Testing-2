"""Repository helpers for hardware inventory and installation history."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device import DeviceHardwareInstallation, HardwareUnit
from services.shared.tenant_context import TenantContext


@dataclass(slots=True)
class DeviceHardwareMappingRow:
    device_id: str
    plant_id: str
    installation_role: str
    hardware_unit_id: str
    hardware_type: str
    hardware_name: str
    manufacturer: str | None
    model: str | None
    serial_number: str | None
    is_active: bool


class HardwareUnitRepository:
    def __init__(self, session: AsyncSession, ctx: TenantContext):
        self._session = session
        self._tenant_id = ctx.require_tenant()

    async def create(self, hardware_unit: HardwareUnit) -> HardwareUnit:
        hardware_unit.tenant_id = self._tenant_id
        self._session.add(hardware_unit)
        await self._session.flush()
        await self._session.refresh(hardware_unit)
        return hardware_unit

    async def get_by_unit_id(self, hardware_unit_id: str) -> Optional[HardwareUnit]:
        result = await self._session.execute(
            select(HardwareUnit).where(
                HardwareUnit.tenant_id == self._tenant_id,
                HardwareUnit.hardware_unit_id == hardware_unit_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_units(
        self,
        *,
        plant_id: str | None = None,
        unit_type: str | None = None,
        status: str | None = None,
    ) -> tuple[list[HardwareUnit], int]:
        query = select(HardwareUnit).where(HardwareUnit.tenant_id == self._tenant_id)
        count_query = select(func.count(HardwareUnit.id)).where(HardwareUnit.tenant_id == self._tenant_id)

        if plant_id is not None:
            query = query.where(HardwareUnit.plant_id == plant_id)
            count_query = count_query.where(HardwareUnit.plant_id == plant_id)
        if unit_type is not None:
            query = query.where(HardwareUnit.unit_type == unit_type)
            count_query = count_query.where(HardwareUnit.unit_type == unit_type)
        if status is not None:
            query = query.where(HardwareUnit.status == status)
            count_query = count_query.where(HardwareUnit.status == status)

        query = query.order_by(HardwareUnit.created_at.desc(), HardwareUnit.id.desc())

        count_result = await self._session.execute(count_query)
        total = int(count_result.scalar() or 0)

        result = await self._session.execute(query)
        return list(result.scalars().all()), total

    async def update(self, hardware_unit: HardwareUnit) -> HardwareUnit:
        await self._session.flush()
        await self._session.refresh(hardware_unit)
        return hardware_unit


class DeviceHardwareInstallationRepository:
    def __init__(self, session: AsyncSession, ctx: TenantContext):
        self._session = session
        self._tenant_id = ctx.require_tenant()

    async def create(self, installation: DeviceHardwareInstallation) -> DeviceHardwareInstallation:
        installation.tenant_id = self._tenant_id
        self._session.add(installation)
        await self._session.flush()
        await self._session.refresh(installation)
        return installation

    async def get_by_id(self, installation_id: int) -> Optional[DeviceHardwareInstallation]:
        result = await self._session.execute(
            select(DeviceHardwareInstallation).where(
                DeviceHardwareInstallation.tenant_id == self._tenant_id,
                DeviceHardwareInstallation.id == installation_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_active_by_hardware_unit_id(
        self, hardware_unit_id: str
    ) -> Optional[DeviceHardwareInstallation]:
        result = await self._session.execute(
            select(DeviceHardwareInstallation).where(
                DeviceHardwareInstallation.tenant_id == self._tenant_id,
                DeviceHardwareInstallation.active_hardware_unit_key == hardware_unit_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_active_by_device_role(
        self, *, device_id: str, installation_role: str
    ) -> Optional[DeviceHardwareInstallation]:
        role_key = f"{device_id}::{installation_role}"
        result = await self._session.execute(
            select(DeviceHardwareInstallation).where(
                DeviceHardwareInstallation.tenant_id == self._tenant_id,
                DeviceHardwareInstallation.active_device_role_key == role_key,
            )
        )
        return result.scalar_one_or_none()

    async def list_device_history(self, device_id: str) -> list[DeviceHardwareInstallation]:
        result = await self._session.execute(
            select(DeviceHardwareInstallation)
            .where(
                DeviceHardwareInstallation.tenant_id == self._tenant_id,
                DeviceHardwareInstallation.device_id == device_id,
            )
            .order_by(
                DeviceHardwareInstallation.commissioned_at.desc(),
                DeviceHardwareInstallation.id.desc(),
            )
        )
        return list(result.scalars().all())

    async def list_current_device_installations(
        self,
        device_id: str,
    ) -> list[DeviceHardwareInstallation]:
        result = await self._session.execute(
            select(DeviceHardwareInstallation)
            .where(
                DeviceHardwareInstallation.tenant_id == self._tenant_id,
                DeviceHardwareInstallation.device_id == device_id,
                DeviceHardwareInstallation.decommissioned_at.is_(None),
            )
            .order_by(
                DeviceHardwareInstallation.commissioned_at.desc(),
                DeviceHardwareInstallation.id.desc(),
            )
        )
        return list(result.scalars().all())

    async def list_installation_history(
        self,
        *,
        plant_id: str | None = None,
        device_id: str | None = None,
        hardware_unit_id: str | None = None,
        active_only: bool | None = None,
    ) -> list[DeviceHardwareInstallation]:
        query = select(DeviceHardwareInstallation).where(
            DeviceHardwareInstallation.tenant_id == self._tenant_id,
        )

        if plant_id is not None:
            query = query.where(DeviceHardwareInstallation.plant_id == plant_id)
        if device_id is not None:
            query = query.where(DeviceHardwareInstallation.device_id == device_id)
        if hardware_unit_id is not None:
            query = query.where(DeviceHardwareInstallation.hardware_unit_id == hardware_unit_id)
        if active_only is True:
            query = query.where(DeviceHardwareInstallation.decommissioned_at.is_(None))
        elif active_only is False:
            query = query.where(DeviceHardwareInstallation.decommissioned_at.is_not(None))

        result = await self._session.execute(
            query.order_by(
                DeviceHardwareInstallation.commissioned_at.desc(),
                DeviceHardwareInstallation.id.desc(),
            )
        )
        return list(result.scalars().all())

    async def list_current_device_mappings(
        self,
        *,
        plant_id: str | None = None,
        device_id: str | None = None,
    ) -> list[DeviceHardwareMappingRow]:
        query = (
            select(
                DeviceHardwareInstallation.device_id,
                DeviceHardwareInstallation.plant_id,
                DeviceHardwareInstallation.installation_role,
                DeviceHardwareInstallation.hardware_unit_id,
                HardwareUnit.unit_type,
                HardwareUnit.unit_name,
                HardwareUnit.manufacturer,
                HardwareUnit.model,
                HardwareUnit.serial_number,
            )
            .join(
                HardwareUnit,
                (HardwareUnit.tenant_id == DeviceHardwareInstallation.tenant_id)
                & (HardwareUnit.hardware_unit_id == DeviceHardwareInstallation.hardware_unit_id),
            )
            .where(
                DeviceHardwareInstallation.tenant_id == self._tenant_id,
                DeviceHardwareInstallation.decommissioned_at.is_(None),
            )
        )

        if plant_id is not None:
            query = query.where(DeviceHardwareInstallation.plant_id == plant_id)
        if device_id is not None:
            query = query.where(DeviceHardwareInstallation.device_id == device_id)

        result = await self._session.execute(
            query.order_by(
                DeviceHardwareInstallation.device_id.asc(),
                DeviceHardwareInstallation.installation_role.asc(),
                DeviceHardwareInstallation.id.asc(),
            )
        )
        return [
            DeviceHardwareMappingRow(
                device_id=row.device_id,
                plant_id=row.plant_id,
                installation_role=row.installation_role,
                hardware_unit_id=row.hardware_unit_id,
                hardware_type=row.unit_type,
                hardware_name=row.unit_name,
                manufacturer=row.manufacturer,
                model=row.model,
                serial_number=row.serial_number,
                is_active=True,
            )
            for row in result.all()
        ]

    async def update(self, installation: DeviceHardwareInstallation) -> DeviceHardwareInstallation:
        await self._session.flush()
        await self._session.refresh(installation)
        return installation
