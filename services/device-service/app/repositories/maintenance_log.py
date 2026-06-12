"""Maintenance Log repository layer."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import Select, case, func, select

from app.models.device import MaintenanceLog
from services.shared.scoped_repository import TenantScopedRepository
from services.shared.tenant_context import TenantContext


class MaintenanceLogRepository(TenantScopedRepository[MaintenanceLog]):
    model = MaintenanceLog

    def __init__(self, session, ctx: TenantContext | None = None, allow_cross_tenant: bool = False):
        effective_ctx = ctx or TenantContext.system("svc:device-service")
        super().__init__(session, effective_ctx, allow_cross_tenant=allow_cross_tenant or ctx is None)

    async def list_for_device(self, device_id: str) -> list[MaintenanceLog]:
        statement: Select[tuple[MaintenanceLog]] = (
            select(MaintenanceLog)
            .where(MaintenanceLog.device_id == device_id)
            .order_by(MaintenanceLog.maintenance_date.desc(), MaintenanceLog.id.desc())
        )
        statement = self._apply_tenant_scope_select(statement)
        result = await self._session.execute(statement)
        return list(result.scalars().all())

    async def get_for_device(self, device_id: str, record_id: int) -> MaintenanceLog | None:
        statement: Select[tuple[MaintenanceLog]] = (
            select(MaintenanceLog)
            .where(
                MaintenanceLog.id == record_id,
                MaintenanceLog.device_id == device_id,
            )
        )
        statement = self._apply_tenant_scope_select(statement)
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def update(self, record: MaintenanceLog) -> MaintenanceLog:
        await self._session.flush()
        await self._session.refresh(record)
        return record

    async def delete(self, record: MaintenanceLog) -> None:
        await self._session.delete(record)
        await self._session.flush()

    async def get_summary(self, device_id: str) -> dict[str, object]:
        next_due_case = case((MaintenanceLog.next_due_date.is_not(None), MaintenanceLog.next_due_date))
        statement = (
            select(
                func.count(MaintenanceLog.id),
                func.coalesce(func.sum(MaintenanceLog.cost), Decimal("0.00")),
                func.max(MaintenanceLog.maintenance_date),
                func.max(MaintenanceLog.updated_at),
                func.min(next_due_case),
            )
            .where(MaintenanceLog.device_id == device_id)
        )
        statement = self._apply_tenant_scope_select(statement)
        result = await self._session.execute(statement)
        total_records, total_cost, latest_maintenance_date, last_recorded_at, next_due_date = result.one()
        return {
            "total_records": int(total_records or 0),
            "total_cost": total_cost if total_cost is not None else Decimal("0.00"),
            "latest_maintenance_date": latest_maintenance_date,
            "last_recorded_at": last_recorded_at,
            "next_due_date": next_due_date if isinstance(next_due_date, date) else None,
        }
