"""Business logic for hardware inventory and device installation history."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device import (
    DeviceHardwareInstallation,
    HardwareUnit,
    HardwareUnitStatus,
)
from app.repositories.device import DeviceRepository
from app.repositories.hardware_inventory import (
    DeviceHardwareInstallationRepository,
    HardwareUnitRepository,
)
from app.schemas.device import (
    DeviceHardwareInstallationCreate,
    DeviceHardwareInstallationDecommission,
    HardwareUnitCreate,
    HardwareUnitUpdate,
)
from app.services.device_errors import (
    HardwareInstallationCompatibilityError,
    HardwareInstallationConflictError,
    HardwareInstallationNotFoundError,
    HardwarePlantMismatchError,
    HardwareStatusError,
    HardwareTenantMismatchError,
    HardwareUnitAlreadyExistsError,
    HardwareUnitIdAllocationError,
    HardwareUnitNotFoundError,
)
from app.services.hardware_identity import HardwareUnitIdAllocator
from app.schemas.device import HARDWARE_ROLE_COMPATIBILITY
from services.shared.tenant_context import TenantContext


class HardwareInventoryService:
    """Service layer for normalized hardware inventory and installation lifecycle."""

    def __init__(self, session: AsyncSession, ctx: TenantContext):
        self._session = session
        self._ctx = ctx
        self._tenant_id = ctx.require_tenant()
        self._hardware_units = HardwareUnitRepository(session, ctx)
        self._installations = DeviceHardwareInstallationRepository(session, ctx)
        self._devices = DeviceRepository(session, ctx)
        self._hardware_unit_id_allocator = HardwareUnitIdAllocator(session)

    async def create_hardware_unit(self, payload: HardwareUnitCreate) -> HardwareUnit:
        for _ in range(5):
            generated_hardware_unit_id = await self._hardware_unit_id_allocator.allocate()
            hardware_unit = HardwareUnit(
                hardware_unit_id=generated_hardware_unit_id,
                tenant_id=self._tenant_id,
                plant_id=payload.plant_id,
                unit_type=payload.unit_type,
                unit_name=payload.unit_name,
                manufacturer=payload.manufacturer,
                model=payload.model,
                serial_number=payload.serial_number,
                status=payload.status,
            )
            try:
                created = await self._hardware_units.create(hardware_unit)
                await self._session.commit()
                return created
            except IntegrityError:
                await self._session.rollback()
                continue

        raise HardwareUnitIdAllocationError("Unable to allocate a unique hardware unit ID")

    async def list_hardware_units(
        self,
        *,
        plant_id: str | None = None,
        unit_type: str | None = None,
        status: str | None = None,
    ) -> tuple[list[HardwareUnit], int]:
        return await self._hardware_units.list_units(
            plant_id=plant_id,
            unit_type=unit_type,
            status=status,
        )

    async def get_hardware_unit(self, hardware_unit_id: str) -> HardwareUnit:
        return await self._require_hardware_unit(hardware_unit_id)

    async def update_hardware_unit(
        self, hardware_unit_id: str, payload: HardwareUnitUpdate
    ) -> HardwareUnit:
        hardware_unit = await self._require_hardware_unit(hardware_unit_id)
        active_installation = await self._installations.get_active_by_hardware_unit_id(hardware_unit_id)

        if payload.plant_id is not None and active_installation is not None and payload.plant_id != hardware_unit.plant_id:
            raise HardwarePlantMismatchError(
                "Cannot move a hardware unit to another plant while it has an active installation."
            )

        if active_installation is not None and payload.status == HardwareUnitStatus.RETIRED.value:
            raise HardwareStatusError(
                "Active hardware installations must be decommissioned before the hardware unit can be retired."
            )

        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(hardware_unit, field, value)

        try:
            updated = await self._hardware_units.update(hardware_unit)
            await self._session.commit()
            return updated
        except IntegrityError as exc:
            await self._session.rollback()
            raise HardwareUnitAlreadyExistsError(
                f"Hardware unit '{hardware_unit_id}' already exists for this tenant."
            ) from exc

    async def install_hardware(
        self, device_id: str, payload: DeviceHardwareInstallationCreate
    ) -> DeviceHardwareInstallation:
        device = await self._require_device(device_id)
        hardware_unit = await self._require_hardware_unit(payload.hardware_unit_id)

        if device.tenant_id != hardware_unit.tenant_id:
            raise HardwareTenantMismatchError(
                "Hardware unit and device must belong to the same tenant."
            )

        if not device.plant_id:
            raise HardwarePlantMismatchError(
                "Device plant assignment is required before commissioning hardware."
            )

        if hardware_unit.plant_id != device.plant_id:
            raise HardwarePlantMismatchError(
                "Hardware unit and device must belong to the same plant."
            )

        if hardware_unit.status == HardwareUnitStatus.RETIRED.value:
            raise HardwareStatusError(
                "Retired hardware units cannot be commissioned on a device."
            )

        allowed_roles = HARDWARE_ROLE_COMPATIBILITY.get(hardware_unit.unit_type, set())
        if payload.installation_role not in allowed_roles:
            raise HardwareInstallationCompatibilityError(
                f"Hardware unit type '{hardware_unit.unit_type}' is not compatible with role '{payload.installation_role}'."
            )

        active_hardware_installation = await self._installations.get_active_by_hardware_unit_id(
            payload.hardware_unit_id
        )
        if active_hardware_installation is not None:
            raise HardwareInstallationConflictError(
                f"Hardware unit '{payload.hardware_unit_id}' already has an active installation."
            )

        active_role_installation = await self._installations.get_active_by_device_role(
            device_id=device_id,
            installation_role=payload.installation_role,
        )
        if active_role_installation is not None:
            raise HardwareInstallationConflictError(
                f"Device '{device_id}' already has an active installation for role '{payload.installation_role}'."
            )

        commissioned_at = payload.commissioned_at or datetime.now(timezone.utc)
        installation = DeviceHardwareInstallation(
            tenant_id=self._tenant_id,
            plant_id=device.plant_id,
            device_id=device_id,
            hardware_unit_id=payload.hardware_unit_id,
            installation_role=payload.installation_role,
            commissioned_at=commissioned_at,
            notes=payload.notes,
            active_hardware_unit_key=payload.hardware_unit_id,
            active_device_role_key=self._device_role_key(device_id, payload.installation_role),
        )
        try:
            created = await self._installations.create(installation)
            await self._session.commit()
            return created
        except IntegrityError as exc:
            await self._session.rollback()
            raise HardwareInstallationConflictError(
                "The requested installation conflicts with an existing active assignment."
            ) from exc

    async def decommission_installation(
        self, installation_id: int, payload: DeviceHardwareInstallationDecommission
    ) -> DeviceHardwareInstallation:
        installation = await self._installations.get_by_id(installation_id)
        if installation is None:
            raise HardwareInstallationNotFoundError(
                f"Installation '{installation_id}' was not found."
            )

        if installation.decommissioned_at is not None:
            raise HardwareInstallationConflictError(
                f"Installation '{installation_id}' is already decommissioned."
            )

        commissioned_at = self._normalize_utc(installation.commissioned_at)
        if payload.decommissioned_at is not None:
            decommissioned_at = self._normalize_utc(payload.decommissioned_at)
        else:
            # Auto-generated decommission timestamps should never fail for a
            # freshly created installation because of sub-second precision or
            # cross-process clock skew. Clamp the implicit "now" timestamp to
            # the recorded commission time.
            decommissioned_at = max(
                self._normalize_utc(datetime.now(timezone.utc)),
                commissioned_at,
            )
        if decommissioned_at < commissioned_at:
            raise HardwareInstallationConflictError(
                "Decommission time cannot be earlier than the commission time."
            )

        hardware_unit = await self._require_hardware_unit(installation.hardware_unit_id)
        installation.decommissioned_at = decommissioned_at
        installation.active_hardware_unit_key = None
        installation.active_device_role_key = None
        if payload.notes is not None:
            installation.notes = payload.notes
        updated = await self._installations.update(installation)
        await self._session.commit()
        return updated

    async def get_device_installation_history(self, device_id: str) -> list[DeviceHardwareInstallation]:
        await self._require_device(device_id)
        return await self._installations.list_device_history(device_id)

    async def list_current_device_installations(
        self,
        device_id: str,
    ) -> list[DeviceHardwareInstallation]:
        await self._require_device(device_id)
        return await self._installations.list_current_device_installations(device_id)

    async def list_installation_history(
        self,
        *,
        plant_id: str | None = None,
        device_id: str | None = None,
        hardware_unit_id: str | None = None,
        active_only: bool | None = None,
    ) -> list[DeviceHardwareInstallation]:
        if device_id is not None:
            await self._require_device(device_id)
        if hardware_unit_id is not None:
            await self._require_hardware_unit(hardware_unit_id)
        return await self._installations.list_installation_history(
            plant_id=plant_id,
            device_id=device_id,
            hardware_unit_id=hardware_unit_id,
            active_only=active_only,
        )

    async def list_current_device_mappings(
        self,
        *,
        plant_id: str | None = None,
        device_id: str | None = None,
    ):
        if device_id is not None:
            await self._require_device(device_id)
        return await self._installations.list_current_device_mappings(
            plant_id=plant_id,
            device_id=device_id,
        )

    async def get_installation(self, installation_id: int) -> DeviceHardwareInstallation:
        installation = await self._installations.get_by_id(installation_id)
        if installation is None:
            raise HardwareInstallationNotFoundError(
                f"Installation '{installation_id}' was not found."
            )
        return installation

    async def _require_device(self, device_id: str):
        device = await self._devices.get_by_id(device_id)
        if device is None:
            raise HardwareInstallationNotFoundError(f"Device '{device_id}' was not found.")
        return device

    async def _require_hardware_unit(self, hardware_unit_id: str) -> HardwareUnit:
        hardware_unit = await self._hardware_units.get_by_unit_id(hardware_unit_id)
        if hardware_unit is None:
            raise HardwareUnitNotFoundError(
                f"Hardware unit '{hardware_unit_id}' was not found."
            )
        return hardware_unit

    @staticmethod
    def _device_role_key(device_id: str, installation_role: str) -> str:
        return f"{device_id}::{installation_role}"

    @staticmethod
    def _normalize_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
