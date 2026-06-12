from datetime import datetime, timedelta
from typing import Optional
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import load_only

from src.models import EnergyReport, ReportType, ReportStatus
from src.services.report_scope import report_visible_to_scope
from services.shared.scoped_repository import TenantScopedRepository
from services.shared.tenant_context import TenantContext


class ReportRepository(TenantScopedRepository[EnergyReport]):
    model = EnergyReport

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
    
    async def create_report(
        self,
        report_id: str,
        report_type: str,
        params: dict,
        tenant_id: str | None = None,
        **_: object,
    ) -> EnergyReport:
        effective_tenant_id = self._effective_tenant_id(tenant_id)
        if effective_tenant_id is None:
            raise ValueError("Tenant scope is required to create a report")

        report = EnergyReport(
            report_id=report_id,
            tenant_id=effective_tenant_id,
            report_type=ReportType(report_type),
            status="pending",
            progress=0,
            phase="queued",
            phase_label="Queued",
            phase_progress=0.0,
            params=params,
            root_report_id=report_id,
            revision_number=1,
            is_authoritative=True,
            enqueued_at=datetime.utcnow(),
            created_at=datetime.utcnow(),
        )
        report = await self.create(report)
        await self.db.commit()
        return report

    async def create_revision_report(
        self,
        *,
        new_report_id: str,
        supersedes_report_id: str,
        revision_reason: str,
        tenant_id: str | None = None,
        params: dict | None = None,
        generated_from_reconciliation_run_id: str | None = None,
        tariff_version_id: int | None = None,
    ) -> EnergyReport:
        effective_tenant_id = self._effective_tenant_id(tenant_id)
        if effective_tenant_id is None:
            raise ValueError("Tenant scope is required to create a report revision")

        prior = await self.get_report(supersedes_report_id, tenant_id=effective_tenant_id)
        if prior is None:
            raise ValueError("Superseded report not found")

        root_report_id = str(prior.root_report_id or prior.report_id)
        result = await self.db.execute(
            self._scope_select(
                select(func.max(EnergyReport.revision_number)).where(EnergyReport.root_report_id == root_report_id),
                tenant_id=effective_tenant_id,
            )
        )
        latest_revision = result.scalar_one_or_none() or 1

        report = EnergyReport(
            report_id=new_report_id,
            tenant_id=effective_tenant_id,
            report_type=prior.report_type,
            status=ReportStatus.pending,
            progress=0,
            phase="queued",
            phase_label="Queued",
            phase_progress=0.0,
            params=params or dict(prior.params or {}),
            computation_mode=prior.computation_mode,
            phase_type_used=prior.phase_type_used,
            root_report_id=root_report_id,
            revision_number=int(latest_revision) + 1,
            supersedes_report_id=prior.report_id,
            is_authoritative=False,
            revision_reason=revision_reason,
            generated_from_reconciliation_run_id=generated_from_reconciliation_run_id,
            tariff_version_id=tariff_version_id,
            enqueued_at=datetime.utcnow(),
            created_at=datetime.utcnow(),
        )
        report = await self.create(report)
        await self.db.commit()
        return report

    async def finalize_revision_report(
        self,
        report_id: str,
        tenant_id: str | None = None,
    ) -> bool:
        effective_tenant_id = self._effective_tenant_id(tenant_id)
        report = await self.get_report(report_id, tenant_id=effective_tenant_id)
        if report is None:
            return False
        if not report.supersedes_report_id:
            return False
        prior = await self.get_report(report.supersedes_report_id, tenant_id=effective_tenant_id)
        if prior is None:
            return False
        prior.is_authoritative = False
        prior.superseded_by_report_id = report.report_id
        report.is_authoritative = True
        await self.db.commit()
        return True
    
    async def get_report(
        self,
        report_id: str,
        tenant_id: str | None = None,
        accessible_device_ids: list[str] | None = None,
        *_: object,
        **__: object,
    ) -> Optional[EnergyReport]:
        effective_tenant_id = self._effective_tenant_id(tenant_id)
        filters = []
        if effective_tenant_id is not None:
            filters.append(EnergyReport.tenant_id == effective_tenant_id)
        report = await self.get_by_id(report_id, id_field="report_id", extra_filters=filters)
        if report is None or not report_visible_to_scope(report.params, accessible_device_ids):
            return None
        return report
    
    async def update_report(
        self,
        report_id: str,
        tenant_id: str | None = None,
        **kwargs
    ) -> None:
        update_values = {k: v for k, v in kwargs.items() if v is not None}
        if not update_values:
            return
        # report_id is globally unique, so updating by report_id alone avoids
        # taking an additional tenant secondary-index lock. That secondary lock
        # caused deadlocks when multiple reports for the same tenant advanced
        # progress concurrently.
        statement = update(EnergyReport).where(EnergyReport.report_id == report_id).values(**update_values)
        await self.db.execute(statement)
        await self.db.commit()
    
    async def list_reports(
        self,
        tenant_id: str | None = None,
        limit: int = 20,
        offset: int = 0,
        report_type: Optional[str] = None,
        accessible_device_ids: list[str] | None = None,
        *args: object,
        **__: object,
    ) -> list[EnergyReport]:
        if args:
            # Backward-compatible positional pattern: (tenant_id, limit, offset, report_type)
            if len(args) > 0 and tenant_id is None and isinstance(args[0], str):
                tenant_id = args[0]
            if len(args) > 1 and isinstance(args[1], int):
                limit = args[1]
            if len(args) > 2 and isinstance(args[2], int):
                offset = args[2]
            if len(args) > 3 and isinstance(args[3], str):
                report_type = args[3]
        query = self._scope_select(select(EnergyReport), tenant_id=tenant_id)
        
        if report_type:
            query = query.where(EnergyReport.report_type == ReportType(report_type))

        # History pages only need summary fields plus params for scope filtering.
        # Coverage metadata is now part of the durable history contract, so
        # result_json must be present in the async session to avoid lazy loads.
        query = query.options(
            load_only(
                EnergyReport.report_id,
                EnergyReport.tenant_id,
                EnergyReport.report_type,
                EnergyReport.status,
                EnergyReport.params,
                EnergyReport.created_at,
                EnergyReport.completed_at,
                EnergyReport.progress,
                EnergyReport.phase,
                EnergyReport.phase_label,
                EnergyReport.phase_progress,
                EnergyReport.error_code,
                EnergyReport.error_message,
                EnergyReport.s3_key,
                EnergyReport.processing_started_at,
                EnergyReport.result_json,
            )
        ).order_by(EnergyReport.created_at.desc())

        if accessible_device_ids is None:
            query = query.offset(offset).limit(limit)
        
        result = await self.db.execute(query)
        reports = [
            report
            for report in result.scalars().all()
            if report_visible_to_scope(report.params, accessible_device_ids)
        ]
        if accessible_device_ids is None:
            return reports
        return reports[offset : offset + limit]

    async def find_active_duplicate(
        self,
        report_type: str,
        dedup_signature: str,
        tenant_id: str | None = None,
        limit: int = 50,
        **_: object,
    ) -> Optional[EnergyReport]:
        query = self._scope_select(
            select(EnergyReport)
            .where(EnergyReport.report_type == ReportType(report_type))
            .where(EnergyReport.status.in_([ReportStatus.pending, ReportStatus.processing]))
            .order_by(EnergyReport.created_at.desc())
            .limit(limit),
            tenant_id=tenant_id,
        )
        result = await self.db.execute(query)
        for report in result.scalars().all():
            params = report.params or {}
            if params.get("dedup_signature") == dedup_signature:
                return report
        return None

    async def claim_report_for_processing(
        self,
        report_id: str,
        worker_id: str,
        stale_after: timedelta,
        tenant_id: str | None = None,
        now: datetime | None = None,
    ) -> bool:
        now = now or datetime.utcnow()
        stale_cutoff = now - stale_after
        statement = (
            update(EnergyReport)
            .where(EnergyReport.report_id == report_id)
            .where(
                or_(
                    EnergyReport.status == ReportStatus.pending,
                    EnergyReport.status == ReportStatus.enqueue_failed,
                    (
                        (EnergyReport.status == ReportStatus.processing)
                        & (EnergyReport.processing_started_at.is_not(None))
                        & (EnergyReport.processing_started_at < stale_cutoff)
                    ),
                )
            )
            .values(
                status=ReportStatus.processing,
                worker_id=worker_id,
                processing_started_at=now,
                last_attempt_at=now,
                error_code=None,
                error_message=None,
                completed_at=None,
                phase="execution",
                phase_label="Running report generation",
                phase_progress=0.05,
            )
        )
        if self._effective_tenant_id(tenant_id) is not None:
            statement = statement.where(EnergyReport.tenant_id == self._effective_tenant_id(tenant_id))
        result = await self.db.execute(statement)
        await self.db.commit()
        return bool(result.rowcount)

    async def requeue_report(
        self,
        report_id: str,
        *,
        tenant_id: str | None = None,
        error_code: str,
        error_message: str,
        increment_retry: bool = True,
        increment_timeout: bool = False,
        now: datetime | None = None,
    ) -> bool:
        now = now or datetime.utcnow()
        values = {
            "status": ReportStatus.pending,
            "progress": 0,
            "worker_id": None,
            "processing_started_at": None,
            "enqueued_at": now,
            "completed_at": None,
            "error_code": error_code,
            "error_message": error_message,
            "phase": "queued",
            "phase_label": "Queued",
            "phase_progress": 0.0,
        }
        if increment_retry:
            values["retry_count"] = EnergyReport.retry_count + 1
        if increment_timeout:
            values["timeout_count"] = EnergyReport.timeout_count + 1
        statement = update(EnergyReport).where(EnergyReport.report_id == report_id).values(**values)
        if self._effective_tenant_id(tenant_id) is not None:
            statement = statement.where(EnergyReport.tenant_id == self._effective_tenant_id(tenant_id))
        result = await self.db.execute(statement)
        await self.db.commit()
        return bool(result.rowcount)

    async def fail_report(
        self,
        report_id: str,
        *,
        tenant_id: str | None = None,
        error_code: str,
        error_message: str,
        increment_retry: bool = False,
        increment_timeout: bool = False,
        now: datetime | None = None,
    ) -> bool:
        now = now or datetime.utcnow()
        values = {
            "status": ReportStatus.failed,
            "progress": 100,
            "worker_id": None,
            "processing_started_at": None,
            "completed_at": now,
            "error_code": error_code,
            "error_message": error_message,
            "phase": "failed",
            "phase_label": "Failed",
            "phase_progress": 1.0,
        }
        if increment_retry:
            values["retry_count"] = EnergyReport.retry_count + 1
        if increment_timeout:
            values["timeout_count"] = EnergyReport.timeout_count + 1
        statement = update(EnergyReport).where(EnergyReport.report_id == report_id).values(**values)
        if self._effective_tenant_id(tenant_id) is not None:
            statement = statement.where(EnergyReport.tenant_id == self._effective_tenant_id(tenant_id))
        result = await self.db.execute(statement)
        await self.db.commit()
        return bool(result.rowcount)

    async def clear_processing_claim(
        self,
        report_id: str,
        *,
        tenant_id: str | None = None,
    ) -> bool:
        statement = (
            update(EnergyReport)
            .where(EnergyReport.report_id == report_id)
            .values(
                worker_id=None,
                processing_started_at=None,
            )
        )
        if self._effective_tenant_id(tenant_id) is not None:
            statement = statement.where(EnergyReport.tenant_id == self._effective_tenant_id(tenant_id))
        result = await self.db.execute(statement)
        await self.db.commit()
        return bool(result.rowcount)

    async def load_report_for_worker(
        self,
        report_id: str,
        tenant_id: str | None = None,
    ) -> Optional[EnergyReport]:
        query = self._scope_select(select(EnergyReport).where(EnergyReport.report_id == report_id), tenant_id=tenant_id)
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def count_by_status(self, tenant_id: str | None = None) -> dict[str, int]:
        query = self._scope_select(
            select(EnergyReport.status, func.count())
            .group_by(EnergyReport.status),
            tenant_id=tenant_id,
        )
        result = await self.db.execute(query)
        counts: dict[str, int] = {}
        for status, count in result.all():
            status_value = status.value if hasattr(status, "value") else str(status)
            counts[status_value] = int(count)
        return counts

    async def count_active_jobs_for_tenant(self, tenant_id: str) -> int:
        result = await self.db.execute(
            select(func.count())
            .select_from(EnergyReport)
            .where(EnergyReport.tenant_id == tenant_id)
            .where(EnergyReport.status.in_([ReportStatus.processing]))
        )
        return int(result.scalar() or 0)

    async def aggregate_runtime_counters(self, tenant_id: str | None = None) -> dict[str, int]:
        query = self._scope_select(
            select(
                func.coalesce(func.sum(EnergyReport.retry_count), 0),
                func.coalesce(func.sum(EnergyReport.timeout_count), 0),
            ),
            tenant_id=tenant_id,
        )
        result = await self.db.execute(query)
        retry_count, timeout_count = result.one()
        return {
            "retry_count": int(retry_count or 0),
            "timeout_count": int(timeout_count or 0),
        }

    async def list_reports_for_retention(
        self,
        *,
        cutoff: datetime,
        limit: int,
    ) -> list[tuple[str, str | None]]:
        result = await self.db.execute(
            select(EnergyReport.report_id, EnergyReport.s3_key)
            .where(EnergyReport.status.in_([ReportStatus.completed, ReportStatus.failed]))
            .where(func.coalesce(EnergyReport.completed_at, EnergyReport.created_at) < cutoff)
            .order_by(func.coalesce(EnergyReport.completed_at, EnergyReport.created_at).asc())
            .limit(max(1, int(limit)))
        )
        return [(str(row.report_id), row.s3_key) for row in result.all()]

    async def delete_reports_by_ids(self, report_ids: list[str]) -> int:
        if not report_ids:
            return 0
        result = await self.db.execute(delete(EnergyReport).where(EnergyReport.report_id.in_(report_ids)))
        await self.db.commit()
        return int(result.rowcount or 0)
