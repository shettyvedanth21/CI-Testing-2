"""Analytics Service entry point."""

import os
import sys
import importlib.abc

APP_ROLE = os.environ.get("APP_ROLE", "").strip().lower()
VALID_ROLES = {"api", "worker"}

if APP_ROLE not in VALID_ROLES:
    raise RuntimeError(
        f"APP_ROLE must be one of {VALID_ROLES}, got '{APP_ROLE}'. "
        "Set APP_ROLE=api for the API server or APP_ROLE=worker for the ML worker."
    )

if APP_ROLE == "api":
    ML_LIBRARIES = ["tensorflow", "torch", "xgboost", "sklearn", "prophet", "shap"]
    for lib in ML_LIBRARIES:
        if lib in sys.modules:
            raise RuntimeError(
                f"ML library '{lib}' was imported in the API process (APP_ROLE=api). "
                "This blocks the event loop. Move ML imports to the worker process only."
            )

    class _APIModuleGuard(importlib.abc.MetaPathFinder):
        def __init__(self, blocked_modules: set[str]):
            self._blocked_modules = blocked_modules

        def find_spec(self, fullname: str, path, target=None):  # type: ignore[override]
            for blocked in self._blocked_modules:
                if fullname == blocked or fullname.startswith(f"{blocked}."):
                    raise AssertionError(
                        f"Module '{fullname}' may not be imported when APP_ROLE=api"
                    )
            return None

    if not any(getattr(finder, "__class__", None).__name__ == "_APIModuleGuard" for finder in sys.meta_path):
        sys.meta_path.insert(
            0,
            _APIModuleGuard(
                {
                    "src.workers.job_worker",
                    "tensorflow",
                    "torch",
                    "xgboost",
                    "sklearn",
                    "prophet",
                    "shap",
                }
            ),
        )

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text

from src.api.routes import analytics, health
from src.config.logging_config import configure_logging
from src.config.settings import Settings, get_settings
from src.infrastructure.database import async_session_maker
from src.rate_limit import configure_rate_limiting
from src.services.retention import apply_retention
from src.workers.job_queue import InMemoryJobQueue, RedisJobQueue
from shared.auth_middleware import AuthMiddleware
from shared.feature_entitlements import require_feature
from services.shared.debug_bootstrap import init_debug
from services.shared.http_compression import add_api_response_compression
from services.shared.startup_contract import validate_startup_contract

init_debug()

logger = structlog.get_logger()

try:
    from prometheus_client import Counter as PCounter, Gauge as PGauge, generate_latest
except ImportError:
    PCounter = None
    PGauge = None
    generate_latest = None

_ANALYTICS_QUEUE_DEPTH = None
_ANALYTICS_QUEUED_JOB_COUNT = None
_ANALYTICS_PROCESSING_JOB_COUNT = None
_ANALYTICS_FAILED_JOB_COUNT = None
_ANALYTICS_ACTIVE_WORKERS = None
_ANALYTICS_PENDING_MESSAGES = None
_ANALYTICS_CLAIMED_MESSAGES = None
_ANALYTICS_DEAD_LETTER_DEPTH = None
_ANALYTICS_RETRY_JOB_COUNT = None
_ANALYTICS_REJECTED_TENANT_CAP_TOTAL = None
_ANALYTICS_REJECTED_OVERLOADED_TOTAL = None

if PGauge is not None:
    _ANALYTICS_QUEUE_DEPTH = PGauge("analytics_queue_depth", "Number of pending analytics jobs")
    _ANALYTICS_QUEUED_JOB_COUNT = PGauge("analytics_queued_job_count", "Jobs in pending status")
    _ANALYTICS_PROCESSING_JOB_COUNT = PGauge("analytics_processing_job_count", "Jobs in running status")
    _ANALYTICS_FAILED_JOB_COUNT = PGauge("analytics_failed_job_count", "Jobs in failed status")
    _ANALYTICS_ACTIVE_WORKERS = PGauge("analytics_active_workers", "Workers with recent heartbeat")
    _ANALYTICS_PENDING_MESSAGES = PGauge("analytics_pending_messages", "Stream queued messages")
    _ANALYTICS_CLAIMED_MESSAGES = PGauge("analytics_claimed_messages", "Stream claimed messages")
    _ANALYTICS_DEAD_LETTER_DEPTH = PGauge("analytics_dead_letter_depth", "Current dead letter stream depth")
    _ANALYTICS_RETRY_JOB_COUNT = PGauge("analytics_retry_job_count", "Current jobs with retry attempts")
    _ANALYTICS_REJECTED_TENANT_CAP_TOTAL = PCounter("analytics_rejected_tenant_cap_total", "Submissions rejected by tenant cap")
    _ANALYTICS_REJECTED_OVERLOADED_TOTAL = PCounter("analytics_rejected_overloaded_total", "Submissions rejected by backlog overload")

_CROSS_PROCESS_COUNTER_DEFS = [
    ("analytics_dead_letter_events_total", "counters:analytics_dead_letter_events", "Dead letter messages added"),
    ("analytics_retry_events_total", "counters:analytics_retry_events", "Job retry events"),
]


def _normalize_validation_error_value(value: Any) -> Any:
    if isinstance(value, BaseException):
        return {
            "type": value.__class__.__name__,
            "message": str(value),
        }
    if isinstance(value, dict):
        return {
            str(key): _normalize_validation_error_value(item)
            for key, item in value.items()
        }
    if isinstance(value, tuple):
        return [_normalize_validation_error_value(item) for item in value]
    if isinstance(value, list):
        return [_normalize_validation_error_value(item) for item in value]
    return value


def _build_validation_error_details(exc: RequestValidationError) -> list[dict[str, Any]]:
    normalized_errors = [
        _normalize_validation_error_value(error)
        for error in exc.errors()
    ]
    return jsonable_encoder(normalized_errors)


async def cleanup_stale_jobs(max_age_minutes: int | None = 30) -> int:
    cutoff = (
        datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)
        if max_age_minutes is not None
        else None
    )
    where_clause = "WHERE status = 'running'"
    params: dict[str, object] = {}
    if cutoff is not None:
        where_clause += " AND created_at < :cutoff"
        params["cutoff"] = cutoff

    async with async_session_maker() as session:
        result = await session.execute(
            text(
                f"""
                UPDATE analytics_jobs
                SET status = 'failed',
                    error_code = 'SERVICE_RESTART',
                    error_message = 'Job was interrupted by a service restart. Please resubmit.',
                    message = 'Job was interrupted by a service restart. Please resubmit.',
                    completed_at = UTC_TIMESTAMP(),
                    updated_at = UTC_TIMESTAMP()
                {where_clause}
                """
            ),
            params,
        )
        await session.commit()
        return int(result.rowcount or 0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle."""
    validate_startup_contract()
    settings = get_settings()
    configure_logging(settings.log_level)
    logger.info("analytics_service_starting", version="1.0.0")
    cleanup_window_minutes = 30
    cleaned_jobs = await cleanup_stale_jobs(cleanup_window_minutes)
    if cleaned_jobs:
        logger.warning(
            "analytics_stale_jobs_cleaned",
            count=cleaned_jobs,
            app_role=settings.app_role,
            cleanup_window_minutes=cleanup_window_minutes,
        )
    
    if settings.queue_backend == "redis":
        job_queue = RedisJobQueue(
            redis_url=settings.redis_url,
            stream_name=settings.redis_stream_name,
            dead_letter_stream=settings.redis_dead_letter_stream,
            consumer_group=settings.redis_consumer_group,
            consumer_name=settings.redis_consumer_name,
            maxsize=settings.queue_max_length,
        )
    else:
        job_queue = InMemoryJobQueue(maxsize=settings.queue_max_length)

    job_worker = None
    worker_task = None
    app.state.job_queue = job_queue
    app.state.fleet_tasks = set()
    app.state.pending_jobs = {}
    app.state.queue_backend = settings.queue_backend
    app.state.analytics_rejections = {
        "tenant_cap": 0,
        "overloaded": 0,
    }
    app.state.prom_counters = {
        "tenant_cap": _ANALYTICS_REJECTED_TENANT_CAP_TOTAL,
        "overloaded": _ANALYTICS_REJECTED_OVERLOADED_TOTAL,
    }

    if settings.app_role == "worker":
        from src.workers.job_worker import JobWorker

        job_worker = JobWorker(job_queue, max_concurrent=settings.max_concurrent_jobs)
        app.state.job_worker = job_worker
        worker_task = asyncio.create_task(job_worker.start())

    _retrainer = None
    if settings.ml_weekly_retrainer_enabled and settings.app_role == "worker":
        from src.infrastructure.s3_client import S3Client
        from src.services.analytics.retrainer import WeeklyRetrainer
        from src.services.dataset_service import DatasetService

        _retrainer = WeeklyRetrainer(
            job_queue=job_queue,
            dataset_service=DatasetService(S3Client()),
        )
        await _retrainer.start(device_ids=[])
        app.state.retrainer = _retrainer

    retention_task = None

    async def run_retention_loop() -> None:
        while True:
            try:
                await apply_retention()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("analytics_retention_failed")
            await asyncio.sleep(max(300, int(settings.retention_interval_seconds)))

    if settings.retention_enabled:
        retention_task = asyncio.create_task(run_retention_loop())
        app.state.retention_task = retention_task
    
    logger.info("analytics_service_ready")
    
    yield
    
    logger.info("analytics_service_shutting_down")
    if job_worker is not None:
        await job_worker.stop()
    for task in list(app.state.fleet_tasks):
        task.cancel()
    if app.state.fleet_tasks:
        await asyncio.gather(*app.state.fleet_tasks, return_exceptions=True)
    if worker_task is not None:
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass

    if settings.ml_weekly_retrainer_enabled and hasattr(app.state, "retrainer") and _retrainer:
        await _retrainer.stop()
    if retention_task is not None:
        retention_task.cancel()
        try:
            await retention_task
        except asyncio.CancelledError:
            pass
    logger.info("analytics_service_stopped")


def create_app() -> FastAPI:
    """Create and configure FastAPI application."""
    settings = get_settings()
    
    app = FastAPI(
        title="Analytics Service",
        description="ML Analytics Service for Energy Intelligence Platform",
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )
    configure_rate_limiting(app)
    add_api_response_compression(app)
    app.add_middleware(AuthMiddleware)
    
    app.include_router(health.router, prefix="/health", tags=["health"])
    app.include_router(
        analytics.router,
        prefix="/api/v1/analytics",
        tags=["analytics"],
        dependencies=[Depends(require_feature("analytics"))],
    )

    @app.get("/health")
    async def health_compat() -> dict[str, str]:
        return {
            "status": "healthy",
            "service": "analytics-service",
        }

    @app.get("/metrics", tags=["health"])
    async def metrics():
        if PGauge is None or generate_latest is None:
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "METRICS_RUNTIME_UNAVAILABLE",
                    "message": "prometheus_client is required for /metrics but is not installed",
                    "code": "METRICS_RUNTIME_UNAVAILABLE",
                },
            )

        from src.infrastructure.mysql_repository import MySQLResultRepository
        from src.models.schemas import JobStatus

        queue = getattr(app.state, "job_queue", None)
        queue_metrics_fetcher = getattr(queue, "metrics", None)
        queue_metrics = await queue_metrics_fetcher() if callable(queue_metrics_fetcher) else {}

        pending_count = 0
        running_count = 0
        failed_count = 0
        retry_count = 0
        active_workers = 0
        try:
            async with async_session_maker() as session:
                result_repo = MySQLResultRepository(session)
                pending_count = await result_repo.count_jobs(statuses=[JobStatus.PENDING.value])
                running_count = await result_repo.count_jobs(statuses=[JobStatus.RUNNING.value])
                failed_count = await result_repo.count_jobs(statuses=[JobStatus.FAILED.value])
                retry_count = await result_repo.count_jobs(attempts_gte=2)
                cutoff = datetime.now(timezone.utc) - timedelta(seconds=max(10, get_settings().worker_heartbeat_ttl_seconds))
                from src.models.database import WorkerHeartbeat
                rows = await session.execute(
                    __import__("sqlalchemy").select(WorkerHeartbeat).where(WorkerHeartbeat.last_heartbeat_at >= cutoff)
                )
                active_workers = len(list(rows.scalars().all()))
        except Exception:
            pass

        _ANALYTICS_QUEUE_DEPTH.set(pending_count)
        _ANALYTICS_QUEUED_JOB_COUNT.set(pending_count)
        _ANALYTICS_PROCESSING_JOB_COUNT.set(running_count)
        _ANALYTICS_FAILED_JOB_COUNT.set(failed_count)
        _ANALYTICS_ACTIVE_WORKERS.set(active_workers)
        _ANALYTICS_PENDING_MESSAGES.set(int(queue_metrics.get("queued_messages", 0)))
        _ANALYTICS_CLAIMED_MESSAGES.set(int(queue_metrics.get("claimed_messages", 0)))
        _ANALYTICS_DEAD_LETTER_DEPTH.set(int(queue_metrics.get("dead_letter_messages", 0)))
        _ANALYTICS_RETRY_JOB_COUNT.set(retry_count)

        counter_lines = []
        settings_obj = get_settings()
        if getattr(settings_obj, "redis_url", None):
            try:
                from redis.asyncio import Redis as AIORedis
                redis = AIORedis.from_url(settings_obj.redis_url, decode_responses=True)
                for metric_name, redis_key, help_text in _CROSS_PROCESS_COUNTER_DEFS:
                    val = int(await redis.get(redis_key) or 0)
                    counter_lines.append(f"# HELP {metric_name} {help_text}")
                    counter_lines.append(f"# TYPE {metric_name} counter")
                    counter_lines.append(f"{metric_name} {val}")
                await redis.close()
            except Exception:
                pass

        from starlette.responses import Response as PromResponse
        payload = generate_latest().decode("utf-8")
        if counter_lines:
            payload += "\n" + "\n".join(counter_lines) + "\n"
        return PromResponse(content=payload, media_type="text/plain; version=0.0.4; charset=utf-8")

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={
                "error": "VALIDATION_ERROR",
                "message": "Invalid request payload",
                "code": "VALIDATION_ERROR",
                "details": _build_validation_error_details(exc),
            },
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        if isinstance(exc.detail, dict):
            payload = dict(exc.detail)
            payload.setdefault("code", payload.get("error", "HTTP_ERROR"))
            payload.setdefault("message", "Request failed")
            return JSONResponse(status_code=exc.status_code, content=payload)
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": "HTTP_ERROR", "message": str(exc.detail), "code": "HTTP_ERROR"},
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        logger.exception("Unhandled exception in analytics-service")
        return JSONResponse(
            status_code=500,
            content={
                "error": "INTERNAL_ERROR",
                "message": "Unexpected server error",
                "code": "INTERNAL_ERROR",
            },
        )
    
    return app


app = create_app()
