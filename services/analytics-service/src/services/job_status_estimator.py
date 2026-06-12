"""History-backed queue and completion estimates for analytics jobs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Iterable

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from src.config.settings import get_settings
from src.models.database import AnalyticsJob, WorkerHeartbeat
from src.models.schemas import JobStatus


@dataclass
class RuntimeEstimate:
    queue_position: int | None
    estimated_wait_seconds: int | None
    estimated_completion_seconds: int | None
    estimate_quality: str | None
    activity_state: str | None = None
    eta_reliable: bool | None = None
    heartbeat_age_seconds: int | None = None


def _is_fleet_parent(job: AnalyticsJob) -> bool:
    if str(getattr(job, "job_kind", "")) == "fleet_parent":
        return True
    params = job.parameters if isinstance(job.parameters, dict) else {}
    return str(job.device_id) == "ALL" or bool(params.get("fleet_mode"))


def _duration(values: Iterable[float]) -> float | None:
    cleaned = [float(v) for v in values if float(v) > 0]
    if not cleaned:
        return None
    return float(median(cleaned))


def _estimate_quality(sample_size: int, spread_ratio: float | None) -> str:
    if sample_size >= 20 and spread_ratio is not None and spread_ratio <= 0.6:
        return "high"
    if sample_size >= 8:
        return "medium"
    return "low"


def _spread_ratio(values: list[float]) -> float | None:
    if len(values) < 4:
        return None
    sorted_vals = sorted(values)
    q1 = sorted_vals[len(sorted_vals) // 4]
    q3 = sorted_vals[(len(sorted_vals) * 3) // 4]
    med = _duration(values)
    if not med or med <= 0:
        return None
    return max(0.0, (q3 - q1) / med)


def _range_days(job: AnalyticsJob) -> float:
    start = getattr(job, "date_range_start", None)
    end = getattr(job, "date_range_end", None)
    if not start or not end:
        return 1.0
    delta = end - start
    return max(1.0 / 24.0, float(delta.total_seconds()) / 86400.0)


class JobStatusEstimator:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def estimate(self, job: AnalyticsJob) -> RuntimeEstimate:
        completed_q = (
            select(
                AnalyticsJob.execution_time_seconds,
                AnalyticsJob.date_range_start,
                AnalyticsJob.date_range_end,
                AnalyticsJob.device_id,
                AnalyticsJob.parameters,
            )
            .where(AnalyticsJob.status == JobStatus.COMPLETED.value)
            .where(AnalyticsJob.analysis_type == job.analysis_type)
            .where(AnalyticsJob.execution_time_seconds.is_not(None))
            .order_by(AnalyticsJob.completed_at.desc())
            .limit(60)
        )
        completed_rows = list((await self._session.execute(completed_q)).all())
        active_workers = await self._active_workers()
        queue_position = await self._queue_position(job)
        return self._estimate_from_completed_rows(
            job,
            completed_rows=completed_rows,
            active_workers=active_workers,
            queue_position=queue_position,
        )

    async def estimate_many(self, jobs: Iterable[AnalyticsJob]) -> dict[str, RuntimeEstimate]:
        job_list = list(jobs)
        if not job_list:
            return {}

        history_rows_by_type = await self._completed_history_rows_by_analysis_type(job_list)
        pending_positions = await self._queue_positions(job_list)
        needs_runtime_context = any(
            getattr(job, "status", None) in {JobStatus.PENDING.value, JobStatus.RUNNING.value}
            for job in job_list
        )
        active_workers = await self._active_workers() if needs_runtime_context else 1

        estimates: dict[str, RuntimeEstimate] = {}
        for job in job_list:
            analysis_type = str(getattr(job, "analysis_type", "") or "")
            completed_rows = history_rows_by_type.get(analysis_type, [])
            estimates[self._job_key(job)] = self._estimate_from_completed_rows(
                job,
                completed_rows=completed_rows,
                active_workers=active_workers,
                queue_position=pending_positions.get(self._job_key(job)),
            )
        return estimates

    def _estimate_from_completed_rows(
        self,
        job: AnalyticsJob,
        *,
        completed_rows: list[object],
        active_workers: int,
        queue_position: int | None,
    ) -> RuntimeEstimate:
        target_is_fleet = _is_fleet_parent(job)
        target_days = _range_days(job)

        durations: list[float] = []
        day_spans: list[float] = []
        for row in completed_rows:
            row_is_fleet = str(row.device_id) == "ALL" or bool((row.parameters or {}).get("fleet_mode"))
            if row_is_fleet != target_is_fleet:
                continue
            duration = float(row.execution_time_seconds or 0)
            if duration <= 0:
                continue
            start = row.date_range_start
            end = row.date_range_end
            span_days = 1.0
            if start is not None and end is not None:
                span_days = max(1.0 / 24.0, float((end - start).total_seconds()) / 86400.0)
            durations.append(duration)
            day_spans.append(span_days)

        sample_size = len(durations)
        baseline = _duration(durations)
        span_baseline = _duration(day_spans) if day_spans else 1.0
        if baseline is None:
            baseline = 45.0 if target_is_fleet else 30.0
            span_baseline = 1.0

        workload_factor = max(0.4, min(4.5, target_days / max(1e-6, float(span_baseline or 1.0))))
        expected_runtime = int(max(10.0, baseline * workload_factor))

        spread = _spread_ratio(durations)
        quality = _estimate_quality(sample_size, spread)

        wait_seconds: int | None = None
        completion_seconds: int | None = None
        activity_state: str | None = None
        eta_reliable: bool | None = None
        heartbeat_age_seconds: int | None = None

        if job.status == JobStatus.PENDING.value:
            if queue_position is None:
                queue_position = 0
            wait_seconds = int(max(0, (queue_position + 1) * expected_runtime / max(1, active_workers)))
            completion_seconds = wait_seconds + expected_runtime
        elif job.status == JobStatus.RUNNING.value:
            activity_state, heartbeat_age_seconds = self._activity_state(job)
            started_at = getattr(job, "started_at", None)
            if started_at is not None:
                elapsed = max(0.0, (self._utc_now() - self._as_utc(started_at)).total_seconds())
                remaining = int(expected_runtime - elapsed)
                if activity_state == "active" and remaining > max(5, int(expected_runtime * 0.1)):
                    completion_seconds = remaining
                    eta_reliable = True
                else:
                    completion_seconds = None
                    eta_reliable = False

        return RuntimeEstimate(
            queue_position=queue_position,
            estimated_wait_seconds=wait_seconds,
            estimated_completion_seconds=completion_seconds,
            estimate_quality=quality,
            activity_state=activity_state,
            eta_reliable=eta_reliable,
            heartbeat_age_seconds=heartbeat_age_seconds,
        )

    async def _completed_history_rows_by_analysis_type(
        self,
        jobs: list[AnalyticsJob],
    ) -> dict[str, list[object]]:
        analysis_types = sorted(
            {
                str(getattr(job, "analysis_type", "") or "").strip()
                for job in jobs
                if str(getattr(job, "analysis_type", "") or "").strip()
            }
        )
        if not analysis_types:
            return {}

        limit = max(60, 60 * len(analysis_types))
        completed_q = (
            select(
                AnalyticsJob.analysis_type,
                AnalyticsJob.execution_time_seconds,
                AnalyticsJob.date_range_start,
                AnalyticsJob.date_range_end,
                AnalyticsJob.device_id,
                AnalyticsJob.parameters,
            )
            .where(AnalyticsJob.status == JobStatus.COMPLETED.value)
            .where(AnalyticsJob.analysis_type.in_(analysis_types))
            .where(AnalyticsJob.execution_time_seconds.is_not(None))
            .order_by(AnalyticsJob.completed_at.desc())
            .limit(limit)
        )
        rows = list((await self._session.execute(completed_q)).all())

        rows_by_type: dict[str, list[object]] = {analysis_type: [] for analysis_type in analysis_types}
        for row in rows:
            analysis_type = str(getattr(row, "analysis_type", "") or "")
            if not analysis_type and len(analysis_types) == 1:
                analysis_type = analysis_types[0]
            bucket = rows_by_type.get(analysis_type)
            if bucket is None or len(bucket) >= 60:
                continue
            bucket.append(row)
        return rows_by_type

    def _activity_state(self, job: AnalyticsJob) -> tuple[str, int | None]:
        now = self._utc_now()
        fresh_cutoff_seconds = max(90, int(get_settings().job_heartbeat_seconds) * 3)
        last_heartbeat_at = getattr(job, "last_heartbeat_at", None)
        worker_lease_expires_at = getattr(job, "worker_lease_expires_at", None)

        if last_heartbeat_at is not None:
            heartbeat_age_seconds = max(0, int((now - self._as_utc(last_heartbeat_at)).total_seconds()))
            if heartbeat_age_seconds <= fresh_cutoff_seconds:
                return "active", heartbeat_age_seconds
            return "stalled", heartbeat_age_seconds

        if worker_lease_expires_at is not None and self._as_utc(worker_lease_expires_at) <= now:
            return "stalled", None

        return "unknown", None

    async def _active_workers(self) -> int:
        cutoff = self._utc_now() - timedelta(seconds=120)
        q = (
            select(func.count())
            .select_from(WorkerHeartbeat)
            .where(WorkerHeartbeat.last_heartbeat_at >= cutoff)
        )
        row = await self._session.execute(q)
        return max(1, int(row.scalar() or 0))

    async def _queue_position(self, job: AnalyticsJob) -> int | None:
        if job.status != JobStatus.PENDING.value:
            return None
        q = (
            select(func.count())
            .select_from(AnalyticsJob)
            .where(AnalyticsJob.status == JobStatus.PENDING.value)
            .where(AnalyticsJob.created_at < job.created_at)
        )
        result = await self._session.execute(q)
        return max(0, int(result.scalar() or 0))

    async def _queue_positions(self, jobs: Iterable[AnalyticsJob]) -> dict[str, int]:
        pending_jobs = [job for job in jobs if getattr(job, "status", None) == JobStatus.PENDING.value]
        if not pending_jobs:
            return {}

        target = aliased(AnalyticsJob)
        older = aliased(AnalyticsJob)
        pending_job_ids = [self._job_key(job) for job in pending_jobs]
        q = (
            select(target.job_id, func.count(older.id))
            .select_from(target)
            .outerjoin(
                older,
                and_(
                    older.status == JobStatus.PENDING.value,
                    older.created_at < target.created_at,
                ),
            )
            .where(target.status == JobStatus.PENDING.value)
            .where(target.job_id.in_(pending_job_ids))
            .group_by(target.job_id)
        )
        result = await self._session.execute(q)
        return {
            str(job_id): max(0, int(count or 0))
            for job_id, count in result.all()
        }

    @staticmethod
    def _job_key(job: AnalyticsJob) -> str:
        return str(getattr(job, "job_id", ""))

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
