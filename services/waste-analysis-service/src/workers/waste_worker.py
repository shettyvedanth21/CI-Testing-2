from __future__ import annotations

import asyncio
import json
import logging
import socket
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select, update

from src.config import settings
from src.database import AsyncSessionLocal
from src.models import WasteAnalysisJob, WasteWorkerHeartbeat, WasteStatus
from src.queue import WasteJob, WasteQueue, get_waste_queue
from src.repositories.waste_repository import WasteRepository
from src.tasks.waste_task import run_waste_analysis
from services.shared.tenant_context import TenantContext, normalize_tenant_id


logger = logging.getLogger(__name__)

_REDIS_COUNTER_KEYS = {
    "retry": "counters:waste_retry_events",
    "timeout": "counters:waste_timeout_events",
    "dead_letter": "counters:waste_dead_letter_events",
}


class WasteWorker:
    def __init__(self, queue: WasteQueue | None = None, concurrency: int | None = None) -> None:
        self._queue = queue or get_waste_queue()
        self._concurrency = max(1, concurrency or settings.WASTE_WORKER_CONCURRENCY)
        self._worker_id = f"{settings.WASTE_QUEUE_CONSUMER_NAME}-{socket.gethostname()}"
        self._tasks: list[asyncio.Task] = []
        self._stopping = False
        self._heartbeat_task: asyncio.Task | None = None
        self._semaphore: asyncio.Semaphore | None = None
        self._metrics_redis = None

    async def _init_metrics_redis(self) -> None:
        if self._metrics_redis is not None or not settings.REDIS_URL:
            return
        try:
            from redis.asyncio import Redis as AIORedis
            self._metrics_redis = AIORedis.from_url(settings.REDIS_URL, decode_responses=True)
        except Exception:
            logger.debug("waste_worker_metrics_redis_unavailable")

    async def _incr_event(self, event: str) -> None:
        key = _REDIS_COUNTER_KEYS.get(event)
        if key is None or self._metrics_redis is None:
            return
        try:
            await self._metrics_redis.incr(key)
        except Exception:
            pass

    async def start(self) -> None:
        await self._init_metrics_redis()
        logger.info("waste_worker_started concurrency=%s worker_id=%s", self._concurrency, self._worker_id)
        self._semaphore = asyncio.Semaphore(self._concurrency)
        await self._recover_stale_running_jobs()
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        self._tasks = [asyncio.create_task(self._worker_loop(slot)) for slot in range(self._concurrency)]
        await asyncio.gather(*self._tasks)

    async def stop(self) -> None:
        self._stopping = True
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        for task in self._tasks:
            task.cancel()
        if self._heartbeat_task:
            await asyncio.gather(self._heartbeat_task, return_exceptions=True)
            self._heartbeat_task = None
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        if self._metrics_redis is not None:
            try:
                await self._metrics_redis.close()
            except Exception:
                pass
            self._metrics_redis = None

    async def _heartbeat_loop(self) -> None:
        while not self._stopping:
            try:
                async with AsyncSessionLocal() as db:
                    row = await db.get(WasteWorkerHeartbeat, self._worker_id)
                    now = datetime.now(timezone.utc)
                    if row is None:
                        row = WasteWorkerHeartbeat(
                            worker_id=self._worker_id,
                            app_role="worker",
                            last_heartbeat_at=now,
                            status="alive",
                        )
                        db.add(row)
                    else:
                        row.last_heartbeat_at = now
                        row.status = "alive"
                    await db.commit()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("waste_worker_heartbeat_failed worker_id=%s", self._worker_id)
            await asyncio.sleep(max(5, settings.WASTE_WORKER_HEARTBEAT_SECONDS))

    async def _worker_loop(self, slot: int) -> None:
        while not self._stopping:
            job = await self._queue.get_job()
            if job is None:
                continue
            try:
                async with self._semaphore:
                    await self._process_job(job, slot)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("waste_worker_loop_error job_id=%s slot=%s", job.job_id, slot)

    async def _process_job(self, job: WasteJob, slot: int) -> None:
        worker_slot_id = f"{self._worker_id}-{slot}"
        claimed = await self._claim_job(job.job_id, worker_slot_id)
        if not claimed:
            await self._queue.ack(job)
            return

        params = json.loads(job.params_json)
        tenant_id = normalize_tenant_id(params.get("tenant_id"))
        if tenant_id is None:
            await self._mark_job_failed(job.job_id, "TENANT_SCOPE_REQUIRED", "Tenant scope is required for waste-analysis execution")
            await self._queue.dead_letter(job, "Tenant scope is required")
            return

        ctx = TenantContext(
            tenant_id=tenant_id,
            user_id="svc:waste-analysis-worker",
            role="internal_service",
            plant_ids=[],
            is_super_admin=False,
        )

        heartbeat = asyncio.create_task(self._job_heartbeat_loop(job.job_id))
        try:
            await asyncio.wait_for(
                run_waste_analysis(job.job_id, params),
                timeout=max(1, settings.WASTE_JOB_TIMEOUT_SECONDS),
            )
        except asyncio.TimeoutError:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass
            await self._retry_or_fail(
                job,
                error_code="JOB_TIMEOUT",
                error_message=f"Waste analysis exceeded timeout ({settings.WASTE_JOB_TIMEOUT_SECONDS}s)",
                increment_timeout=True,
            )
            return
        except Exception as exc:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass
            await self._retry_or_fail(
                job,
                error_code="WORKER_ERROR",
                error_message=str(exc),
            )
            return
        finally:
            await self._clear_job_lease(job.job_id)

        heartbeat.cancel()
        try:
            await heartbeat
        except asyncio.CancelledError:
            pass

        async with AsyncSessionLocal() as db:
            repo = WasteRepository(db, ctx)
            updated = await repo.get_job(job.job_id)
            if updated is None:
                await self._queue.ack(job)
                return
            status = updated.status.value if hasattr(updated.status, "value") else str(updated.status)
            if status in ("completed", "failed"):
                if status == "failed" and (updated.error_code or "") in {"INTERNAL_ERROR", "JOB_TIMEOUT", "WORKER_ERROR"}:
                    await self._retry_or_fail(
                        job,
                        error_code=updated.error_code or "INTERNAL_ERROR",
                        error_message=updated.error_message or "Waste analysis failed",
                        increment_timeout=(updated.error_code == "JOB_TIMEOUT"),
                    )
                    return
                await self._queue.ack(job)
                return

            await self._retry_or_fail(
                job,
                error_code="WORKER_INCOMPLETE",
                error_message="Worker finished without terminal job state",
            )

    async def _claim_job(self, job_id: str, worker_slot_id: str) -> bool:
        now = datetime.now(timezone.utc)
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                update(WasteAnalysisJob)
                .where(WasteAnalysisJob.id == job_id)
                .where(
                    or_(
                        WasteAnalysisJob.status == WasteStatus.pending,
                        (WasteAnalysisJob.status == WasteStatus.running)
                        & or_(
                            WasteAnalysisJob.worker_lease_expires_at.is_(None),
                            WasteAnalysisJob.worker_lease_expires_at <= now,
                        ),
                    )
                )
                .values(
                    status=WasteStatus.running,
                    started_at=now,
                    worker_id=worker_slot_id,
                    processing_started_at=now,
                    worker_lease_expires_at=now + timedelta(seconds=settings.WASTE_WORKER_LEASE_SECONDS),
                    last_heartbeat_at=now,
                )
            )
            await db.commit()
            return bool(result.rowcount)

    async def _job_heartbeat_loop(self, job_id: str) -> None:
        while not self._stopping:
            await asyncio.sleep(max(5, settings.WASTE_WORKER_HEARTBEAT_SECONDS))
            now = datetime.now(timezone.utc)
            try:
                async with AsyncSessionLocal() as db:
                    await db.execute(
                        update(WasteAnalysisJob)
                        .where(WasteAnalysisJob.id == job_id)
                        .values(
                            last_heartbeat_at=now,
                            worker_lease_expires_at=now + timedelta(seconds=settings.WASTE_WORKER_LEASE_SECONDS),
                        )
                    )
                    await db.commit()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("waste_job_heartbeat_failed job_id=%s", job_id)

    async def _clear_job_lease(self, job_id: str) -> None:
        try:
            async with AsyncSessionLocal() as db:
                await db.execute(
                    update(WasteAnalysisJob)
                    .where(WasteAnalysisJob.id == job_id)
                    .values(worker_lease_expires_at=None)
                )
                await db.commit()
        except Exception:
            logger.warning("waste_clear_lease_failed job_id=%s", job_id)

    async def _mark_job_failed(self, job_id: str, error_code: str, error_message: str) -> None:
        now = datetime.now(timezone.utc)
        async with AsyncSessionLocal() as db:
            await db.execute(
                update(WasteAnalysisJob)
                .where(WasteAnalysisJob.id == job_id)
                .values(
                    status=WasteStatus.failed,
                    error_code=error_code,
                    error_message=error_message,
                    progress_pct=100,
                    stage="Failed",
                    completed_at=now,
                    worker_lease_expires_at=None,
                )
            )
            await db.commit()

    async def _retry_or_fail(
        self,
        job: WasteJob,
        *,
        error_code: str,
        error_message: str,
        increment_timeout: bool = False,
    ) -> None:
        async with AsyncSessionLocal() as db:
            waste_job = await db.get(WasteAnalysisJob, job.job_id)
            current_retry_count = int(getattr(waste_job, "retry_count", 0) or 0) if waste_job else 0
            next_attempt = current_retry_count + 1
            if next_attempt >= settings.WASTE_JOB_MAX_RETRIES:
                now = datetime.now(timezone.utc)
                await db.execute(
                    update(WasteAnalysisJob)
                    .where(WasteAnalysisJob.id == job.job_id)
                    .values(
                        status=WasteStatus.failed,
                        error_code=error_code,
                        error_message=error_message,
                        progress_pct=100,
                        stage="Failed",
                        completed_at=now,
                        retry_count=next_attempt,
                        timeout_count=(int(getattr(waste_job, "timeout_count", 0) or 0) + (1 if increment_timeout else 0)) if waste_job else (1 if increment_timeout else 0),
                        worker_lease_expires_at=None,
                    )
                )
                await db.commit()
                await self._incr_event("dead_letter")
                if increment_timeout:
                    await self._incr_event("timeout")
                await self._queue.dead_letter(job, error_message)
                return

            now = datetime.now(timezone.utc)
            await db.execute(
                update(WasteAnalysisJob)
                .where(WasteAnalysisJob.id == job.job_id)
                .values(
                    status=WasteStatus.pending,
                    error_code=error_code,
                    error_message=f"Retrying ({next_attempt}/{settings.WASTE_JOB_MAX_RETRIES}): {error_message}",
                    progress_pct=0,
                    stage="Queued",
                    started_at=None,
                    worker_id=None,
                    processing_started_at=None,
                    worker_lease_expires_at=None,
                    retry_count=next_attempt,
                    timeout_count=(int(getattr(waste_job, "timeout_count", 0) or 0) + (1 if increment_timeout else 0)) if waste_job else (1 if increment_timeout else 0),
                )
            )
            await db.commit()

        await self._incr_event("retry")
        if increment_timeout:
            await self._incr_event("timeout")
        backoff = min(30, 2 ** (job.attempt - 1))
        await asyncio.sleep(backoff)
        await self._queue.enqueue(
            WasteJob(
                job_id=job.job_id,
                tenant_id=job.tenant_id,
                params_json=job.params_json,
                attempt=job.attempt + 1,
            ),
        )
        await self._queue.ack(job)

    async def _recover_stale_running_jobs(self) -> None:
        now = datetime.now(timezone.utc)
        async with AsyncSessionLocal() as db:
            stmt = (
                select(WasteAnalysisJob)
                .where(WasteAnalysisJob.status.in_([WasteStatus.running, WasteStatus.pending, WasteStatus.enqueue_failed]))
                .where(WasteAnalysisJob.created_at < now - timedelta(minutes=10))
            )
            result = await db.execute(stmt)
            stale_jobs = list(result.scalars().all())

            for job in stale_jobs:
                lease = getattr(job, "worker_lease_expires_at", None)
                if lease is not None and lease.tzinfo is None:
                    lease = lease.replace(tzinfo=timezone.utc)
                is_stale = lease is None or lease <= now
                if not is_stale:
                    continue

                next_attempt = int(getattr(job, "retry_count", 0) or 0) + 1
                if next_attempt >= settings.WASTE_JOB_MAX_RETRIES:
                    await db.execute(
                        update(WasteAnalysisJob)
                        .where(WasteAnalysisJob.id == job.id)
                        .values(
                            status=WasteStatus.failed,
                            error_code="STALE_WORKER_LEASE",
                            error_message="Job failed after stale worker recovery attempts exhausted",
                            progress_pct=100,
                            stage="Failed",
                            completed_at=now,
                            worker_lease_expires_at=None,
                        )
                    )
                    logger.error("stale_waste_job_failed job_id=%s attempt=%s", job.id, next_attempt)
                    continue

                params = {
                    "tenant_id": job.tenant_id,
                    "scope": job.scope.value if hasattr(job.scope, "value") else str(job.scope),
                    "device_ids": job.device_ids,
                    "start_date": job.start_date.isoformat() if job.start_date else None,
                    "end_date": job.end_date.isoformat() if job.end_date else None,
                    "granularity": job.granularity.value if hasattr(job.granularity, "value") else str(job.granularity),
                }

                await db.execute(
                    update(WasteAnalysisJob)
                    .where(WasteAnalysisJob.id == job.id)
                    .values(
                        status=WasteStatus.pending,
                        progress_pct=0,
                        stage="Queued",
                        started_at=None,
                        worker_id=None,
                        processing_started_at=None,
                        worker_lease_expires_at=None,
                        error_code="STALE_WORKER_LEASE",
                        error_message=f"Requeued after stale worker lease (attempt {next_attempt}/{settings.WASTE_JOB_MAX_RETRIES})",
                        retry_count=next_attempt,
                    )
                )

                await self._queue.enqueue(
                    WasteJob(
                        job_id=job.id,
                        tenant_id=job.tenant_id,
                        params_json=json.dumps(params, separators=(",", ":"), sort_keys=True),
                        attempt=next_attempt,
                    ),
                )
                logger.warning("stale_waste_job_requeued job_id=%s attempt=%s", job.id, next_attempt)

            await db.commit()
