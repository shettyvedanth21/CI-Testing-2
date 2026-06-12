"""MySQL implementation of result repository."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import math
import structlog
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from services.shared.tenant_context import TenantContext
from src.models.database import AnalyticsJob, ModelArtifact
from src.models.schemas import JobStatus
from src.services.result_repository import ResultRepository, UNSET
from src.utils.exceptions import JobNotFoundError

logger = structlog.get_logger()


class MySQLResultRepository(ResultRepository):
    """MySQL implementation of result repository."""

    def __init__(self, session: AsyncSession, ctx: TenantContext | None = None):
        self._session = session
        self._ctx = ctx
        self._logger = logger.bind(repository="MySQLResultRepository")

    def _sanitize_json(self, value: Any) -> Any:
        """Recursively replace NaN / inf values so JSON inserts never fail."""

        if value is None:
            return None

        if isinstance(value, float):
            if math.isnan(value) or math.isinf(value):
                return None
            return value

        if hasattr(value, "tolist") and not isinstance(value, (str, bytes, bytearray)):
            try:
                return self._sanitize_json(value.tolist())
            except Exception:
                pass

        if isinstance(value, list):
            return [self._sanitize_json(v) for v in value]

        if isinstance(value, dict):
            return {k: self._sanitize_json(v) for k, v in value.items()}

        return value

    async def create_job(
        self,
        job_id: str,
        device_id: str,
        analysis_type: str,
        model_name: str,
        date_range_start: datetime,
        date_range_end: datetime,
        parameters: Optional[Dict[str, Any]],
        job_kind: str = "single",
        parent_job_id: Optional[str] = None,
    ) -> None:
        tenant_id = None
        if isinstance(parameters, dict):
            normalized_tenant_id = str(parameters.get("tenant_id") or "").strip()
            tenant_id = normalized_tenant_id or None
        job = AnalyticsJob(
            job_id=job_id,
            tenant_id=tenant_id,
            device_id=device_id,
            job_kind=job_kind,
            parent_job_id=parent_job_id,
            analysis_type=analysis_type,
            model_name=model_name,
            date_range_start=date_range_start,
            date_range_end=date_range_end,
            parameters=self._sanitize_json(parameters),
            status=JobStatus.PENDING.value,
            progress=0.0,
            phase="queued",
            phase_label="Queued",
            phase_progress=0.0,
        )

        self._session.add(job)
        await self._session.commit()

        self._logger.info("job_created", job_id=job_id, device_id=device_id)

    async def get_job(self, job_id: str) -> AnalyticsJob:
        result = await self._session.execute(
            select(AnalyticsJob).where(AnalyticsJob.job_id == job_id)
        )
        job = result.scalar_one_or_none()

        if not job:
            raise JobNotFoundError(f"Job {job_id} not found")

        return job

    async def find_active_duplicate(
        self,
        *,
        tenant_id: str,
        device_id: str,
        analysis_type: str,
        model_name: str,
    ) -> Optional[AnalyticsJob]:
        statement = (
            select(AnalyticsJob)
            .where(AnalyticsJob.tenant_id == tenant_id)
            .where(AnalyticsJob.device_id == device_id)
            .where(AnalyticsJob.analysis_type == analysis_type)
            .where(AnalyticsJob.model_name == model_name)
            .where(AnalyticsJob.status.in_([JobStatus.PENDING.value, JobStatus.RUNNING.value]))
            .order_by(AnalyticsJob.created_at.desc())
            .limit(1)
        )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    def _extract_job_device_ids(self, job: AnalyticsJob) -> list[str]:
        if str(job.device_id or "").strip() and str(job.device_id) != "ALL":
            return [str(job.device_id)]

        params = job.parameters if isinstance(job.parameters, dict) else {}
        raw_device_ids = params.get("device_ids")
        if not isinstance(raw_device_ids, list):
            return []

        normalized: list[str] = []
        seen: set[str] = set()
        for device_id in raw_device_ids:
            normalized_id = str(device_id).strip()
            if normalized_id and normalized_id not in seen:
                seen.add(normalized_id)
                normalized.append(normalized_id)
        return normalized

    def _job_is_visible(
        self,
        job: AnalyticsJob,
        *,
        tenant_id: Optional[str] = None,
        accessible_device_ids: Optional[list[str]] = None,
    ) -> bool:
        params = job.parameters if isinstance(job.parameters, dict) else {}
        job_tenant_id = str(getattr(job, "tenant_id", None) or "").strip() or None
        effective_job_tenant_id = job_tenant_id or (str(params.get("tenant_id") or "").strip() or None)
        if tenant_id is not None and effective_job_tenant_id != tenant_id:
            return False

        if accessible_device_ids is None:
            return True

        referenced_device_ids = self._extract_job_device_ids(job)
        if not referenced_device_ids:
            return False

        accessible_set = set(str(device_id) for device_id in accessible_device_ids)
        return all(device_id in accessible_set for device_id in referenced_device_ids)

    @staticmethod
    def _tenant_match_clause(tenant_id: str):
        return AnalyticsJob.tenant_id == tenant_id

    def _list_jobs_base_query(
        self,
        *,
        status: Optional[str] = None,
        device_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        job_kinds: Optional[list[str]] = None,
        parent_job_id: Optional[str] = None,
        only_undispatched: Optional[bool] = None,
        include_results: bool = False,
        order_by_created_desc: bool = True,
    ):
        columns = [
            AnalyticsJob.job_id,
            AnalyticsJob.device_id,
            AnalyticsJob.job_kind,
            AnalyticsJob.parent_job_id,
            AnalyticsJob.analysis_type,
            AnalyticsJob.model_name,
            AnalyticsJob.date_range_start,
            AnalyticsJob.date_range_end,
            AnalyticsJob.parameters,
            AnalyticsJob.status,
            AnalyticsJob.progress,
            AnalyticsJob.phase,
            AnalyticsJob.phase_label,
            AnalyticsJob.phase_progress,
            AnalyticsJob.message,
            AnalyticsJob.error_message,
            AnalyticsJob.created_at,
            AnalyticsJob.started_at,
            AnalyticsJob.completed_at,
            AnalyticsJob.attempt,
            AnalyticsJob.queue_position,
            AnalyticsJob.queue_enqueued_at,
            AnalyticsJob.queue_dispatched_at,
            AnalyticsJob.queue_started_at,
            AnalyticsJob.worker_lease_expires_at,
            AnalyticsJob.last_heartbeat_at,
            AnalyticsJob.error_code,
        ]
        if include_results:
            columns.append(AnalyticsJob.results)

        query = select(AnalyticsJob).options(load_only(*columns))
        if order_by_created_desc:
            query = query.order_by(AnalyticsJob.created_at.desc())

        if status:
            query = query.where(AnalyticsJob.status == status)
        if device_id:
            query = query.where(AnalyticsJob.device_id == device_id)
        if job_kinds:
            query = query.where(AnalyticsJob.job_kind.in_(job_kinds))
        if parent_job_id:
            query = query.where(AnalyticsJob.parent_job_id == parent_job_id)
        if only_undispatched is True:
            query = query.where(AnalyticsJob.queue_dispatched_at.is_(None))
        elif only_undispatched is False:
            query = query.where(AnalyticsJob.queue_dispatched_at.is_not(None))
        if tenant_id:
            query = query.where(self._tenant_match_clause(tenant_id))

        return query

    async def list_jobs_for_worker_scan(
        self,
        *,
        status: Optional[str] = None,
        job_kinds: Optional[list[str]] = None,
        only_undispatched: Optional[bool] = None,
        limit: int = 5000,
    ) -> List[Any]:
        limit = max(1, int(limit))
        query = (
            select(
                AnalyticsJob.job_id,
                AnalyticsJob.device_id,
                AnalyticsJob.job_kind,
                AnalyticsJob.parent_job_id,
                AnalyticsJob.analysis_type,
                AnalyticsJob.model_name,
                AnalyticsJob.date_range_start,
                AnalyticsJob.date_range_end,
                AnalyticsJob.parameters,
                AnalyticsJob.status,
                AnalyticsJob.created_at,
                AnalyticsJob.attempt,
                AnalyticsJob.phase,
                AnalyticsJob.worker_lease_expires_at,
                AnalyticsJob.queue_dispatched_at,
                AnalyticsJob.queue_started_at,
            )
            .limit(limit)
        )

        if status:
            query = query.where(AnalyticsJob.status == status)
        if job_kinds:
            query = query.where(AnalyticsJob.job_kind.in_(job_kinds))
        if only_undispatched is True:
            query = query.where(AnalyticsJob.queue_dispatched_at.is_(None))
        elif only_undispatched is False:
            query = query.where(AnalyticsJob.queue_dispatched_at.is_not(None))

        result = await self._session.execute(query)
        return [
            SimpleNamespace(
                job_id=row.job_id,
                device_id=row.device_id,
                job_kind=row.job_kind,
                parent_job_id=row.parent_job_id,
                analysis_type=row.analysis_type,
                model_name=row.model_name,
                date_range_start=row.date_range_start,
                date_range_end=row.date_range_end,
                parameters=row.parameters,
                status=row.status,
                created_at=row.created_at,
                attempt=row.attempt,
                phase=row.phase,
                worker_lease_expires_at=row.worker_lease_expires_at,
                queue_dispatched_at=row.queue_dispatched_at,
                queue_started_at=row.queue_started_at,
            )
            for row in result.all()
        ]

    async def list_running_jobs_for_recovery(
        self,
        *,
        limit: int = 5000,
    ) -> List[Any]:
        return await self.list_jobs_for_worker_scan(
            status=JobStatus.RUNNING.value,
            limit=limit,
        )

    async def get_job_scoped(
        self,
        job_id: str,
        tenant_id: Optional[str] = None,
        accessible_device_ids: Optional[list[str]] = None,
    ) -> AnalyticsJob:
        job = await self.get_job(job_id)
        if not self._job_is_visible(job, tenant_id=tenant_id, accessible_device_ids=accessible_device_ids):
            raise JobNotFoundError(f"Job {job_id} not found")
        return job

    async def update_job_status(
        self,
        job_id: str,
        status: JobStatus,
        started_at: Optional[datetime] = None,
        completed_at: Optional[datetime] = None,
        progress: Optional[float] = None,
        message: Optional[str] = None,
        error_message: Optional[str] = None,
        phase: Optional[str] = None,
        phase_label: Optional[str] = None,
        phase_progress: Optional[float] = None,
    ) -> None:

        job = await self.get_job(job_id)

        job.status = status.value

        if started_at:
            job.started_at = started_at
        if completed_at:
            job.completed_at = completed_at
        if progress is not None:
            job.progress = progress
        if message:
            job.message = message
        if error_message:
            job.error_message = error_message
        if phase is not None:
            job.phase = phase
        if phase_label is not None:
            job.phase_label = phase_label
        if phase_progress is not None:
            job.phase_progress = float(max(0.0, min(1.0, phase_progress)))

        await self._session.commit()

        self._logger.debug(
            "job_status_updated",
            job_id=job_id,
            status=status.value,
            progress=progress,
        )

    async def update_job_progress(
        self,
        job_id: str,
        progress: float,
        message: str,
        phase: Optional[str] = None,
        phase_label: Optional[str] = None,
        phase_progress: Optional[float] = None,
    ) -> None:

        job = await self.get_job(job_id)

        job.progress = progress
        job.message = message
        if phase is not None:
            job.phase = phase
        if phase_label is not None:
            job.phase_label = phase_label
        if phase_progress is not None:
            job.phase_progress = float(max(0.0, min(1.0, phase_progress)))

        await self._session.commit()

    async def save_results(
        self,
        job_id: str,
        results: Dict[str, Any],
        accuracy_metrics: Optional[Dict[str, float]],
        execution_time_seconds: int,
    ) -> None:

        job = await self.get_job(job_id)

        job.results = self._sanitize_json(results)
        job.accuracy_metrics = self._sanitize_json(accuracy_metrics)
        job.execution_time_seconds = execution_time_seconds

        await self._session.commit()

        self._logger.info(
            "results_saved",
            job_id=job_id,
            execution_time_seconds=execution_time_seconds,
        )

    async def list_jobs(
        self,
        status: Optional[str] = None,
        device_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        accessible_device_ids: Optional[list[str]] = None,
        job_kinds: Optional[list[str]] = None,
        parent_job_id: Optional[str] = None,
        only_undispatched: Optional[bool] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> List[AnalyticsJob]:
        limit = max(1, int(limit))
        offset = max(0, int(offset))
        query = self._list_jobs_base_query(
            status=status,
            device_id=device_id,
            tenant_id=tenant_id,
            job_kinds=job_kinds,
            parent_job_id=parent_job_id,
            only_undispatched=only_undispatched,
        )

        if accessible_device_ids is None:
            result = await self._session.execute(query.offset(offset).limit(limit))
            return list(result.scalars().all())

        visible_jobs: list[AnalyticsJob] = []
        scanned_matches = 0
        db_offset = 0
        batch_size = max(limit + offset, 100)

        while len(visible_jobs) < limit:
            result = await self._session.execute(query.offset(db_offset).limit(batch_size))
            jobs = list(result.scalars().all())
            if not jobs:
                break

            for job in jobs:
                if not self._job_is_visible(
                    job,
                    tenant_id=tenant_id,
                    accessible_device_ids=accessible_device_ids,
                ):
                    continue

                if scanned_matches < offset:
                    scanned_matches += 1
                    continue

                visible_jobs.append(job)
                if len(visible_jobs) >= limit:
                    break

            db_offset += len(jobs)

        return visible_jobs

    async def update_job_queue_metadata(
        self,
        job_id: str,
        attempt: Optional[int] = None,
        queue_position: Optional[int] = None,
        queue_enqueued_at: Optional[datetime] | object = UNSET,
        queue_dispatched_at: Optional[datetime] | object = UNSET,
        queue_started_at: Optional[datetime] | object = UNSET,
        worker_lease_expires_at: Optional[datetime] | object = UNSET,
        last_heartbeat_at: Optional[datetime] | object = UNSET,
        error_code: Optional[str] | object = UNSET,
    ) -> None:
        job = await self.get_job(job_id)

        if attempt is not None:
            job.attempt = int(attempt)
        if queue_position is not None:
            job.queue_position = int(queue_position)
        if queue_enqueued_at is not UNSET:
            job.queue_enqueued_at = queue_enqueued_at
        if queue_dispatched_at is not UNSET:
            job.queue_dispatched_at = queue_dispatched_at
        if queue_started_at is not UNSET:
            job.queue_started_at = queue_started_at
        if worker_lease_expires_at is not UNSET:
            job.worker_lease_expires_at = worker_lease_expires_at
        if last_heartbeat_at is not UNSET:
            job.last_heartbeat_at = last_heartbeat_at
        if error_code is not UNSET:
            job.error_code = error_code

        await self._session.commit()

    async def claim_child_dispatch(
        self,
        job_id: str,
        dispatched_at: datetime,
        attempt: Optional[int] = None,
    ) -> bool:
        values: dict[str, Any] = {
            "queue_dispatched_at": dispatched_at,
        }
        if attempt is not None:
            values["attempt"] = int(attempt)

        result = await self._session.execute(
            update(AnalyticsJob)
            .where(AnalyticsJob.job_id == job_id)
            .where(AnalyticsJob.status == JobStatus.PENDING.value)
            .where(AnalyticsJob.queue_dispatched_at.is_(None))
            .values(**values)
        )
        await self._session.commit()
        return bool(result.rowcount)

    async def release_child_dispatch(
        self,
        job_id: str,
    ) -> None:
        await self._session.execute(
            update(AnalyticsJob)
            .where(AnalyticsJob.job_id == job_id)
            .where(AnalyticsJob.status == JobStatus.PENDING.value)
            .where(AnalyticsJob.queue_started_at.is_(None))
            .values(queue_dispatched_at=None)
        )
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()

    async def count_jobs(
        self,
        statuses: Optional[list[str]] = None,
        tenant_id: Optional[str] = None,
        attempts_gte: Optional[int] = None,
        job_kinds: Optional[list[str]] = None,
        parent_job_id: Optional[str] = None,
        only_undispatched: Optional[bool] = None,
    ) -> int:
        query = select(func.count()).select_from(AnalyticsJob)

        if statuses:
            query = query.where(AnalyticsJob.status.in_(statuses))
        if job_kinds:
            query = query.where(AnalyticsJob.job_kind.in_(job_kinds))
        if parent_job_id:
            query = query.where(AnalyticsJob.parent_job_id == parent_job_id)
        if only_undispatched is True:
            query = query.where(AnalyticsJob.queue_dispatched_at.is_(None))
        elif only_undispatched is False:
            query = query.where(AnalyticsJob.queue_dispatched_at.is_not(None))
        if tenant_id:
            query = query.where(self._tenant_match_clause(tenant_id))
        if attempts_gte is not None:
            query = query.where(AnalyticsJob.attempt >= int(attempts_gte))

        result = await self._session.execute(query)
        return int(result.scalar() or 0)

    async def list_tenant_job_counts(
        self,
        statuses: Optional[list[str]] = None,
        job_kinds: Optional[list[str]] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        query = (
            select(
                AnalyticsJob.tenant_id.label("tenant_id"),
                func.count().label("job_count"),
            )
            .select_from(AnalyticsJob)
            .where(AnalyticsJob.tenant_id.is_not(None))
            .group_by(AnalyticsJob.tenant_id)
            .order_by(func.count().desc())
            .limit(max(1, int(limit)))
        )

        if statuses:
            query = query.where(AnalyticsJob.status.in_(statuses))
        if job_kinds:
            query = query.where(AnalyticsJob.job_kind.in_(job_kinds))

        result = await self._session.execute(query)
        rows = result.all()
        return [
            {
                "tenant_id": str(row.tenant_id),
                "job_count": int(row.job_count or 0),
            }
            for row in rows
            if row.tenant_id
        ]

    async def list_jobs_for_parent(
        self,
        parent_job_id: str,
    ) -> List[Any]:
        query = (
            select(
                AnalyticsJob.job_id,
                AnalyticsJob.device_id,
                AnalyticsJob.status,
                AnalyticsJob.results,
                AnalyticsJob.error_message,
                AnalyticsJob.message,
            )
            .where(AnalyticsJob.parent_job_id == parent_job_id)
        )
        result = await self._session.execute(query)
        return [
            SimpleNamespace(
                job_id=row.job_id,
                device_id=row.device_id,
                status=row.status,
                results=row.results,
                error_message=row.error_message,
                message=row.message,
            )
            for row in result.all()
        ]

    async def get_model_artifact(
        self,
        tenant_id: Optional[str],
        device_id: str,
        analysis_type: str,
        model_key: str,
    ) -> Optional[Dict[str, Any]]:
        query = (
            select(ModelArtifact)
            .where(ModelArtifact.device_id == device_id)
            .where(ModelArtifact.analysis_type == analysis_type)
            .where(ModelArtifact.model_key == model_key)
            .order_by(ModelArtifact.updated_at.desc())
            .limit(1)
        )
        if tenant_id is not None:
            query = query.where(ModelArtifact.tenant_id == tenant_id)
        result = await self._session.execute(query)
        row = result.scalar_one_or_none()
        if not row:
            return None
        return {
            "feature_schema_hash": row.feature_schema_hash,
            "artifact_payload": row.artifact_payload,
            "model_version": row.model_version,
            "metrics": row.metrics or {},
            "updated_at": row.updated_at,
            "expires_at": row.expires_at,
        }

    async def upsert_model_artifact(
        self,
        tenant_id: Optional[str],
        device_id: str,
        analysis_type: str,
        model_key: str,
        feature_schema_hash: str,
        artifact_payload: bytes,
        model_version: str = "v1",
        metrics: Optional[Dict[str, Any]] = None,
        expires_at: Optional[datetime] = None,
    ) -> None:
        if not artifact_payload:
            return

        query = (
            select(ModelArtifact)
            .where(ModelArtifact.device_id == device_id)
            .where(ModelArtifact.analysis_type == analysis_type)
            .where(ModelArtifact.model_key == model_key)
            .where(ModelArtifact.feature_schema_hash == feature_schema_hash)
            .limit(1)
        )
        if tenant_id is not None:
            query = query.where(ModelArtifact.tenant_id == tenant_id)
        existing = await self._session.execute(query)
        artifact = existing.scalar_one_or_none()
        if artifact is None:
            artifact = ModelArtifact(
                tenant_id=tenant_id,
                device_id=device_id,
                analysis_type=analysis_type,
                model_key=model_key,
                feature_schema_hash=feature_schema_hash,
                model_version=model_version,
                artifact_payload=artifact_payload,
                metrics=self._sanitize_json(metrics),
                expires_at=expires_at,
            )
            self._session.add(artifact)
        else:
            artifact.tenant_id = tenant_id
            artifact.model_version = model_version
            artifact.artifact_payload = artifact_payload
            artifact.metrics = self._sanitize_json(metrics)
            artifact.expires_at = expires_at

        await self._session.commit()

    async def purge_terminal_jobs_older_than(
        self,
        *,
        cutoff: datetime,
        batch_size: int,
    ) -> int:
        candidate_ids = list(
            (
                await self._session.execute(
                    select(AnalyticsJob.id)
                    .where(AnalyticsJob.status.in_([JobStatus.COMPLETED.value, JobStatus.FAILED.value]))
                    .where(func.coalesce(AnalyticsJob.completed_at, AnalyticsJob.updated_at, AnalyticsJob.created_at) < cutoff)
                    .order_by(func.coalesce(AnalyticsJob.completed_at, AnalyticsJob.updated_at, AnalyticsJob.created_at).asc())
                    .limit(max(1, int(batch_size)))
                )
            ).scalars().all()
        )
        if not candidate_ids:
            return 0
        result = await self._session.execute(delete(AnalyticsJob).where(AnalyticsJob.id.in_(candidate_ids)))
        await self._session.commit()
        return int(result.rowcount or 0)

    async def purge_expired_model_artifacts(
        self,
        *,
        now: datetime,
        grace_period_hours: int,
        batch_size: int,
    ) -> int:
        grace_cutoff = now - timedelta(hours=max(0, int(grace_period_hours)))
        candidate_ids = list(
            (
                await self._session.execute(
                    select(ModelArtifact.id)
                    .where(ModelArtifact.expires_at.is_not(None))
                    .where(ModelArtifact.expires_at < grace_cutoff)
                    .order_by(ModelArtifact.expires_at.asc())
                    .limit(max(1, int(batch_size)))
                )
            ).scalars().all()
        )
        if not candidate_ids:
            return 0
        result = await self._session.execute(delete(ModelArtifact).where(ModelArtifact.id.in_(candidate_ids)))
        await self._session.commit()
        return int(result.rowcount or 0)
