"""Job worker for processing analytics jobs."""

import asyncio
import json
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional, Any
import structlog
import socket
from uuid import uuid4
from sqlalchemy import update

from src.config.settings import get_settings
from src.infrastructure.database import (
    async_session_maker,
    is_transient_disconnect,
    reset_db_connections,
)
from src.infrastructure.mysql_repository import MySQLResultRepository
from src.infrastructure.s3_client import S3Client
from src.models.database import AnalyticsJob, WorkerHeartbeat
from src.services.dataset_service import DatasetService
from src.services.job_runner import JobRunner
from src.models.schemas import AnalyticsRequest, AnalyticsType, FleetAnalyticsRequest, JobStatus
from src.services.progress_tracking import FLEET_PARENT_PHASES
from src.services.readiness_orchestrator import ensure_device_ready
from src.services.result_formatter import ResultFormatter
from src.utils.exceptions import AnalyticsError, DatasetNotFoundError
from src.workers.job_queue import Job, QueueBackend
from services.shared.job_context import BoundJobPayload
from services.shared.telemetry_coverage import build_device_coverage_result
from services.shared.tenant_context import TenantContext

logger = structlog.get_logger()

_REDIS_COUNTER_KEYS = {
    "retry": "counters:analytics_retry_events",
    "dead_letter": "counters:analytics_dead_letter_events",
}


def _normalize_failure_code(error_code: Optional[str], error_message: str) -> str:
    normalized_code = str(error_code or "").strip().upper()
    normalized_message = (error_message or "").lower()

    if normalized_code in {
        "NO_TELEMETRY_IN_RANGE",
        "DEVICE_NOT_FOUND",
        "TENANT_SCOPE_REQUIRED",
        "DATASET_NOT_READY_TIMEOUT",
        "JOB_EXECUTION_TIMEOUT",
        "STALE_WORKER_LEASE",
    }:
        return normalized_code

    if "no_telemetry_in_range" in normalized_message:
        return "NO_TELEMETRY_IN_RANGE"
    if "device_not_found" in normalized_message:
        return "DEVICE_NOT_FOUND"
    if "tenant_scope_required" in normalized_message:
        return "TENANT_SCOPE_REQUIRED"
    if "dataset_not_ready_timeout" in normalized_message or "export_timeout" in normalized_message:
        return "DATASET_NOT_READY_TIMEOUT"
    if "job execution exceeded timeout" in normalized_message:
        return "JOB_EXECUTION_TIMEOUT"
    if "stale_worker_lease" in normalized_message:
        return "STALE_WORKER_LEASE"
    return normalized_code or "UNEXPECTED_ERROR"


def _child_coverage_result(child: Any) -> dict[str, Any]:
    payload = getattr(child, "results", None) or {}
    if not isinstance(payload, dict):
        return {}
    coverage = payload.get("coverage_result")
    return coverage if isinstance(coverage, dict) else {}


class JobWorker:
    """Worker that processes analytics jobs from the queue."""

    def __init__(
        self,
        job_queue: QueueBackend,
        max_concurrent: int = 3,
    ):
        settings = get_settings()
        self._queue = job_queue
        self._max_concurrent = max(1, max_concurrent)
        self._running = False
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._logger = logger.bind(worker="JobWorker")
        self._current_tasks: set = set()
        self._lease_seconds = settings.job_lease_seconds
        self._heartbeat_seconds = settings.job_heartbeat_seconds
        self._job_timeout_seconds = max(1, int(settings.job_timeout_seconds))
        self._max_attempts = settings.queue_max_attempts
        self._stale_scan_interval_seconds = max(5, int(settings.stale_scan_interval_seconds))
        self._worker_id = settings.redis_consumer_name or f"worker-{socket.gethostname()}"
        self._worker_heartbeat_task: Optional[asyncio.Task] = None
        self._orchestration_task: Optional[asyncio.Task] = None
        self._fleet_parent_max_active_children = max(1, int(settings.fleet_parent_max_active_children))
        self._fleet_dispatch_scan_limit = max(1, int(settings.fleet_dispatch_scan_limit))
        self._orchestration_poll_seconds = max(1, int(settings.fleet_orchestration_poll_seconds))
        self._system_ctx = TenantContext(
            tenant_id=None,
            user_id="analytics-worker",
            role="super_admin",
            plant_ids=[],
            is_super_admin=True,
        )
        self._metrics_redis = None

    async def _init_metrics_redis(self) -> None:
        if self._metrics_redis is not None:
            return
        settings = get_settings()
        if not getattr(settings, "redis_url", None):
            return
        try:
            from redis.asyncio import Redis as AIORedis
            self._metrics_redis = AIORedis.from_url(settings.redis_url, decode_responses=True)
        except Exception:
            self._logger.debug("job_worker_metrics_redis_unavailable")

    async def _incr_event(self, event: str) -> None:
        key = _REDIS_COUNTER_KEYS.get(event)
        if key is None or self._metrics_redis is None:
            return
        try:
            await self._metrics_redis.incr(key)
        except Exception:
            pass

    @staticmethod
    def _is_fleet_parent_job(job_row: Any) -> bool:
        try:
            if str(getattr(job_row, "device_id", "")) == "ALL":
                return True
            params = getattr(job_row, "parameters", None) or {}
            return bool(params.get("fleet_mode"))
        except Exception:
            return False

    @staticmethod
    def _is_fleet_parent_request(request: AnalyticsRequest) -> bool:
        try:
            if str(getattr(request, "device_id", "")) == "ALL":
                return True
            params = getattr(request, "parameters", None) or {}
            return bool(params.get("fleet_mode"))
        except Exception:
            return False

    @staticmethod
    def _default_model_for(analysis_type: str) -> str:
        if analysis_type == AnalyticsType.ANOMALY.value:
            return "anomaly_ensemble"
        return "failure_ensemble"

    @staticmethod
    def _phase_progress_to_absolute(phase: str, phase_progress: float) -> float:
        phase_window = FLEET_PARENT_PHASES.get(phase)
        if phase_window is None:
            return 0.0
        bounded = max(0.0, min(1.0, phase_progress))
        return phase_window.start + (phase_window.end - phase_window.start) * bounded

    async def _orchestration_loop(self) -> None:
        next_stale_scan = datetime.now(timezone.utc)
        while self._running:
            try:
                now = datetime.now(timezone.utc)
                if now >= next_stale_scan:
                    await self._recover_stale_running_jobs()
                    next_stale_scan = now + timedelta(seconds=self._stale_scan_interval_seconds)
                await self._dispatch_pending_child_jobs()
                await self._reconcile_active_fleet_parents()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                if is_transient_disconnect(exc):
                    await reset_db_connections()
                    self._logger.info("fleet_orchestration_waiting_for_db_reconnect", error=str(exc))
                else:
                    self._logger.error("fleet_orchestration_error", error=str(exc), exc_info=True)
            await asyncio.sleep(self._orchestration_poll_seconds)

    async def start(self) -> None:
        """Start the job worker."""
        self._running = True
        self._semaphore = asyncio.Semaphore(self._max_concurrent)

        await self._init_metrics_redis()

        self._logger.info(
            "worker_started",
            max_concurrent=self._max_concurrent,
        )
        self._worker_heartbeat_task = asyncio.create_task(self._worker_heartbeat_loop())
        self._orchestration_task = asyncio.create_task(self._orchestration_loop())
        try:
            await self._recover_stale_running_jobs()
        except Exception as exc:
            if is_transient_disconnect(exc):
                await reset_db_connections()
                self._logger.info("worker_startup_waiting_for_db_reconnect", error=str(exc))
            else:
                raise

        while self._running:
            try:
                job = await self._queue.get_job()
                if job is None:
                    await asyncio.sleep(0.1)
                    continue

                task = asyncio.create_task(
                    self._process_job_with_semaphore(job)
                )
                self._current_tasks.add(task)
                task.add_done_callback(self._current_tasks.discard)

            except asyncio.CancelledError:
                self._logger.info("worker_cancelled")
                break
            except Exception as e:
                if is_transient_disconnect(e):
                    await reset_db_connections()
                    self._logger.info("worker_waiting_for_db_reconnect", error=str(e))
                else:
                    self._logger.error("worker_error", error=str(e))
                await asyncio.sleep(1)

    async def _recover_stale_running_jobs(self) -> None:
        """Requeue jobs left in running state when worker lease is stale/missing."""
        now = datetime.now(timezone.utc)
        async with async_session_maker() as session:
            repo = MySQLResultRepository(session, self._system_ctx)
            running_jobs = await repo.list_running_jobs_for_recovery(limit=5000)

            for job in running_jobs:
                lease = getattr(job, "worker_lease_expires_at", None)
                if lease is not None and lease.tzinfo is None:
                    lease = lease.replace(tzinfo=timezone.utc)
                if (
                    str(getattr(job, "job_kind", "")) == "fleet_parent"
                    and lease is None
                    and str(getattr(job, "phase", "")) in {"child_execution", "aggregation"}
                ):
                    continue
                is_stale = lease is None or lease <= now
                if not is_stale:
                    continue

                next_attempt = int(getattr(job, "attempt", 0) or 0) + 1
                if next_attempt > self._max_attempts:
                    await repo.update_job_status(
                        job_id=job.job_id,
                        status=JobStatus.FAILED,
                        completed_at=now,
                        message="Job failed after stale worker recovery attempts exhausted",
                        error_message="STALE_WORKER_LEASE",
                        phase="failed",
                        phase_label="Failed",
                        phase_progress=1.0,
                    )
                    await repo.update_job_queue_metadata(
                        job_id=job.job_id,
                        error_code="STALE_WORKER_LEASE",
                        worker_lease_expires_at=None,
                    )
                    self._logger.error(
                        "stale_running_job_failed",
                        job_id=job.job_id,
                        attempt=next_attempt,
                    )
                    continue

                raw_payload = self._build_requeue_payload(job)
                await self._queue.submit_job(
                    job_id=job.job_id,
                    raw_payload=raw_payload,
                    attempt=next_attempt,
                )
                await repo.update_job_status(
                    job_id=job.job_id,
                    status=JobStatus.PENDING,
                    progress=0.0,
                    message=f"Requeued after stale worker lease (attempt {next_attempt}/{self._max_attempts})",
                    error_message=None,
                    phase="queued",
                    phase_label="Queued",
                    phase_progress=0.0,
                )
                await repo.update_job_queue_metadata(
                    job_id=job.job_id,
                    attempt=next_attempt,
                    queue_enqueued_at=now,
                    queue_dispatched_at=now,
                    queue_started_at=None,
                    worker_lease_expires_at=None,
                    error_code="STALE_WORKER_LEASE",
                )
                self._logger.warning(
                    "stale_running_job_requeued",
                    job_id=job.job_id,
                    attempt=next_attempt,
                )

    def _build_requeue_payload(self, job_row: Any) -> str:
        params = getattr(job_row, "parameters", None) or {}
        tenant_id_value = params.get("tenant_id")
        if self._is_fleet_parent_job(job_row):
            payload = FleetAnalyticsRequest(
                device_ids=[str(device_id) for device_id in params.get("device_ids", [])],
                start_time=job_row.date_range_start,
                end_time=job_row.date_range_end,
                analysis_type=str(job_row.analysis_type),
                model_name=job_row.model_name,
                parameters=params,
            ).model_dump(mode="json")
            job_type = "fleet_parent_analytics"
            device_id = "ALL"
        else:
            payload = AnalyticsRequest(
                device_id=job_row.device_id,
                analysis_type=job_row.analysis_type,
                model_name=job_row.model_name,
                start_time=job_row.date_range_start,
                end_time=job_row.date_range_end,
                parameters=params,
            ).model_dump(mode="json")
            job_type = "analytics"
            device_id = job_row.device_id

        return json.dumps(
            BoundJobPayload(
                job_type=job_type,
                tenant_id=tenant_id_value,
                device_id=device_id,
                initiated_by_user_id="analytics-worker",
                initiated_by_role="super_admin",
                payload=payload,
            ).__dict__,
            separators=(",", ":"),
            sort_keys=True,
            default=str,
        )

    async def _dispatch_pending_child_jobs(self) -> None:
        async with async_session_maker() as session:
            repo = MySQLResultRepository(session, self._system_ctx)
            running_rows = await repo.list_jobs_for_worker_scan(
                status=JobStatus.RUNNING.value,
                job_kinds=["single", "fleet_child"],
                limit=5000,
            )
            reserved_rows = await repo.list_jobs_for_worker_scan(
                status=JobStatus.PENDING.value,
                job_kinds=["single", "fleet_child"],
                only_undispatched=False,
                limit=5000,
            )
            occupied_rows = list(running_rows) + list(reserved_rows)
            global_headroom = max(0, int(get_settings().global_active_job_limit) - len(occupied_rows))
            if global_headroom <= 0:
                return

            candidates = await repo.list_jobs_for_worker_scan(
                status=JobStatus.PENDING.value,
                job_kinds=["fleet_child"],
                only_undispatched=True,
                limit=self._fleet_dispatch_scan_limit,
            )

        selected = self._select_child_jobs_for_dispatch(candidates, occupied_rows, global_headroom)
        if not selected:
            return

        dispatch_time = datetime.now(timezone.utc)
        for row in selected:
            async with async_session_maker() as session:
                repo = MySQLResultRepository(session, self._system_ctx)
                claimed = await repo.claim_child_dispatch(
                    job_id=row.job_id,
                    dispatched_at=dispatch_time,
                    attempt=max(1, int(getattr(row, "attempt", 0) or 1)),
                )
                if not claimed:
                    continue
            try:
                await self._queue.submit_job(
                    job_id=row.job_id,
                    raw_payload=self._build_requeue_payload(row),
                    attempt=max(1, int(getattr(row, "attempt", 0) or 1)),
                )
            except Exception:
                async with async_session_maker() as session:
                    repo = MySQLResultRepository(session, self._system_ctx)
                    await repo.release_child_dispatch(row.job_id)
                raise

    def _select_child_jobs_for_dispatch(
        self,
        candidates: list[Any],
        occupied_rows: list[Any],
        global_headroom: int,
    ) -> list[Any]:
        if global_headroom <= 0 or not candidates:
            return []

        active_by_tenant: dict[str, int] = defaultdict(int)
        active_by_parent: dict[str, int] = defaultdict(int)
        for row in occupied_rows:
            params = getattr(row, "parameters", None) or {}
            tenant_key = str(params.get("tenant_id") or "")
            active_by_tenant[tenant_key] += 1
            if str(getattr(row, "job_kind", "")) == "fleet_child" and getattr(row, "parent_job_id", None):
                active_by_parent[str(row.parent_job_id)] += 1

        tenant_queues: dict[str, list[Any]] = {}
        for row in sorted(candidates, key=lambda item: item.created_at):
            params = getattr(row, "parameters", None) or {}
            tenant_key = str(params.get("tenant_id") or "")
            tenant_queues.setdefault(tenant_key, []).append(row)

        tenant_order = list(tenant_queues.keys())
        scheduled_by_tenant: dict[str, int] = defaultdict(int)
        scheduled_by_parent: dict[str, int] = defaultdict(int)
        selected: list[Any] = []

        while tenant_order and len(selected) < global_headroom:
            made_progress = False
            next_tenant_order: list[str] = []
            for tenant_key in tenant_order:
                queue = tenant_queues.get(tenant_key) or []
                if active_by_tenant[tenant_key] + scheduled_by_tenant[tenant_key] >= int(get_settings().tenant_max_active_jobs):
                    if queue:
                        next_tenant_order.append(tenant_key)
                    continue

                chosen_index: Optional[int] = None
                for idx, candidate in enumerate(queue):
                    parent_key = str(getattr(candidate, "parent_job_id", "") or "")
                    if parent_key and (
                        active_by_parent[parent_key] + scheduled_by_parent[parent_key]
                        >= self._fleet_parent_max_active_children
                    ):
                        continue
                    chosen_index = idx
                    break

                if chosen_index is None:
                    if queue:
                        next_tenant_order.append(tenant_key)
                    continue

                candidate = queue[chosen_index]
                del queue[chosen_index]
                selected.append(candidate)
                scheduled_by_tenant[tenant_key] += 1
                parent_key = str(getattr(candidate, "parent_job_id", "") or "")
                if parent_key:
                    scheduled_by_parent[parent_key] += 1
                made_progress = True
                if queue:
                    next_tenant_order.append(tenant_key)
                if len(selected) >= global_headroom:
                    break

            if not made_progress:
                break
            tenant_order = next_tenant_order

        return selected

    async def _process_job_with_semaphore(self, job: Job) -> None:
        if self._semaphore:
            async with self._semaphore:
                await self._process_job(job)

    # ---------------------------------------------------------
    # NEW: extract dates from dataset key
    # ---------------------------------------------------------
    def _extract_dates_from_dataset_key(self, dataset_key: str):
        """
        Expected:
          datasets/D1/20260210_20260210.parquet
        """

        m = re.search(r"(\d{8})_(\d{8})", dataset_key)
        if not m:
            raise AnalyticsError(
                f"Invalid dataset key format: {dataset_key}"
            )

        start = datetime.strptime(m.group(1), "%Y%m%d")
        end = datetime.strptime(m.group(2), "%Y%m%d")

        return start, end

    async def _try_claim_job_start(self, job_id: str) -> bool:
        now = datetime.now(timezone.utc)
        async with async_session_maker() as session:
            result = await session.execute(
                update(AnalyticsJob)
                .where(AnalyticsJob.job_id == job_id)
                .where(AnalyticsJob.status == JobStatus.PENDING.value)
                .where(AnalyticsJob.queue_started_at.is_(None))
                .values(
                    status=JobStatus.RUNNING.value,
                    started_at=now,
                    queue_started_at=now,
                    worker_lease_expires_at=now + timedelta(seconds=self._lease_seconds),
                    last_heartbeat_at=now,
                )
            )
            await session.commit()
            return bool(result.rowcount)

    async def _process_job(self, job: Job) -> None:
        job_id = job.job_id
        try:
            raw_payload = json.loads(job.raw_payload)
            bound_payload = BoundJobPayload(**raw_payload)
            bound_payload.validate()
            ctx = bound_payload.to_tenant_context()
            request_type = bound_payload.job_type
            if request_type == "fleet_parent_analytics":
                request = FleetAnalyticsRequest.model_validate(bound_payload.payload)
                if ctx.tenant_id:
                    parameters = dict(request.parameters or {})
                    parameters.setdefault("tenant_id", ctx.tenant_id)
                    request = request.model_copy(update={"parameters": parameters})
            else:
                request = AnalyticsRequest.model_validate(bound_payload.payload)
                if ctx.tenant_id:
                    parameters = dict(request.parameters or {})
                    parameters.setdefault("tenant_id", ctx.tenant_id)
                    request = request.model_copy(update={"parameters": parameters})
        except Exception as exc:
            self._logger.error("job_payload_invalid", job_id=job_id, error=str(exc))
            await self._mark_job_failed(job_id, f"Invalid job payload: {exc}", error_code="INVALID_JOB_PAYLOAD")
            await self._incr_event("dead_letter")
            await self._queue.dead_letter(job, f"Invalid job payload: {exc}")
            if job.receipt:
                await self._queue.ack_job(job.receipt)
            return

        self._logger.info(
            "processing_job",
            job_id=job_id,
            analysis_type=getattr(request, "analysis_type", "unknown"),
            job_type=request_type,
        )

        if not await self._try_claim_job_start(job_id):
            self._logger.info("job_already_claimed_or_terminal", job_id=job_id, job_type=request_type)
            if job.receipt:
                await self._queue.ack_job(job.receipt)
            self._queue.task_done()
            return

        if request_type == "fleet_parent_analytics":
            await self._process_fleet_parent_job(job, ctx, request)
            return

        async with async_session_maker() as session:
            try:
                result_repo = MySQLResultRepository(session, ctx)

                # ---------------------------------------------------------
                # PERMANENT FIX:
                # Fill date_range_* when dataset_key is used
                # ---------------------------------------------------------
                if request.dataset_key:
                    date_range_start, date_range_end = (
                        self._extract_dates_from_dataset_key(
                            request.dataset_key
                        )
                    )
                else:
                    date_range_start = request.start_time
                    date_range_end = request.end_time

                await result_repo.update_job_queue_metadata(
                    job_id=job_id,
                    attempt=job.attempt,
                    worker_lease_expires_at=datetime.now(timezone.utc) + timedelta(seconds=self._lease_seconds),
                    last_heartbeat_at=datetime.now(timezone.utc),
                )

                s3_client = S3Client()
                dataset_service = DatasetService(s3_client)

                runner = JobRunner(dataset_service, result_repo)
                heartbeat = asyncio.create_task(self._heartbeat_loop(job_id))
                try:
                    await asyncio.wait_for(
                        runner.run_job(job_id, request),
                        timeout=self._job_timeout_seconds,
                    )
                finally:
                    heartbeat.cancel()
                    try:
                        await heartbeat
                    except asyncio.CancelledError:
                        pass
                    await result_repo.update_job_queue_metadata(
                        job_id=job_id,
                        worker_lease_expires_at=None,
                    )

                self._logger.info("job_completed", job_id=job_id)
                if job.receipt:
                    await self._queue.ack_job(job.receipt)

            except asyncio.TimeoutError:
                message = f"Job execution exceeded timeout of {self._job_timeout_seconds} seconds"
                self._logger.error(
                    "job_failed_timeout",
                    job_id=job_id,
                    tenant_id=ctx.tenant_id,
                    device_id=request.device_id,
                    timeout_seconds=self._job_timeout_seconds,
                )
                await self._retry_or_fail(job, "JOB_EXECUTION_TIMEOUT", message)

            except DatasetNotFoundError as e:
                self._logger.error(
                    "job_failed_dataset_not_found",
                    job_id=job_id,
                    tenant_id=ctx.tenant_id,
                    device_id=request.device_id,
                    error=str(e),
                )
                await self._retry_or_fail(job, "DATASET_NOT_FOUND", str(e))

            except AnalyticsError as e:
                self._logger.error(
                    "job_failed_analytics_error",
                    job_id=job_id,
                    tenant_id=ctx.tenant_id,
                    device_id=request.device_id,
                    error=str(e),
                )
                await self._retry_or_fail(job, "ANALYTICS_ERROR", str(e))

            except Exception as e:
                self._logger.error(
                    "job_failed_unexpected",
                    job_id=job_id,
                    tenant_id=ctx.tenant_id,
                    device_id=request.device_id,
                    error=str(e),
                    exc_info=True,
                )
                await self._retry_or_fail(job, "UNEXPECTED_ERROR", f"Unexpected error: {e}")

            finally:
                self._queue.task_done()

    async def _heartbeat_loop(self, job_id: str) -> None:
        while self._running:
            await asyncio.sleep(self._heartbeat_seconds)
            now = datetime.now(timezone.utc)
            try:
                await self._write_job_heartbeat(job_id, now)
            except Exception as exc:
                if is_transient_disconnect(exc):
                    try:
                        await reset_db_connections()
                        await self._write_job_heartbeat(job_id, now)
                        self._logger.info("job_heartbeat_recovered_after_disconnect", job_id=job_id)
                    except Exception as retry_exc:
                        if is_transient_disconnect(retry_exc):
                            self._logger.info(
                                "job_heartbeat_waiting_for_db_reconnect",
                                job_id=job_id,
                                error=str(retry_exc),
                            )
                        else:
                            self._logger.warning("job_heartbeat_failed", job_id=job_id, error=str(retry_exc))
                else:
                    self._logger.warning("job_heartbeat_failed", job_id=job_id, error=str(exc))

    async def _write_job_heartbeat(self, job_id: str, now: datetime) -> None:
        async with async_session_maker() as session:
            result_repo = MySQLResultRepository(session, self._system_ctx)
            await result_repo.update_job_queue_metadata(
                job_id=job_id,
                last_heartbeat_at=now,
                worker_lease_expires_at=now + timedelta(seconds=self._lease_seconds),
            )

    async def _process_fleet_parent_job(
        self,
        job: Job,
        ctx: TenantContext,
        request: FleetAnalyticsRequest,
    ) -> None:
        job_id = job.job_id
        try:
            async with async_session_maker() as session:
                result_repo = MySQLResultRepository(session, ctx)
                await result_repo.update_job_queue_metadata(
                    job_id=job_id,
                    attempt=job.attempt,
                    worker_lease_expires_at=datetime.now(timezone.utc) + timedelta(seconds=self._lease_seconds),
                    last_heartbeat_at=datetime.now(timezone.utc),
                )
                await result_repo.update_job_status(
                    job_id=job_id,
                    status=JobStatus.RUNNING,
                    started_at=datetime.now(timezone.utc),
                    progress=self._phase_progress_to_absolute("fleet_readiness", 0.0),
                    message="Checking data readiness for fleet analytics",
                    error_message=None,
                    phase="fleet_readiness",
                    phase_label=FLEET_PARENT_PHASES["fleet_readiness"].label,
                    phase_progress=0.0,
                )

            heartbeat = asyncio.create_task(self._heartbeat_loop(job_id))
            try:
                await asyncio.wait_for(
                    self._run_fleet_parent(job_id, request, ctx),
                    timeout=self._job_timeout_seconds,
                )
            finally:
                heartbeat.cancel()
                try:
                    await heartbeat
                except asyncio.CancelledError:
                    pass
                async with async_session_maker() as session:
                    result_repo = MySQLResultRepository(session, self._system_ctx)
                    await result_repo.update_job_queue_metadata(
                        job_id=job_id,
                        worker_lease_expires_at=None,
                    )

            self._logger.info("fleet_parent_completed", job_id=job_id)
            if job.receipt:
                await self._queue.ack_job(job.receipt)
        except asyncio.TimeoutError:
            message = f"Fleet orchestration exceeded timeout of {self._job_timeout_seconds} seconds"
            self._logger.error("fleet_parent_timeout", job_id=job_id, timeout_seconds=self._job_timeout_seconds)
            await self._retry_or_fail(job, "JOB_EXECUTION_TIMEOUT", message)
        except AnalyticsError as exc:
            self._logger.error("fleet_parent_failed_analytics_error", job_id=job_id, error=str(exc))
            await self._retry_or_fail(job, "ANALYTICS_ERROR", str(exc))
        except Exception as exc:
            self._logger.error("fleet_parent_failed_unexpected", job_id=job_id, error=str(exc), exc_info=True)
            await self._retry_or_fail(job, "UNEXPECTED_ERROR", f"Unexpected error: {exc}")
        finally:
            self._queue.task_done()

    async def _run_fleet_parent(
        self,
        parent_job_id: str,
        request: FleetAnalyticsRequest,
        ctx: TenantContext,
    ) -> None:
        device_ids = [str(device_id) for device_id in request.device_ids]
        if not device_ids:
            await self._fail_parent_job(
                parent_job_id,
                "No devices available for fleet analysis",
                {"analysis_type": request.analysis_type, "devices_failed": []},
            )
            return

        async with async_session_maker() as session:
            repo = MySQLResultRepository(session, ctx)
            existing_children = await repo.list_jobs_for_parent(parent_job_id)
            parent_job = await repo.get_job(parent_job_id)
            existing_results = parent_job.results or {}
        child_jobs = {
            str(child.device_id): str(child.job_id)
            for child in existing_children
        }
        skipped_devices = list(existing_results.get("skipped_children") or [])

        if not child_jobs:
            await self._update_parent_progress(
                parent_job_id,
                phase="fleet_readiness",
                phase_progress=0.15,
                message=f"Checking data readiness for {len(device_ids)} devices",
            )
            ready_keys, skipped_devices = await self._resolve_fleet_ready_devices(request, ctx.tenant_id)
            await self._persist_fleet_state(parent_job_id, child_jobs, skipped_devices)
            if not ready_keys:
                await self._fail_parent_job(
                    parent_job_id,
                    "No devices have exact-range datasets ready for the selected window.",
                    {"analysis_type": request.analysis_type, "devices_failed": skipped_devices},
                )
                return

            await self._update_parent_progress(
                parent_job_id,
                phase="child_submission",
                phase_progress=0.2,
                message="Submitting device analytics jobs",
            )
            model_name = request.model_name or self._default_model_for(request.analysis_type)
            for device_id in ready_keys:
                child_id = str(uuid4())
                child_parameters = dict(request.parameters or {})
                child_parameters["parent_job_id"] = parent_job_id
                async with async_session_maker() as session:
                    repo = MySQLResultRepository(session, ctx)
                    await repo.create_job(
                        job_id=child_id,
                        device_id=device_id,
                        analysis_type=request.analysis_type,
                        model_name=model_name,
                        date_range_start=request.start_time,
                        date_range_end=request.end_time,
                        parameters=child_parameters,
                        job_kind="fleet_child",
                        parent_job_id=parent_job_id,
                    )
                    await repo.update_job_queue_metadata(
                        job_id=child_id,
                        attempt=1,
                        queue_enqueued_at=datetime.now(timezone.utc),
                    )
                child_jobs[device_id] = child_id
                await self._persist_fleet_state(parent_job_id, child_jobs, skipped_devices)
                await self._update_parent_progress(
                    parent_job_id,
                    phase="child_submission",
                    phase_progress=len(child_jobs) / max(1, len(ready_keys)),
                    message=f"Submitting device analytics jobs ({len(child_jobs)}/{len(ready_keys)})",
                )

        if not child_jobs:
            await self._fail_parent_job(
                parent_job_id,
                "No devices produced child analytics jobs.",
                {"analysis_type": request.analysis_type, "devices_failed": skipped_devices},
            )
            return

        await self._update_parent_progress(
            parent_job_id,
            phase="child_execution",
            phase_progress=0.0,
            message=f"Queued analytics for {len(child_jobs)} fleet devices",
        )

    async def _resolve_fleet_ready_devices(
        self,
        request: FleetAnalyticsRequest,
        tenant_id: str | None,
    ) -> tuple[dict[str, str], list[dict[str, str]]]:
        settings = get_settings()
        s3_client = S3Client()
        dataset_service = DatasetService(s3_client)
        readiness_limit = max(1, int(settings.data_readiness_max_concurrency))
        readiness_semaphore = asyncio.Semaphore(readiness_limit)

        async def _bounded_ready_check(device_id: str):
            async with readiness_semaphore:
                return await ensure_device_ready(
                    s3_client=s3_client,
                    dataset_service=dataset_service,
                    device_id=device_id,
                    start_time=request.start_time,
                    end_time=request.end_time,
                    tenant_id=tenant_id,
                )

        checks = await asyncio.gather(*[_bounded_ready_check(str(device_id)) for device_id in request.device_ids])
        ready_keys: dict[str, str] = {}
        skipped_devices: list[dict[str, str]] = []
        for device_id, key, meta in checks:
            if key:
                ready_keys[str(device_id)] = str(key)
                continue
            reason = str((meta or {}).get("reason") or "dataset_not_ready")
            skipped_devices.append(
                {
                    "device_id": str(device_id),
                    "reason": reason,
                    "message": {
                        "dataset_not_ready": "Exact-range dataset is not ready yet",
                        "export_timeout": "Export timed out while preparing exact-range dataset",
                        "device_not_found": "Device not found in export pipeline",
                        "no_telemetry_in_range": "No telemetry found in selected date range",
                    }.get(reason, "Data readiness check did not pass"),
                }
            )
        return ready_keys, skipped_devices

    async def _persist_fleet_state(
        self,
        parent_job_id: str,
        child_jobs: dict[str, str],
        skipped_devices: list[dict[str, str]],
    ) -> None:
        async with async_session_maker() as session:
            repo = MySQLResultRepository(session, self._system_ctx)
            current = await repo.get_job(parent_job_id)
            existing_results = current.results or {}
            existing_results["children"] = child_jobs
            existing_results["skipped_children"] = skipped_devices
            await repo.save_results(
                job_id=parent_job_id,
                results=existing_results,
                accuracy_metrics={},
                execution_time_seconds=0,
            )

    async def _reconcile_active_fleet_parents(self) -> None:
        async with async_session_maker() as session:
            repo = MySQLResultRepository(session, self._system_ctx)
            parents = await repo.list_jobs(
                job_kinds=["fleet_parent"],
                limit=500,
                offset=0,
            )
        for parent in parents:
            if parent.status not in {JobStatus.PENDING.value, JobStatus.RUNNING.value}:
                continue
            await self._reconcile_fleet_parent(parent.job_id)

    async def _reconcile_fleet_parent(self, parent_job_id: str) -> None:
        formatter = ResultFormatter()
        async with async_session_maker() as session:
            repo = MySQLResultRepository(session, self._system_ctx)
            parent_job = await repo.get_job(parent_job_id)
            state = parent_job.results or {}
            child_jobs = dict(state.get("children") or {})
            skipped_devices = list(state.get("skipped_children") or [])
            child_rows = await repo.list_jobs_for_parent(parent_job_id)

            if not child_rows:
                return

            completed: list[dict] = []
            business_blocked: list[dict] = []
            failed: list[dict] = []
            running_count = 0
            queued_count = 0
            for child in child_rows:
                child_jobs[str(child.device_id)] = str(child.job_id)
                if child.status == JobStatus.COMPLETED.value:
                    coverage = _child_coverage_result(child)
                    if coverage and not bool(coverage.get("usable_for_business_decisions")):
                        business_blocked.append(
                            {
                                "device_id": child.device_id,
                                "job_id": child.job_id,
                                "coverage_result": coverage,
                                "message": str(coverage.get("message") or "Telemetry coverage is insufficient."),
                            }
                        )
                    else:
                        completed.append(
                            {
                                "device_id": child.device_id,
                                "job_id": child.job_id,
                                "results": child.results or {},
                            }
                        )
                elif child.status == JobStatus.FAILED.value:
                    failed.append(
                        {
                            "device_id": child.device_id,
                            "job_id": child.job_id,
                            "message": child.error_message or child.message or "Job failed",
                        }
                    )
                elif child.status == JobStatus.RUNNING.value:
                    running_count += 1
                else:
                    queued_count += 1

            total = len(child_jobs)
            settled = len(completed) + len(business_blocked) + len(failed)
            total_selected = len(list((parent_job.parameters or {}).get("device_ids") or []))
            execution_phase_progress = settled / max(1, total)
            await repo.update_job_progress(
                parent_job_id,
                progress=self._phase_progress_to_absolute("child_execution", execution_phase_progress),
                message=(
                    f"Running analytics for fleet ({settled}/{max(1, total)} settled; "
                    f"{running_count} running; {queued_count} queued)"
                ),
                phase="child_execution",
                phase_label=FLEET_PARENT_PHASES["child_execution"].label,
                phase_progress=execution_phase_progress,
            )

            if running_count == 0 and queued_count == 0 and settled == total:
                await repo.update_job_progress(
                    parent_job_id,
                    progress=self._phase_progress_to_absolute("aggregation", 0.5),
                    message="Aggregating fleet parent results",
                    phase="aggregation",
                    phase_label=FLEET_PARENT_PHASES["aggregation"].label,
                    phase_progress=0.5,
                )
                device_formatted = []
                for item in completed:
                    formatted = (item["results"] or {}).get("formatted")
                    if formatted:
                        device_formatted.append(formatted)
                fleet_formatted = formatter.format_fleet_results(
                    job_id=parent_job_id,
                    analysis_type=str(parent_job.analysis_type),
                    device_results=device_formatted,
                    child_job_map=child_jobs,
                )
                failed_devices = [
                    {
                        "device_id": str(item["device_id"]),
                        "reason": "child_job_failed",
                        "message": str(item["message"]),
                    }
                    for item in failed
                ]
                blocked_devices = [
                    {
                        "device_id": str(item["device_id"]),
                        "reason": str((item.get("coverage_result") or {}).get("level") or "insufficient_coverage"),
                        "message": str(item["message"]),
                    }
                    for item in business_blocked
                ]
                coverage_pct = round((len(completed) / max(1, total_selected or total)) * 100, 1)
                coverage_result = build_device_coverage_result(
                    selected_device_ids=[
                        str(device_id)
                        for device_id in list((parent_job.parameters or {}).get("device_ids") or [])
                    ],
                    usable_device_ids=[str(item["device_id"]) for item in completed],
                    has_any_data=bool(completed or business_blocked),
                    skipped_devices=[*skipped_devices, *blocked_devices, *failed_devices],
                    warnings=[
                        "Fleet analytics coverage is partial; review skipped or failed devices before using the result."
                    ]
                    if skipped_devices or blocked_devices or failed_devices
                    else [],
                    artifact_generation_allowed=bool(completed),
                ).to_dict()
                fleet_formatted["execution_metadata"] = {
                    "fleet_policy": "best_effort_exact",
                    "children_count": total,
                    "devices_ready": [str(item["device_id"]) for item in completed],
                    "devices_failed": failed_devices,
                    "devices_skipped": [*skipped_devices, *blocked_devices],
                    "skipped_reasons": {
                        str(item.get("device_id")): str(item.get("reason"))
                        for item in [*skipped_devices, *blocked_devices]
                    },
                    "coverage_pct": coverage_pct,
                    "selected_device_count": total_selected or total,
                }
                fleet_formatted["coverage_result"] = coverage_result
                await repo.save_results(
                    job_id=parent_job_id,
                    results={
                        "children": child_jobs,
                        "failed_children": failed,
                        "skipped_children": [*skipped_devices, *blocked_devices],
                        "coverage_result": coverage_result,
                        "formatted": fleet_formatted,
                    },
                    accuracy_metrics={},
                    execution_time_seconds=0,
                )
                if completed:
                    message = f"Fleet analysis completed ({len(completed)}/{total_selected or total} devices analyzed)"
                    if skipped_devices or blocked_devices or failed:
                        message += f"; skipped/failed: {len(skipped_devices) + len(blocked_devices) + len(failed)}"
                    final_status = JobStatus.COMPLETED
                    error_message = None
                    final_phase = "completed"
                    final_phase_label = "Completed"
                elif business_blocked or skipped_devices:
                    message = str(coverage_result.get("message") or "No devices had usable telemetry coverage.")
                    final_status = JobStatus.COMPLETED
                    error_message = None
                    final_phase = str(coverage_result.get("level") or "insufficient_coverage")
                    final_phase_label = "No Data" if final_phase == "no_coverage" else "Insufficient Coverage"
                else:
                    message = "No devices produced successful analytics results"
                    final_status = JobStatus.FAILED
                    error_message = "All fleet child jobs were skipped or failed"
                    final_phase = "failed"
                    final_phase_label = "Failed"
                await repo.update_job_status(
                    parent_job_id,
                    status=final_status,
                    completed_at=datetime.now(timezone.utc),
                    progress=100.0,
                    message=message,
                    error_message=error_message,
                    phase=final_phase,
                    phase_label=final_phase_label,
                    phase_progress=1.0,
                )

    async def _update_parent_progress(
        self,
        parent_job_id: str,
        *,
        phase: str,
        phase_progress: float,
        message: str,
    ) -> None:
        async with async_session_maker() as session:
            repo = MySQLResultRepository(session, self._system_ctx)
            await repo.update_job_progress(
                parent_job_id,
                progress=self._phase_progress_to_absolute(phase, phase_progress),
                message=message,
                phase=phase,
                phase_label=FLEET_PARENT_PHASES.get(phase).label if FLEET_PARENT_PHASES.get(phase) else phase.replace("_", " ").title(),
                phase_progress=max(0.0, min(1.0, phase_progress)),
            )

    async def _fail_parent_job(self, parent_job_id: str, message: str, details: dict[str, object]) -> None:
        async with async_session_maker() as session:
            repo = MySQLResultRepository(session, self._system_ctx)
            await repo.save_results(
                job_id=parent_job_id,
                results={
                    "formatted": {
                        "analysis_type": "fleet",
                        "job_id": parent_job_id,
                        "fleet_health_score": 0.0,
                        "worst_device_id": None,
                        "worst_device_health": 0.0,
                        "critical_devices": [],
                        "source_analysis_type": details.get("analysis_type", "prediction"),
                        "device_summaries": [],
                        "execution_metadata": {
                            "data_readiness": "not_ready",
                            "devices_failed": details.get("devices_failed", []),
                            "reason": message,
                        },
                    }
                },
                accuracy_metrics={},
                execution_time_seconds=0,
            )
            await repo.update_job_status(
                job_id=parent_job_id,
                status=JobStatus.FAILED,
                completed_at=datetime.now(timezone.utc),
                message=message,
                error_message=message,
                phase="failed",
                phase_label="Failed",
                phase_progress=1.0,
            )

    async def _retry_or_fail(self, job: Job, error_code: str, error_message: str) -> None:
        non_retryable = {
            "DATASET_NOT_READY_TIMEOUT",
            "DEVICE_NOT_FOUND",
            "NO_TELEMETRY_IN_RANGE",
            "TENANT_SCOPE_REQUIRED",
        }
        error_code = _normalize_failure_code(error_code, error_message)

        if error_code in non_retryable:
            await self._mark_job_failed(job.job_id, error_message, error_code=error_code)
            await self._incr_event("dead_letter")
            await self._queue.dead_letter(job, error_message)
            return

        if job.attempt < self._max_attempts:
            backoff = min(30, 2 ** (job.attempt - 1))
            await asyncio.sleep(backoff)
            if job.receipt:
                await self._queue.ack_job(job.receipt)
            await self._queue.submit_job(
                job.job_id,
                raw_payload=job.raw_payload,
                attempt=job.attempt + 1,
            )
            async with async_session_maker() as session:
                repo = MySQLResultRepository(session, self._system_ctx)
                await repo.update_job_status(
                    job_id=job.job_id,
                    status=JobStatus.PENDING,
                    progress=0.0,
                    message=f"Retrying job (attempt {job.attempt + 1}/{self._max_attempts})",
                    error_message=None,
                    phase="queued",
                    phase_label="Queued",
                    phase_progress=0.0,
                )
                await repo.update_job_queue_metadata(
                    job_id=job.job_id,
                    attempt=job.attempt + 1,
                    error_code=error_code,
                    queue_enqueued_at=datetime.now(timezone.utc),
                    queue_dispatched_at=datetime.now(timezone.utc),
                    queue_started_at=None,
                    worker_lease_expires_at=None,
                )
            await self._incr_event("retry")
            return

        await self._mark_job_failed(job.job_id, error_message, error_code=error_code)
        await self._incr_event("dead_letter")
        await self._queue.dead_letter(job, error_message)

    async def _mark_job_failed(self, job_id: str, error_message: str, error_code: Optional[str] = None) -> None:
        try:
            async with async_session_maker() as session:
                result_repo = MySQLResultRepository(session, self._system_ctx)
                normalized_code = _normalize_failure_code(error_code, error_message)
                msg = "Job failed"
                phase_label = "Failed"
                lower = (error_message or "").lower()
                if "dataset_not_ready_timeout" in lower or "export_timeout" in lower:
                    msg = "Dataset preparation timed out for selected range. Retry shortly."
                elif "dataset not found" in lower or "no such key" in lower:
                    msg = "No exact-range dataset is available for selected date range."
                elif "device_not_found" in lower:
                    msg = "Selected device could not be found in export pipeline."
                elif "no_telemetry_in_range" in lower:
                    msg = "No telemetry found in selected time range."
                    phase_label = "No data"
                elif "no numeric columns" in lower or "insufficient" in lower:
                    msg = "Insufficient signal/data for reliable analytics. Please collect more telemetry."
                elif "job execution exceeded timeout" in lower:
                    msg = "Analytics job timed out before completion."
                elif normalized_code == "TENANT_SCOPE_REQUIRED":
                    msg = "Analytics could not verify tenant-scoped telemetry access for this run."

                await result_repo.update_job_status(
                    job_id=job_id,
                    status=JobStatus.FAILED,
                    completed_at=datetime.utcnow(),
                    message=msg,
                    error_message=error_message,
                    phase="failed",
                    phase_label=phase_label,
                    phase_progress=1.0,
                )
                await result_repo.update_job_queue_metadata(
                    job_id=job_id,
                    error_code=normalized_code,
                    worker_lease_expires_at=None,
                )
        except Exception as e:
            self._logger.error(
                "failed_to_mark_job_failed",
                job_id=job_id,
                error=str(e),
            )

    async def stop(self) -> None:
        self._logger.info("stopping_worker")
        self._running = False
        if self._worker_heartbeat_task:
            self._worker_heartbeat_task.cancel()
            try:
                await self._worker_heartbeat_task
            except asyncio.CancelledError:
                pass
        if self._orchestration_task:
            self._orchestration_task.cancel()
            try:
                await self._orchestration_task
            except asyncio.CancelledError:
                pass

        if self._current_tasks:
            self._logger.info(
                "waiting_for_tasks",
                task_count=len(self._current_tasks),
            )
            await asyncio.gather(*self._current_tasks, return_exceptions=True)

        if self._metrics_redis is not None:
            try:
                await self._metrics_redis.close()
            except Exception:
                pass
            self._metrics_redis = None

        self._logger.info("worker_stopped")

    async def _worker_heartbeat_loop(self) -> None:
        while self._running:
            now = datetime.now(timezone.utc)
            try:
                await self._write_worker_heartbeat(now)
            except Exception as exc:
                if is_transient_disconnect(exc):
                    try:
                        await reset_db_connections()
                        await self._write_worker_heartbeat(now)
                        self._logger.info("worker_heartbeat_recovered_after_disconnect")
                    except Exception as retry_exc:
                        if is_transient_disconnect(retry_exc):
                            self._logger.info("worker_heartbeat_waiting_for_db_reconnect", error=str(retry_exc))
                        else:
                            self._logger.warning("worker_heartbeat_failed", error=str(retry_exc))
                else:
                    self._logger.warning("worker_heartbeat_failed", error=str(exc))
            await asyncio.sleep(max(5, self._heartbeat_seconds))

    async def _write_worker_heartbeat(self, now: datetime) -> None:
        async with async_session_maker() as session:
            row = await session.get(WorkerHeartbeat, self._worker_id)
            if row is None:
                row = WorkerHeartbeat(
                    worker_id=self._worker_id,
                    app_role="worker",
                    status="alive",
                    last_heartbeat_at=now,
                )
                session.add(row)
            else:
                row.status = "alive"
                row.last_heartbeat_at = now
            await session.commit()
