"""Device repository layer - data access abstraction."""

from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import false, func, select, update

from app.models.device import Device
from services.shared.scoped_repository import TenantScopedRepository
from services.shared.tenant_context import TenantContext


class DeviceRepository(TenantScopedRepository[Device]):
    model = Device

    def __init__(self, session, ctx: TenantContext | None = None, allow_cross_tenant: bool = False):
        effective_ctx = ctx or TenantContext.system("svc:device-service")
        super().__init__(session, effective_ctx, allow_cross_tenant=allow_cross_tenant or ctx is None)

    async def get_by_id(self, device_id: str) -> Optional[Device]:
        return await super().get_by_id(
            device_id,
            id_field="device_id",
            extra_filters=[Device.deleted_at.is_(None)],
        )

    async def get_by_id_any_state(self, device_id: str) -> Optional[Device]:
        return await super().get_by_id(device_id, id_field="device_id")

    async def list_devices(
        self,
        plant_id: Optional[str] = None,
        accessible_plant_ids: Optional[List[str]] = None,
        device_type: Optional[str] = None,
        status: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[List[Device], int]:
        query = select(Device).where(Device.deleted_at.is_(None))
        count_query = select(func.count(Device.device_id)).where(Device.deleted_at.is_(None))

        query = self._apply_tenant_scope_select(query)
        count_query = self._apply_tenant_scope_select(count_query)

        if plant_id is not None:
            query = query.where(Device.plant_id == plant_id)
            count_query = count_query.where(Device.plant_id == plant_id)
        elif accessible_plant_ids is not None:
            if accessible_plant_ids:
                query = query.where(Device.plant_id.in_(accessible_plant_ids))
                count_query = count_query.where(Device.plant_id.in_(accessible_plant_ids))
            else:
                query = query.where(false())
                count_query = count_query.where(false())

        if device_type:
            query = query.where(Device.device_type == device_type)
            count_query = count_query.where(Device.device_type == device_type)

        if status:
            query = query.where(Device.status == status)
            count_query = count_query.where(Device.status == status)

        count_result = await self._session.execute(count_query)
        total = count_result.scalar() or 0

        offset = (page - 1) * page_size
        query = query.offset(offset).limit(page_size)

        result = await self._session.execute(query)
        devices = result.scalars().all()

        return list(devices), int(total)

    async def update(self, device: Device) -> Device:
        await self._session.flush()
        await self._session.refresh(device)
        return device

    @staticmethod
    def _normalize_timestamp(value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)

    async def set_first_telemetry_timestamp_if_missing(
        self,
        device_id: str,
        tenant_id: str,
        timestamp: datetime,
    ) -> bool:
        normalized = self._normalize_timestamp(timestamp)
        result = await self._session.execute(
            update(Device)
            .where(
                Device.device_id == device_id,
                Device.tenant_id == tenant_id,
                Device.deleted_at.is_(None),
                Device.first_telemetry_timestamp.is_(None),
            )
            .values(first_telemetry_timestamp=normalized)
        )
        return int(result.rowcount or 0) > 0

    async def delete(self, device: Device, soft: bool = True) -> None:
        if soft:
            device.deleted_at = datetime.utcnow()
            await self._session.flush()
        else:
            await self._session.delete(device)
            await self._session.flush()

    async def count_active_by_plant(self, plant_id: str) -> int:
        query = (
            select(func.count(Device.device_id))
            .where(
                Device.plant_id == plant_id,
                Device.deleted_at.is_(None),
            )
        )
        query = self._apply_tenant_scope_select(query)
        result = await self._session.execute(query)
        return int(result.scalar() or 0)

    async def count_active_inventory_devices(self) -> int:
        query = select(func.count(Device.device_id)).where(
            Device.device_id_class == "active",
            Device.deleted_at.is_(None),
        )
        query = self._apply_tenant_scope_select(query)
        result = await self._session.execute(query)
        return int(result.scalar() or 0)

    async def exists(self, device_id: str) -> bool:
        return await super().exists(
            device_id,
            id_field="device_id",
            extra_filters=[Device.deleted_at.is_(None)],
        )
