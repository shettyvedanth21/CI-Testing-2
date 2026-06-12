from datetime import datetime, timedelta
from typing import Optional
from uuid import uuid4
from sqlalchemy import or_, select, update

from src.models import ScheduledReport, ScheduledReportType, ScheduledFrequency
from src.services.report_scope import schedule_visible_to_scope
from services.shared.scoped_repository import TenantScopedRepository
from services.shared.tenant_context import TenantContext

FREQUENCY_OFFSETS = {
    "daily": timedelta(days=1),
    "weekly": timedelta(days=7),
    "monthly": timedelta(days=30),
}


class ScheduledRepository(TenantScopedRepository[ScheduledReport]):
    model = ScheduledReport

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

    def _scope_dml(self, statement, tenant_id: str | None = None):
        effective_tenant_id = self._effective_tenant_id(tenant_id)
        if effective_tenant_id is not None and self._has_tenant_column():
            statement = statement.where(getattr(self.model, "tenant_id") == effective_tenant_id)
        return statement
    
    async def create_schedule(self, data: dict) -> ScheduledReport:
        freq = data["frequency"]
        offset = FREQUENCY_OFFSETS.get(freq, timedelta(days=1))
        tenant_id = self._effective_tenant_id(data.get("tenant_id"))
        if tenant_id is None:
            raise ValueError("Tenant scope is required to create a schedule")
        
        schedule = ScheduledReport(
            schedule_id=str(uuid4()),
            tenant_id=tenant_id,
            report_type=ScheduledReportType(data["report_type"]),
            frequency=ScheduledFrequency(data["frequency"]),
            params_template=data["params_template"],
            is_active=True,
            next_run_at=datetime.utcnow() + offset,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        schedule = await self.create(schedule)
        await self.db.commit()
        return schedule
    
    async def get_schedule(
        self,
        schedule_id: str,
        accessible_device_ids: list[str] | None = None,
    ) -> Optional[ScheduledReport]:
        schedule = await self.get_by_id(schedule_id, id_field="schedule_id")
        if schedule is None or not schedule_visible_to_scope(schedule.params_template, accessible_device_ids):
            return None
        return schedule
    
    async def list_schedules(
        self,
        *_: object,
        accessible_device_ids: list[str] | None = None,
        **__: object,
    ) -> list[ScheduledReport]:
        query = self._scope_select(
            select(ScheduledReport).order_by(ScheduledReport.created_at.desc())
        )
        result = await self.db.execute(query)
        return [
            schedule
            for schedule in result.scalars().all()
            if schedule_visible_to_scope(schedule.params_template, accessible_device_ids)
        ]
    
    async def get_due_schedules(self) -> list[ScheduledReport]:
        now = datetime.utcnow()
        query = self._scope_select(
            select(ScheduledReport)
            .where(
                ScheduledReport.is_active == True,
                ScheduledReport.next_run_at <= now
            )
        )
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def claim_due_schedules(
        self,
        *,
        now: datetime | None = None,
        limit: int = 25,
        stale_after: timedelta | None = None,
    ) -> list[ScheduledReport]:
        now = now or datetime.utcnow()
        stale_after = stale_after or timedelta(minutes=15)
        stale_before = now - stale_after

        query = self._scope_select(
            select(ScheduledReport)
            .where(
                ScheduledReport.is_active == True,
                ScheduledReport.next_run_at <= now,
                or_(
                    ScheduledReport.processing_started_at.is_(None),
                    ScheduledReport.processing_started_at < stale_before,
                ),
            )
            .order_by(ScheduledReport.next_run_at.asc(), ScheduledReport.created_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        result = await self.db.execute(query)
        schedules = list(result.scalars().all())
        if not schedules:
            await self.db.rollback()
            return []

        for schedule in schedules:
            schedule.processing_started_at = now
            schedule.last_run_at = now
            schedule.last_status = "processing"
            schedule.updated_at = now

        await self.db.commit()

        for schedule in schedules:
            await self.db.refresh(schedule)

        return schedules
    
    async def update_schedule(
        self,
        schedule_id: str,
        **kwargs
    ) -> None:
        update_values = dict(kwargs)
        if update_values:
            update_values["updated_at"] = datetime.utcnow()
        statement = (
            update(ScheduledReport)
            .where(ScheduledReport.schedule_id == schedule_id)
            .values(**update_values)
        )
        statement = self._scope_dml(statement)
        await self.db.execute(statement)
        await self.db.commit()
