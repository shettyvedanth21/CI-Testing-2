from datetime import datetime
from typing import Optional

from sqlalchemy import delete, func, select, update

from src.models import WasteAnalysisJob, WasteDeviceSummary, WasteStatus
from services.shared.tenant_context import TenantContext


class WasteRepository:
    def __init__(self, db, ctx: TenantContext | None = None):
        self.db = db
        self._ctx = ctx or TenantContext.system("svc:waste-analysis-service")
        self._tenant_id = self._ctx.tenant_id

    async def create_job(
        self,
        job_id: str,
        job_name: Optional[str],
        scope: str,
        device_ids: Optional[list[str]],
        start_date,
        end_date,
        granularity: str,
        **_: object,
    ) -> WasteAnalysisJob:
        job = WasteAnalysisJob(
            id=job_id,
            tenant_id=self._require_tenant_id(),
            job_name=job_name,
            scope=scope,
            device_ids=device_ids,
            start_date=start_date,
            end_date=end_date,
            granularity=granularity,
            status="pending",
            progress_pct=0,
            stage="Queued",
            result_json={"tenant_id": self._tenant_id} if self._tenant_id else {},
            created_at=datetime.utcnow(),
        )
        self.db.add(job)
        await self.db.commit()
        await self.db.refresh(job)
        return job

    def _require_tenant_id(self) -> str:
        return self._ctx.require_tenant()

    def _tenant_scoped_job_query(self):
        statement = select(WasteAnalysisJob)
        if self._tenant_id is not None:
            statement = statement.where(WasteAnalysisJob.tenant_id == self._tenant_id)
        return statement

    async def get_job(self, job_id: str, **_: object) -> Optional[WasteAnalysisJob]:
        result = await self.db.execute(
            self._tenant_scoped_job_query().where(WasteAnalysisJob.id == job_id)
        )
        return result.scalar_one_or_none()

    async def list_jobs(self, limit: int = 20, offset: int = 0, **_: object) -> list[WasteAnalysisJob]:
        result = await self.db.execute(
            self._tenant_scoped_job_query()
            .order_by(WasteAnalysisJob.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def update_job(self, job_id: str, **kwargs) -> None:
        payload = {k: v for k, v in kwargs.items() if v is not None}
        if not payload:
            return
        existing = await self.get_job(job_id)
        existing_meta = existing.result_json if existing and isinstance(existing.result_json, dict) else {}
        if "result_json" in payload and isinstance(payload["result_json"], dict) and existing_meta.get("tenant_id"):
            payload["result_json"] = {**payload["result_json"], "tenant_id": existing_meta["tenant_id"]}
        await self.db.execute(update(WasteAnalysisJob).where(WasteAnalysisJob.id == job_id).values(**payload))
        await self.db.commit()

    async def replace_device_summaries(self, job_id: str, summaries: list[dict]) -> None:
        await self.db.execute(delete(WasteDeviceSummary).where(WasteDeviceSummary.job_id == job_id))
        tenant_id = self._require_tenant_id()
        for s in summaries:
            self.db.add(WasteDeviceSummary(job_id=job_id, tenant_id=tenant_id, **s))
        await self.db.commit()

    async def replace_device_summaries_chunked(self, job_id: str, summaries: list[dict], batch_size: int = 500) -> None:
        await self.db.execute(delete(WasteDeviceSummary).where(WasteDeviceSummary.job_id == job_id))
        await self.db.commit()

        tenant_id = self._require_tenant_id()
        size = max(1, int(batch_size))
        for i in range(0, len(summaries), size):
            batch = summaries[i : i + size]
            for s in batch:
                self.db.add(WasteDeviceSummary(job_id=job_id, tenant_id=tenant_id, **s))
            await self.db.commit()

    async def list_device_summaries(self, job_id: str) -> list[WasteDeviceSummary]:
        statement = select(WasteDeviceSummary).where(WasteDeviceSummary.job_id == job_id)
        if self._tenant_id is not None:
            statement = statement.where(WasteDeviceSummary.tenant_id == self._tenant_id)
        result = await self.db.execute(statement.order_by(WasteDeviceSummary.id.asc()))
        return list(result.scalars().all())

    async def find_active_duplicate(
        self,
        scope: str,
        device_ids: Optional[list[str]],
        start_date,
        end_date,
        granularity: str,
        limit: int = 50,
        **_: object,
    ) -> Optional[WasteAnalysisJob]:
        statement = (
            self._tenant_scoped_job_query()
            .where(WasteAnalysisJob.status.in_(["pending", "running"]))
            .where(WasteAnalysisJob.scope == scope)
            .where(WasteAnalysisJob.start_date == start_date)
            .where(WasteAnalysisJob.end_date == end_date)
            .where(WasteAnalysisJob.granularity == granularity)
            .order_by(WasteAnalysisJob.created_at.desc())
            .limit(limit)
        )
        result = await self.db.execute(statement)
        requested_ids = sorted(device_ids or [])
        for job in result.scalars().all():
            existing_ids = sorted(job.device_ids or [])
            if existing_ids == requested_ids:
                return job
        return None

    async def count_pending_jobs_for_tenant(self, tenant_id: str) -> int:
        result = await self.db.execute(
            select(func.count())
            .select_from(WasteAnalysisJob)
            .where(WasteAnalysisJob.tenant_id == tenant_id)
            .where(WasteAnalysisJob.status.in_(["pending", "running"]))
        )
        return int(result.scalar() or 0)

    async def count_pending_jobs_global(self) -> int:
        result = await self.db.execute(
            select(func.count())
            .select_from(WasteAnalysisJob)
            .where(WasteAnalysisJob.status.in_(["pending", "running"]))
        )
        return int(result.scalar() or 0)

    async def count_active_workers(self) -> int:
        from datetime import datetime, timedelta
        cutoff = datetime.utcnow() - timedelta(seconds=120)
        result = await self.db.execute(
            select(func.count())
            .select_from(WasteAnalysisJob.__table__)
            .where(
                WasteAnalysisJob.__table__.c.last_heartbeat_at.isnot(None),
            )
        )
        return int(result.scalar() or 0)

    async def count_by_status(self) -> dict[str, int]:
        result = await self.db.execute(
            select(WasteAnalysisJob.status, func.count())
            .group_by(WasteAnalysisJob.status)
        )
        return {str(row[0]): int(row[1]) for row in result.all()}

    async def aggregate_runtime_counters(self) -> dict[str, int]:
        result = await self.db.execute(
            select(
                func.coalesce(func.sum(WasteAnalysisJob.retry_count), 0).label("retry_count"),
                func.coalesce(func.sum(WasteAnalysisJob.timeout_count), 0).label("timeout_count"),
            )
        )
        row = result.one()
        return {"retry_count": int(row.retry_count or 0), "timeout_count": int(row.timeout_count or 0)}

    async def list_jobs_for_retention(
        self,
        *,
        cutoff: datetime,
        limit: int,
    ) -> list[tuple[str, str | None]]:
        result = await self.db.execute(
            select(WasteAnalysisJob.id, WasteAnalysisJob.s3_key)
            .where(WasteAnalysisJob.status.in_(["completed", "failed"]))
            .where(func.coalesce(WasteAnalysisJob.completed_at, WasteAnalysisJob.created_at) < cutoff)
            .order_by(func.coalesce(WasteAnalysisJob.completed_at, WasteAnalysisJob.created_at).asc())
            .limit(max(1, int(limit)))
        )
        return [(str(row.id), row.s3_key) for row in result.all()]

    async def delete_jobs_by_ids(self, job_ids: list[str]) -> int:
        if not job_ids:
            return 0
        await self.db.execute(delete(WasteDeviceSummary).where(WasteDeviceSummary.job_id.in_(job_ids)))
        result = await self.db.execute(delete(WasteAnalysisJob).where(WasteAnalysisJob.id.in_(job_ids)))
        await self.db.commit()
        return int(result.rowcount or 0)
