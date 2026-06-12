from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EnergyReconcileAudit, EnergyReconcileRun


class ReconciliationRunRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def create_run(self, **payload: Any) -> EnergyReconcileRun:
        run = EnergyReconcileRun(**payload)
        self._session.add(run)
        await self._session.flush()
        return run

    async def get_run(self, run_id: str) -> EnergyReconcileRun | None:
        return await self._session.get(EnergyReconcileRun, run_id)

    async def update_run(self, run_id: str, **values: Any) -> bool:
        result = await self._session.execute(
            update(EnergyReconcileRun)
            .where(EnergyReconcileRun.run_id == run_id)
            .values(**values)
        )
        await self._session.flush()
        return bool(result.rowcount)


class ReconciliationAuditRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def create_item(self, **payload: Any) -> EnergyReconcileAudit:
        item = EnergyReconcileAudit(**payload)
        self._session.add(item)
        await self._session.flush()
        return item

    async def get_item(self, audit_id: int) -> EnergyReconcileAudit | None:
        return await self._session.get(EnergyReconcileAudit, audit_id)

    async def list_run_items(self, run_id: str) -> list[EnergyReconcileAudit]:
        result = await self._session.execute(
            select(EnergyReconcileAudit)
            .where(EnergyReconcileAudit.run_id == run_id)
            .order_by(EnergyReconcileAudit.id.asc())
        )
        return list(result.scalars().all())

    async def update_status(
        self,
        audit_id: int,
        *,
        status: str,
        approved_by: str | None = None,
        approved_at: datetime | None = None,
        rejected_by: str | None = None,
        rejected_at: datetime | None = None,
        rejection_reason: str | None = None,
        applied_by: str | None = None,
        applied_at: datetime | None = None,
        repaired: bool | None = None,
        new_quality_flags: dict[str, Any] | None = None,
    ) -> bool:
        values: dict[str, Any] = {"status": status}
        if approved_by is not None:
            values["approved_by"] = approved_by
        if approved_at is not None:
            values["approved_at"] = approved_at
        if rejected_by is not None:
            values["rejected_by"] = rejected_by
        if rejected_at is not None:
            values["rejected_at"] = rejected_at
        if rejection_reason is not None:
            values["rejection_reason"] = rejection_reason
        if applied_by is not None:
            values["applied_by"] = applied_by
        if applied_at is not None:
            values["applied_at"] = applied_at
        if repaired is not None:
            values["repaired"] = repaired
        if new_quality_flags is not None:
            values["new_quality_flags"] = new_quality_flags

        result = await self._session.execute(
            update(EnergyReconcileAudit)
            .where(EnergyReconcileAudit.id == audit_id)
            .values(**values)
        )
        await self._session.flush()
        return bool(result.rowcount)
