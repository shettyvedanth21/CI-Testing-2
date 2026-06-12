import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import inspect, text

from src.config import settings
from src.database import engine
from src.handlers import waste_router
from src.services.retention import apply_waste_retention
from src.services.influx_reader import influx_reader
from src.storage.minio_client import minio_client
from shared.auth_middleware import AuthMiddleware
from shared.feature_entitlements import require_feature
from services.shared.debug_bootstrap import init_debug
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
    _WASTE_QUEUE_DEPTH = PGauge("waste_queue_depth", "Waste queue depth")
    _WASTE_QUEUED_JOB_COUNT = PGauge("waste_queued_job_count", "Jobs in pending status")
    _WASTE_PROCESSING_JOB_COUNT = PGauge("waste_processing_job_count", "Jobs in running status")
    _WASTE_FAILED_JOB_COUNT = PGauge("waste_failed_job_count", "Jobs in failed status")
    _WASTE_COMPLETED_JOB_COUNT = PGauge("waste_completed_job_count", "Jobs in completed status")
    _WASTE_ACTIVE_WORKERS = PGauge("waste_active_workers", "Workers with recent heartbeat")
    _WASTE_PENDING_MESSAGES = PGauge("waste_pending_messages", "Stream pending messages")
    _WASTE_DEAD_LETTER_DEPTH = PGauge("waste_dead_letter_depth", "Current dead letter stream depth")

_EVENT_COUNTER_DEFS = [
    ("waste_retry_events_total", "counters:waste_retry_events", "Job retry events"),
    ("waste_timeout_events_total", "counters:waste_timeout_events", "Job timeout events"),
    ("waste_dead_letter_events_total", "counters:waste_dead_letter_events", "Dead letter messages added"),
]


async def requeue_stale_waste_jobs_on_startup() -> None:
    from src.queue import WasteJob, get_waste_queue
    from src.models import WasteAnalysisJob, WasteStatus
    from sqlalchemy import select, update
    import json

    cutoff = datetime.utcnow() - timedelta(minutes=10)
    now = datetime.utcnow()
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                """
                SELECT id, tenant_id, scope, device_ids, start_date, end_date, granularity, retry_count
                FROM waste_analysis_jobs
                WHERE status IN ('pending', 'running', 'enqueue_failed')
                AND created_at < :cutoff
                """
            ),
            {"cutoff": cutoff},
        )
        rows = result.fetchall()

    if not rows:
        return

    queue = get_waste_queue()
    for row in rows:
        job_id, tenant_id, scope, device_ids, start_date, end_date, granularity, retry_count = row
        next_attempt = int(retry_count or 0) + 1
        if next_attempt >= settings.WASTE_JOB_MAX_RETRIES:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        """
                        UPDATE waste_analysis_jobs
                        SET status = 'failed', progress_pct = 100, stage = 'Failed',
                            error_code = 'SERVICE_RESTARTED', error_message = 'Service restarted',
                            completed_at = :now, worker_lease_expires_at = NULL
                        WHERE id = :job_id
                        """
                    ),
                    {"now": now, "job_id": job_id},
                )
            logger.error("stale_waste_job_failed_on_startup job_id=%s", job_id)
            continue

        params = {
            "tenant_id": tenant_id,
            "scope": scope,
            "device_ids": device_ids,
            "start_date": start_date.isoformat() if hasattr(start_date, "isoformat") else str(start_date) if start_date else None,
            "end_date": end_date.isoformat() if hasattr(end_date, "isoformat") else str(end_date) if end_date else None,
            "granularity": granularity,
        }
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    UPDATE waste_analysis_jobs
                    SET status = 'pending', progress_pct = 0, stage = 'Queued',
                        started_at = NULL, worker_id = NULL, processing_started_at = NULL,
                        worker_lease_expires_at = NULL, retry_count = :next_attempt,
                        error_code = 'SERVICE_RESTARTED',
                        error_message = :msg
                    WHERE id = :job_id
                    """
                ),
                {
                    "next_attempt": next_attempt,
                    "msg": f"Requeued after service restart (attempt {next_attempt}/{settings.WASTE_JOB_MAX_RETRIES})",
                    "job_id": job_id,
                },
            )
        await queue.enqueue(
            WasteJob(
                job_id=job_id,
                tenant_id=tenant_id,
                params_json=json.dumps(params, separators=(",", ":"), sort_keys=True),
                attempt=next_attempt,
            ),
        )
        logger.warning("stale_waste_job_requeued_on_startup job_id=%s attempt=%s", job_id, next_attempt)


async def ensure_waste_duplicate_job_index() -> None:
    index_name = "idx_waste_jobs_tenant_duplicate_lookup"
    async with engine.begin() as conn:
        def _ensure(sync_conn) -> None:
            inspector = inspect(sync_conn)
            existing_indexes = {idx["name"]: idx for idx in inspector.get_indexes("waste_analysis_jobs")}
            existing = existing_indexes.get(index_name)
            expected_columns = ["tenant_id", "status", "scope", "start_date", "end_date", "granularity"]
            if existing and existing.get("column_names") == expected_columns:
                return
            if existing:
                sync_conn.execute(text(f"DROP INDEX {index_name} ON waste_analysis_jobs"))
            if "idx_waste_jobs_duplicate_lookup" in existing_indexes:
                sync_conn.execute(text("DROP INDEX idx_waste_jobs_duplicate_lookup ON waste_analysis_jobs"))
            if "idx_waste_jobs_history_tenant_created" not in existing_indexes:
                sync_conn.execute(
                    text(
                        """
                        CREATE INDEX idx_waste_jobs_history_tenant_created
                        ON waste_analysis_jobs (tenant_id, created_at)
                        """
                    )
                )
            if index_name not in existing_indexes or existing is None or existing.get("column_names") != expected_columns:
                sync_conn.execute(
                    text(
                        f"""
                        CREATE INDEX {index_name}
                        ON waste_analysis_jobs (tenant_id, status, scope, start_date, end_date, granularity)
                        """
                    )
                )

        await conn.run_sync(_ensure)


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_startup_contract()
    logger.info("Starting waste-analysis-service (role=%s)...", settings.APP_ROLE)

    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))

    if settings.APP_ROLE == "api":
        await requeue_stale_waste_jobs_on_startup()
        logger.info("Stale waste-analysis jobs requeue complete")
        await ensure_waste_duplicate_job_index()
        logger.info("Waste duplicate job index ensured")

        try:
            await asyncio.to_thread(influx_reader.client.ping)
        except Exception as exc:  # pragma: no cover
            logger.error("Influx ping failed on startup", exc_info=exc)
            raise

        await asyncio.to_thread(minio_client.ensure_bucket_exists)
        retention_task = None

        async def run_retention_loop() -> None:
            while True:
                try:
                    await apply_waste_retention()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # pragma: no cover
                    logger.error("Waste retention cycle failed", exc_info=exc)
                await asyncio.sleep(max(300, int(settings.WASTE_RETENTION_INTERVAL_SECONDS)))

        if settings.WASTE_RETENTION_ENABLED:
            retention_task = asyncio.create_task(run_retention_loop())
        yield

        if retention_task is not None:
            retention_task.cancel()
            try:
                await retention_task
            except asyncio.CancelledError:
                pass
    else:
        yield

    influx_reader.close()
    await engine.dispose()

    try:
        from src.services.remote_clients import close_shared_http_clients
        await close_shared_http_clients()
    except Exception:
        pass


app = FastAPI(title="Waste Analysis Service", version="1.0.0", lifespan=lifespan)
app.add_middleware(AuthMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "error": "VALIDATION_ERROR",
            "message": "Invalid request payload",
            "code": "VALIDATION_ERROR",
            "details": exc.errors(),
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
    logger.exception("Unhandled exception in waste-analysis-service")
    return JSONResponse(
        status_code=500,
        content={
            "error": "INTERNAL_ERROR",
            "message": "Unexpected server error",
            "code": "INTERNAL_ERROR",
        },
    )


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.get("/ready")
async def ready():
    checks = {
        "db": "connected",
        "influx": "connected",
        "minio": "connected",
    }

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        checks["db"] = "disconnected"

    try:
        await asyncio.to_thread(influx_reader.client.ping)
    except Exception:
        checks["influx"] = "disconnected"

    try:
        await asyncio.to_thread(minio_client.health_check)
    except Exception:
        checks["minio"] = "disconnected"

    if "disconnected" in set(checks.values()):
        raise HTTPException(
            status_code=503,
            detail={"status": "not_ready", **checks},
        )

    return {"status": "ready", **checks}


@app.get("/metrics")
async def metrics():
    if PGauge is None or generate_latest is None:
        return {"error": "prometheus_client not installed"}

    from src.queue import get_waste_queue
    from src.repositories.waste_repository import WasteRepository

    try:
        queue = get_waste_queue()
        queue_metrics = await queue.metrics()
    except Exception:
        queue_metrics = {"queue_depth": 0, "pending_messages": 0, "dead_letter_count": 0}

    status_counts: dict[str, int] = {}
    active_workers = 0
    try:
        async with engine.connect() as conn:
            repo = WasteRepository(conn)
            status_counts = await repo.count_by_status()
            active_workers = await repo.count_active_workers()
    except Exception:
        pass

    _WASTE_QUEUE_DEPTH.set(queue_metrics.get("queue_depth", 0))
    _WASTE_QUEUED_JOB_COUNT.set(status_counts.get("pending", 0))
    _WASTE_PROCESSING_JOB_COUNT.set(status_counts.get("running", 0))
    _WASTE_FAILED_JOB_COUNT.set(status_counts.get("failed", 0))
    _WASTE_COMPLETED_JOB_COUNT.set(status_counts.get("completed", 0))
    _WASTE_ACTIVE_WORKERS.set(active_workers)
    _WASTE_PENDING_MESSAGES.set(queue_metrics.get("pending_messages", 0))
    _WASTE_DEAD_LETTER_DEPTH.set(queue_metrics.get("dead_letter_count", 0))

    counter_lines = []
    if settings.REDIS_URL:
        try:
            from redis.asyncio import Redis as AIORedis
            redis = AIORedis.from_url(settings.REDIS_URL, decode_responses=True)
            for metric_name, redis_key, help_text in _EVENT_COUNTER_DEFS:
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


app.include_router(waste_router, prefix="/api/v1/waste", dependencies=[Depends(require_feature("waste_analysis"))])
