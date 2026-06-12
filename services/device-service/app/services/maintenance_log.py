"""Maintenance Log service layer."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device import MaintenanceLog
from app.repositories.maintenance_log import MaintenanceLogRepository
from app.schemas.device import MaintenanceLogCreate, MaintenanceLogUpdate
from services.shared.tenant_context import TenantContext


class MaintenanceLogService:
    """Business logic for tenant-scoped device maintenance history."""

    def __init__(self, session: AsyncSession, ctx: TenantContext):
        self._session = session
        self._ctx = ctx
        self._repository = MaintenanceLogRepository(session, ctx)

    async def list_records(self, device_id: str) -> list[MaintenanceLog]:
        return await self._repository.list_for_device(device_id)

    async def get_summary(self, device_id: str) -> dict[str, object]:
        return await self._repository.get_summary(device_id)

    async def create_record(self, device_id: str, payload: MaintenanceLogCreate) -> MaintenanceLog:
        record = MaintenanceLog(
            tenant_id=self._ctx.require_tenant(),
            device_id=device_id,
            maintenance_date=payload.maintenance_date,
            title=payload.title,
            description=payload.description,
            cost=payload.cost,
            performed_by=payload.performed_by,
            status=payload.status,
            next_due_date=payload.next_due_date,
            created_by=self._ctx.user_id,
        )
        created = await self._repository.create(record)
        await self._session.commit()
        return created

    async def update_record(
        self,
        device_id: str,
        record_id: int,
        payload: MaintenanceLogUpdate,
    ) -> MaintenanceLog | None:
        record = await self._repository.get_for_device(device_id, record_id)
        if record is None:
            return None

        update_data = payload.model_dump(exclude_unset=True)
        effective_maintenance_date = update_data.get("maintenance_date", record.maintenance_date)
        effective_next_due_date = update_data.get("next_due_date", record.next_due_date)
        if (
            effective_next_due_date is not None
            and effective_maintenance_date is not None
            and effective_next_due_date < effective_maintenance_date
        ):
            raise ValueError("next_due_date cannot be earlier than maintenance_date")

        for field, value in update_data.items():
            setattr(record, field, value)

        updated = await self._repository.update(record)
        await self._session.commit()
        return updated

    async def delete_record(self, device_id: str, record_id: int) -> bool:
        record = await self._repository.get_for_device(device_id, record_id)
        if record is None:
            return False

        await self._repository.delete(record)
        await self._session.commit()
        return True
