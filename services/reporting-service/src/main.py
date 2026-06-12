import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from sqlalchemy import text

from fastapi import Depends, FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from src.config import settings
from src.database import AsyncSessionLocal, engine
from src.handlers import energy_router, comparison_router, tariffs_router, common_router, settings_router
from src.queue import get_report_queue
from src.rate_limit import configure_rate_limiting
from src.repositories.report_repository import ReportRepository
from src.services.influx_reader import influx_reader
from src.services.job_runtime import count_active_workers
from src.services.local_bootstrap import ensure_local_tariff_bootstrap, validate_local_bootstrap_contract
from src.tasks.scheduler import start_scheduler, stop_scheduler
from src.storage.minio_client import minio_client
from shared.auth_middleware import AuthMiddleware
from shared.feature_entitlements import require_feature
from services.shared.debug_bootstrap import init_debug
from services.shared.http_compression import add_api_response_compression
from services.shared.startup_contract import validate_startup_contract

init_debug()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

try:
    from prometheus_client import Counter as PCounter, Gauge as PGauge, generate_latest
except ImportError:
    PCounter = None
    PGauge = None
    generate_latest = None

if PGauge is not None:
    _REPORT_QUEUE_DEPTH = PGauge("reporting_queue_depth", "Report queue depth")
    _REPORT_QUEUED_JOB_COUNT = PGauge("reporting_queued_job_count", "Jobs in pending status")
    _REPORT_PROCESSING_JOB_COUNT = PGauge("reporting_processing_job_count", "Jobs in processing status")
    _REPORT_FAILED_JOB_COUNT = PGauge("reporting_failed_job_count", "Jobs in failed status")
    _REPORT_COMPLETED_JOB_COUNT = PGauge("reporting_completed_job_count", "Jobs in completed status")
    _REPORT_ACTIVE_WORKERS = PGauge("reporting_active_workers", "Workers with recent heartbeat")
    _REPORT_PENDING_MESSAGES = PGauge("reporting_pending_messages", "Stream pending messages")
    _REPORT_DEAD_LETTER_DEPTH = PGauge("reporting_dead_letter_depth", "Current dead letter stream depth")

_EVENT_COUNTER_DEFS = [
    ("reporting_retry_events_total", "counters:reporting_retry_events", "Job retry events"),
    ("reporting_timeout_events_total", "counters:reporting_timeout_events", "Job timeout events"),
    ("reporting_dead_letter_events_total", "counters:reporting_dead_letter_events", "Dead letter messages added"),
]

_metrics_redis: object | None = None


def _metrics_redis_ref():
    global _metrics_redis
    if _metrics_redis is not None:
        return _metrics_redis
    if not settings.REDIS_URL:
        return None
    try:
        from redis.asyncio import Redis as AIORedis
        _metrics_redis = AIORedis.from_url(settings.REDIS_URL, decode_responses=True)
        return _metrics_redis
    except Exception:
        return None


async def requeue_enqueue_failed_reports() -> None:
    from src.queue import ReportJob, get_report_queue

    BATCH_LIMIT = 50
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                """
                SELECT report_id, tenant_id, report_type
                FROM energy_reports
                WHERE status = 'enqueue_failed'
                LIMIT :limit
                """
            ),
            {"limit": BATCH_LIMIT},
        )
        rows = result.fetchall()

    if not rows:
        return

    queue = get_report_queue()
    for row in rows:
        report_id, tenant_id, report_type = row
        now = datetime.utcnow()
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    UPDATE energy_reports
                    SET status = 'pending', progress = 0, phase = 'queued',
                        phase_label = 'Queued', enqueued_at = :now,
                        error_code = 'ENQUEUE_RETRY', error_message = 'Requeued after enqueue failure on startup'
                    WHERE report_id = :report_id
                    """
                ),
                {"now": now, "report_id": report_id},
            )
        await queue.enqueue(
            ReportJob(
                report_id=report_id,
                tenant_id=tenant_id,
                report_type=report_type,
            ),
        )
        logger.warning("enqueue_failed_report_requeued report_id=%s", report_id)

logger.info("="*60)
logger.info("REPORTING SERVICE VERSION: DIAGNOSTIC_BUILD_03")
logger.info("="*60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio

    validate_startup_contract()
    validate_local_bootstrap_contract()
    logger.info("Starting up reporting-service...")
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        logger.info("Database connection verified")
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        raise
    
    try:
        await asyncio.to_thread(influx_reader.client.ping)
        logger.info("InfluxDB connection verified")
    except Exception as e:
        logger.error(f"InfluxDB connection failed: {e}")
        raise
    
    try:
        await minio_client.async_ensure_bucket_exists()
        logger.info(f"MinIO bucket '{settings.MINIO_BUCKET}' ready")
    except Exception as e:
        logger.error(f"MinIO bucket initialization failed: {e}")
        raise

    if settings.QUEUE_BACKEND == "redis":
        try:
            await get_report_queue().metrics()
            logger.info("Redis report queue connection verified")
        except Exception as e:
            logger.error(f"Redis queue initialization failed: {e}")
            raise

    async with AsyncSessionLocal() as session:
        local_bootstrap = await ensure_local_tariff_bootstrap(session)
    if any(local_bootstrap.values()):
        logger.info("Local reporting bootstrap ensured", extra=local_bootstrap)

    await requeue_enqueue_failed_reports()
    logger.info("Enqueue-failed report recovery complete")
    
    try:
        scheduler = start_scheduler()
        scheduler.start()
        logger.info("Scheduler started successfully")
    except Exception as e:
        logger.error(f"Scheduler startup failed: {e}")
    
    yield
    
    logger.info("Shutting down reporting-service...")
    stop_scheduler()
    influx_reader.close()
    global _metrics_redis
    if _metrics_redis is not None:
        try:
            await _metrics_redis.close()
        except Exception:
            pass
        _metrics_redis = None
    await engine.dispose()


app = FastAPI(
    title="Energy Reporting Service",
    version="1.0.0",
    lifespan=lifespan
)
add_api_response_compression(app)
configure_rate_limiting(app)
app.add_middleware(AuthMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    error_messages = []
    for error in exc.errors():
        if "Input tag" in str(error.get("msg", "")):
            continue
        loc = ".".join(str(l) for l in error.get("loc", []))
        msg = error.get("msg", "")
        error_messages.append(f"{loc}: {msg}")
    
    error_summary = "; ".join(error_messages) if error_messages else "Validation error"
    
    return JSONResponse(
        status_code=400,
        content={
            "error": "VALIDATION_ERROR",
            "message": error_summary,
            "details": error_messages
        }
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    # If detail is a dict (our structured error), return it as-is
    if isinstance(exc.detail, dict):
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.detail
        )
    # Otherwise, wrap it
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": "HTTP_ERROR",
            "message": str(exc.detail)
        }
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception in reporting-service")
    return JSONResponse(
        status_code=500,
        content={
            "error": "INTERNAL_ERROR",
            "message": "Unexpected server error",
            "code": "INTERNAL_ERROR",
        },
    )


class HealthResponse(BaseModel):
    status: str


class ReadyResponse(BaseModel):
    status: str
    db: str
    influx: str
    minio: str
    queue: str


@app.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse(status="healthy")


@app.get("/ready", response_model=ReadyResponse)
async def ready():
    import asyncio

    db_status = "connected"
    influx_status = "connected"
    minio_status = "connected"
    queue_status = "connected"
    
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        db_status = "disconnected"
    
    try:
        await asyncio.to_thread(influx_reader.client.ping)
    except Exception:
        influx_status = "disconnected"

    try:
        await minio_client.async_health_check()
    except Exception:
        minio_status = "disconnected"

    if settings.QUEUE_BACKEND == "redis":
        try:
            await get_report_queue().metrics()
        except Exception:
            queue_status = "disconnected"

    if "disconnected" in {db_status, influx_status, minio_status, queue_status}:
        raise HTTPException(
            status_code=503,
            detail={
                "status": "not_ready",
                "db": db_status,
                "influx": influx_status,
                "minio": minio_status,
                "queue": queue_status,
            },
        )
    
    return ReadyResponse(
        status="ready",
        db=db_status,
        influx=influx_status,
        minio=minio_status,
        queue=queue_status,
    )


@app.get("/metrics")
async def metrics():
    if PGauge is None or generate_latest is None:
        return {"error": "prometheus_client not installed"}

    async with AsyncSessionLocal() as db:
        repo = ReportRepository(db)
        status_counts = await repo.count_by_status()
        active_workers = await count_active_workers(db)
    queue_metrics = await get_report_queue().metrics()

    _REPORT_QUEUE_DEPTH.set(queue_metrics.get("queue_depth", 0))
    _REPORT_QUEUED_JOB_COUNT.set(status_counts.get("pending", 0))
    _REPORT_PROCESSING_JOB_COUNT.set(status_counts.get("processing", 0))
    _REPORT_FAILED_JOB_COUNT.set(status_counts.get("failed", 0))
    _REPORT_COMPLETED_JOB_COUNT.set(status_counts.get("completed", 0))
    _REPORT_ACTIVE_WORKERS.set(active_workers)
    _REPORT_PENDING_MESSAGES.set(queue_metrics.get("pending_messages", 0))
    _REPORT_DEAD_LETTER_DEPTH.set(queue_metrics.get("dead_letter_count", 0))

    counter_lines = []
    if settings.REDIS_URL and _metrics_redis_ref() is not None:
        try:
            redis = _metrics_redis_ref()
            for metric_name, redis_key, help_text in _EVENT_COUNTER_DEFS:
                val = int(await redis.get(redis_key) or 0)
                counter_lines.append(f"# HELP {metric_name} {help_text}")
                counter_lines.append(f"# TYPE {metric_name} counter")
                counter_lines.append(f"{metric_name} {val}")
        except Exception:
            global _metrics_redis
            if _metrics_redis is not None:
                try:
                    await _metrics_redis.close()
                except Exception:
                    pass
                _metrics_redis = None

    from starlette.responses import Response as PromResponse
    payload = generate_latest().decode("utf-8")
    if counter_lines:
        payload += "\n" + "\n".join(counter_lines) + "\n"
    return PromResponse(content=payload, media_type="text/plain; version=0.0.4; charset=utf-8")


app.include_router(energy_router, prefix="/api/reports/energy", tags=["Energy Reports"], dependencies=[Depends(require_feature("reports"))])
app.include_router(comparison_router, prefix="/api/reports/energy/comparison", tags=["Comparison Reports"], dependencies=[Depends(require_feature("reports"))])
app.include_router(tariffs_router, prefix="/api/reports/tariffs", tags=["Tariffs"], dependencies=[Depends(require_feature("reports"))])
# Backward-compatible internal tariff routes for services still calling the legacy path.
app.include_router(tariffs_router, prefix="/api/v1/tariffs", tags=["Tariffs"], dependencies=[Depends(require_feature("reports"))])
app.include_router(common_router, prefix="/api/reports", tags=["Reports"], dependencies=[Depends(require_feature("reports"))])
app.include_router(settings_router, prefix="/api/v1/settings", tags=["Settings"], dependencies=[Depends(require_feature("settings"))])
