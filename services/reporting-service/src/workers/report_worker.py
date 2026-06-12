from __future__ import annotations

import asyncio
import logging
import socket
from datetime import datetime, timedelta, timezone

from src.config import settings
from src.database import AsyncSessionLocal
from src.models.energy_reports import ReportWorkerHeartbeat
from src.queue import ReportJob, ReportQueue, get_report_queue
from src.repositories.report_repository import ReportRepository
from src.services.report_executor import execute_report


logger = logging.getLogger(__name__)

_RETRY_BACKOFF_BASE = 30
_RETRY_BACKOFF_MAX = 300

_REDIS_COUNTER_KEYS = {
    "retry": "counters:reporting_retry_events",
    "timeout": "counters:reporting_timeout_events",
    "dead_letter": "counters:reporting_dead_letter_events",
}


class ReportWorker:
    def __init__(self, queue: ReportQueue | None = None, concurrency: int | None = None) -> None:
        self._queue = queue or get_report_queue()
        self._concurrency = max(1, concurrency or settings.REPORT_WORKER_CONCURRENCY)
        self._worker_id = f"{settings.REPORT_QUEUE_CONSUMER_NAME}-{socket.gethostname()}"
        self._tasks: list[asyncio.Task] = []
        self._stopping = False
        self._heartbeat_task: asyncio.Task | None = None
        self._metrics_redis = None

    async def _init_metrics_redis(self) -> None:
        if self._metrics_redis is not None or not settings.REDIS_URL:
            return
        try:
            from redis.asyncio import Redis as AIORedis
            self._metrics_redis = AIORedis.from_url(settings.REDIS_URL, decode_responses=True)
        except Exception:
            logger.debug("report_worker_metrics_redis_unavailable")

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
        logger.info("report_worker_started", extra={"concurrency": self._concurrency, "worker_id": self._worker_id})
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
                    row = await db.get(ReportWorkerHeartbeat, self._worker_id)
                    now = datetime.now(timezone.utc)
                    if row is None:
                        row = ReportWorkerHeartbeat(
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
                logger.exception("report_worker_heartbeat_failed", extra={"worker_id": self._worker_id})
            await asyncio.sleep(max(5, settings.REPORT_WORKER_HEARTBEAT_SECONDS))

    async def _worker_loop(self, slot: int) -> None:
        while not self._stopping:
            job = await self._queue.get_job()
            if job is None:
                continue
            try:
                await self._process_job(job, slot)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("report_worker_loop_error", extra={"report_id": job.report_id, "slot": slot})

    async def _process_job(self, job: ReportJob, slot: int) -> None:
        async with AsyncSessionLocal() as db:
            repo = ReportRepository(db)
            claimed = await repo.claim_report_for_processing(
                job.report_id,
                worker_id=f"{self._worker_id}-{slot}",
                tenant_id=job.tenant_id,
                stale_after=timedelta(seconds=max(settings.REPORT_JOB_TIMEOUT_SECONDS, 1)),
            )
            if not claimed:
                await self._queue.ack(job)
                return

            report = await repo.load_report_for_worker(job.report_id, tenant_id=job.tenant_id)
            if report is None:
                await self._queue.ack(job)
                return
            params = report.params or {}

        try:
            await asyncio.wait_for(
                execute_report(job.report_id, job.report_type, params),
                timeout=max(1, settings.REPORT_JOB_TIMEOUT_SECONDS),
            )
        except asyncio.TimeoutError:
            await self._retry_or_fail(
                job,
                error_code="JOB_TIMEOUT",
                error_message=f"Report exceeded timeout ({settings.REPORT_JOB_TIMEOUT_SECONDS}s)",
                increment_timeout=True,
            )
            return
        except Exception as exc:
            await self._retry_or_fail(
                job,
                error_code="WORKER_ERROR",
                error_message=str(exc),
            )
            return

        async with AsyncSessionLocal() as db:
            repo = ReportRepository(db)
            report = await repo.load_report_for_worker(job.report_id, tenant_id=job.tenant_id)
            if report is None:
                await self._queue.ack(job)
                return
            status = report.status.value if hasattr(report.status, "value") else str(report.status)
            if status == "completed":
                await repo.clear_processing_claim(job.report_id, tenant_id=job.tenant_id)
                await self._queue.ack(job)
                return
            if status == "failed":
                should_retry = (report.error_code or "") in {"INTERNAL_ERROR", "JOB_TIMEOUT", "WORKER_ERROR"}
                if should_retry:
                    await self._retry_or_fail(
                        job,
                        error_code=report.error_code or "INTERNAL_ERROR",
                        error_message=report.error_message or "Report execution failed",
                        increment_timeout=(report.error_code == "JOB_TIMEOUT"),
                    )
                    return
                await repo.clear_processing_claim(job.report_id, tenant_id=job.tenant_id)
                await self._queue.ack(job)
                return

            await self._retry_or_fail(
                job,
                error_code="WORKER_INCOMPLETE",
                error_message="Worker finished without terminal report state",
            )

    async def _retry_or_fail(
        self,
        job: ReportJob,
        *,
        error_code: str,
        error_message: str,
        increment_timeout: bool = False,
    ) -> None:
        async with AsyncSessionLocal() as db:
            repo = ReportRepository(db)
            report = await repo.load_report_for_worker(job.report_id, tenant_id=job.tenant_id)
            current_retry_count = int(getattr(report, "retry_count", 0) or 0) if report else 0
            next_attempt = current_retry_count + 1
            if next_attempt >= settings.REPORT_JOB_MAX_RETRIES:
                await repo.fail_report(
                    job.report_id,
                    tenant_id=job.tenant_id,
                    error_code=error_code,
                    error_message=error_message,
                    increment_retry=True,
                    increment_timeout=increment_timeout,
                )
                await self._incr_event("dead_letter")
                if increment_timeout:
                    await self._incr_event("timeout")
                await self._queue.dead_letter(job, error_message)
                return

            await repo.requeue_report(
                job.report_id,
                tenant_id=job.tenant_id,
                error_code=error_code,
                error_message=error_message,
                increment_retry=True,
                increment_timeout=increment_timeout,
            )
        await self._incr_event("retry")
        if increment_timeout:
            await self._incr_event("timeout")
        backoff = min(_RETRY_BACKOFF_BASE * (2 ** (job.attempt - 1)), _RETRY_BACKOFF_MAX)
        await asyncio.sleep(backoff)
        await self._queue.enqueue(
            ReportJob(
                report_id=job.report_id,
                tenant_id=job.tenant_id,
                report_type=job.report_type,
                attempt=job.attempt + 1,
            ),
        )
        await self._queue.ack(job)
