"""Device Service - Energy Intelligence Platform.

This module initializes the FastAPI application with all required configurations.
"""

import asyncio
import datetime as datetime_module
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager
import json
import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from sqlalchemy import delete, func, select, tuple_

from app.api.v1.router import api_router
from app.config import get_dependency_dns_status, settings, validate_dependency_dns
from app.database import AsyncSessionLocal, engine
from app.logging_config import configure_logging
from app.monitoring import (
    DASHBOARD_SCHEDULER_LAG_SECONDS,
    configure_fleet_stream_broadcaster,
    fleet_stream_broadcaster,
    metrics_payload,
)
from app.models.device import DashboardSnapshot, Device
from shared.auth_middleware import AuthMiddleware
from services.shared.debug_bootstrap import init_debug
from services.shared.http_compression import add_api_response_compression
from services.shared.startup_contract import validate_startup_contract
from services.shared.tenant_context import TenantContext
from app.services.device_identity import ensure_device_allocator_state
from app.services.hardware_identity import ensure_hardware_unit_allocator_state

init_debug()
import logging

logger = logging.getLogger(__name__)

SERVICE_STARTED_AT = datetime_module.datetime.utcnow().isoformat() + "Z"
_SENSITIVE_FIELD_NAMES = {
    "authorization",
    "password",
    "confirm_password",
    "refresh_token",
    "access_token",
    "token",
}


def _make_json_safe(value):
    if isinstance(value, BaseException):
        return str(value)
    if isinstance(value, dict):
        return {key: _make_json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_make_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_make_json_safe(item) for item in value]
    return value


def _redact_sensitive_data(value):
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            if str(key).lower() in _SENSITIVE_FIELD_NAMES:
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = _redact_sensitive_data(item)
        return redacted
    if isinstance(value, list):
        return [_redact_sensitive_data(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_sensitive_data(item) for item in value]
    return value


def _sanitize_validation_errors(errors):
    sanitized = []
    for error in errors:
        clean = {key: value for key, value in error.items() if key != "input"}
        if "ctx" in clean:
            clean["ctx"] = _redact_sensitive_data(clean["ctx"])
        sanitized.append(clean)
    return sanitized


async def _load_active_tenant_ids(session) -> list[str]:
    from app.scheduler_helpers import load_active_tenant_ids as _load
    return await _load(session)


async def _run_live_projection_reconciliation_cycle(*, refresh_fleet_snapshot: bool) -> None:
    from app.database import AsyncSessionLocal
    from app.scheduler_helpers import run_live_projection_reconciliation_cycle as _run
    await _run(refresh_fleet_snapshot=refresh_fleet_snapshot, session_factory=AsyncSessionLocal)


async def _run_activation_backfill_cycle() -> None:
    from app.database import AsyncSessionLocal
    from app.scheduler_helpers import run_activation_backfill_cycle as _run
    await _run(session_factory=AsyncSessionLocal)


async def _run_state_interval_retention_cycle() -> None:
    from app.database import AsyncSessionLocal
    from app.scheduler_helpers import run_state_interval_retention_cycle as _run
    await _run(session_factory=AsyncSessionLocal)


async def _run_dashboard_snapshot_retention_cycle() -> None:
    from app.database import AsyncSessionLocal
    from app.scheduler_helpers import run_dashboard_snapshot_retention_cycle as _run
    await _run(session_factory=AsyncSessionLocal)


DEMO_DEVICES = (
    {
        "device_id": "COMPRESSOR-001",
        "device_name": "Compressor 001",
        "device_type": "compressor",
        "data_source_type": "metered",
        "phase_type": "three",
        "manufacturer": "Atlas Copco",
        "model": "GA37",
        "location": "Plant A",
        "metadata_json": {"floor": "1", "line": "A"},
    },
    {
        "device_id": "COMPRESSOR-002",
        "device_name": "Compressor 002",
        "device_type": "compressor",
        "data_source_type": "sensor",
        "phase_type": "three",
        "manufacturer": "Atlas Copco",
        "model": "GA37",
        "location": "Plant B",
        "metadata_json": {"floor": "2", "line": "B"},
    },
    {
        "device_id": "COMPRESSOR-003",
        "device_name": "Compressor 003",
        "device_type": "compressor",
        "data_source_type": "sensor",
        "phase_type": "three",
        "manufacturer": "Atlas Copco",
        "model": "GA37",
        "location": "Plant C",
        "metadata_json": {"floor": "3", "line": "C"},
    },
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for graceful startup and shutdown."""
    # Startup
    validate_startup_contract()
    configure_logging()
    logger.info(
        "Starting Device Service",
        extra={
            "service": "device-service",
            "version": settings.APP_VERSION,
            "environment": settings.ENVIRONMENT,
        }
    )
    
    logger.info("Database schema is managed by Alembic migrations")
    validate_dependency_dns(log_failures=False)
    if settings.DEVICE_SERVICE_ENABLE_FLEET_STREAM:
        configure_fleet_stream_broadcaster(settings.DASHBOARD_STREAM_QUEUE_SIZE)
        await fleet_stream_broadcaster.start(
            redis_url=settings.REDIS_URL,
            channel_template=settings.FLEET_STREAM_REDIS_CHANNEL_TEMPLATE,
        )
    async with AsyncSessionLocal() as session:
        device_allocator_updates = await ensure_device_allocator_state(session)
        hardware_allocator_updated = await ensure_hardware_unit_allocator_state(session)
    if device_allocator_updates:
        logger.info("Device ID allocator state ensured", extra={"updated_prefixes": device_allocator_updates})
    if hardware_allocator_updated:
        logger.info("Hardware unit allocator state ensured", extra={"prefix": "HWU"})

    if settings.BOOTSTRAP_DEMO_DEVICES:
        await _bootstrap_demo_devices()

    if settings.MIGRATE_SNAPSHOTS_TO_MINIO:
        try:
            from app.tasks.migrate_snapshots import migrate_snapshots_to_minio

            migration_summary = await migrate_snapshots_to_minio()
            logger.info("Snapshot migration task completed", extra=migration_summary)
        except Exception as exc:
            logger.error("Snapshot migration task failed", extra={"error": str(exc)})

    if settings.DEVICE_SERVICE_RUN_STARTUP_MAINTENANCE:
        # Ensure fleet snapshots are consistent with repaired live projections before the
        # service starts accepting traffic after a restart.
        await _run_live_projection_reconciliation_cycle(refresh_fleet_snapshot=True)
        await _run_activation_backfill_cycle()

    stop_event = asyncio.Event()
    trends_task = None
    dashboard_task = None
    projection_reconciler_task = None
    state_interval_retention_task = None
    dashboard_snapshot_retention_task = None
    recent_telemetry_sample_cleanup_task = None

    async def run_performance_trends_scheduler():
        from app.database import AsyncSessionLocal
        from app.services.performance_trends import PerformanceTrendService
        from app.services.idle_running import IdleRunningService

        interval_minutes = max(1, settings.PERFORMANCE_TRENDS_INTERVAL_MINUTES)
        interval_seconds = interval_minutes * 60

        def seconds_until_next_boundary() -> float:
            now = datetime.now(timezone.utc)
            next_slot = (now + timedelta(minutes=interval_minutes)).replace(second=0, microsecond=0)
            rounded_minute = (next_slot.minute // interval_minutes) * interval_minutes
            next_slot = next_slot.replace(minute=rounded_minute)
            if next_slot <= now:
                next_slot = now + timedelta(seconds=interval_seconds)
            return (next_slot - now).total_seconds()

        initial_delay = seconds_until_next_boundary()
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=initial_delay)
            return
        except asyncio.TimeoutError:
            pass

        while not stop_event.is_set():
            try:
                async with AsyncSessionLocal() as session:
                    tenant_ids = await _load_active_tenant_ids(session)
            except Exception as exc:
                logger.error("Performance trends tenant discovery failed", extra={"error": str(exc)})
                tenant_ids = []

            for tenant_id in tenant_ids:
                tenant_ctx = TenantContext(
                    tenant_id=tenant_id,
                    user_id="system-scheduler",
                    role="system",
                    plant_ids=[],
                    is_super_admin=False,
                )
                try:
                    async with AsyncSessionLocal() as session:
                        service = PerformanceTrendService(session, tenant_ctx)
                        summary = await service.materialize_latest_bucket()
                        idle_service = IdleRunningService(session, tenant_ctx)
                        idle_summary = await idle_service.aggregate_all_configured_devices()
                        safe_summary = {
                            "tenant_id": tenant_id,
                            "devices_total": summary.get("devices_total", 0),
                            "created_count": summary.get("created", 0),
                            "updated_count": summary.get("updated", 0),
                            "failed_count": summary.get("failed", 0),
                            "idle_processed": idle_summary.get("processed", 0),
                            "idle_failed": idle_summary.get("failed", 0),
                        }
                        logger.info(
                            "Performance trends and idle aggregation cycle completed",
                            extra=safe_summary,
                        )
                except Exception as exc:
                    logger.error(
                        "Performance trends scheduler failed",
                        extra={"tenant_id": tenant_id, "error": str(exc)},
                    )

            wait_seconds = seconds_until_next_boundary()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=wait_seconds)
            except asyncio.TimeoutError:
                pass

    if (
        settings.DEVICE_SERVICE_RUN_EMBEDDED_SCHEDULERS
        and settings.PERFORMANCE_TRENDS_ENABLED
        and settings.PERFORMANCE_TRENDS_CRON_ENABLED
    ):
        trends_task = asyncio.create_task(run_performance_trends_scheduler())
        logger.info(
            "Performance trends scheduler started",
            extra={
                "interval_minutes": settings.PERFORMANCE_TRENDS_INTERVAL_MINUTES,
                "timezone": settings.PERFORMANCE_TRENDS_TIMEZONE,
            },
        )

    async def run_dashboard_snapshot_scheduler():
        from app.database import AsyncSessionLocal
        from app.services.dashboard import DashboardService

        hot_interval = max(1, int(settings.DASHBOARD_SNAPSHOT_INTERVAL_SECONDS))
        energy_interval = max(hot_interval, int(settings.DASHBOARD_ENERGY_REFRESH_SECONDS))
        max_drift = max(1, int(settings.DASHBOARD_SCHEDULER_MAX_DRIFT_SECONDS))
        last_energy_run_by_tenant: dict[str, datetime] = {}
        expected_next = datetime.now(timezone.utc)
        cycle_lock = asyncio.Lock()

        while not stop_event.is_set():
            now_utc = datetime.now(timezone.utc)
            lag_seconds = max(0.0, (now_utc - expected_next).total_seconds())
            DASHBOARD_SCHEDULER_LAG_SECONDS.set(lag_seconds)
            if lag_seconds > max_drift:
                logger.warning(
                    "Dashboard scheduler lag exceeded threshold",
                    extra={"lag_seconds": lag_seconds, "max_drift_seconds": max_drift},
                )
            if cycle_lock.locked():
                logger.warning("Skipping dashboard snapshot cycle because previous cycle is still running")
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=hot_interval)
                except asyncio.TimeoutError:
                    pass
                expected_next = datetime.now(timezone.utc) + timedelta(seconds=hot_interval)
                continue
            try:
                async with cycle_lock:
                    try:
                        async with AsyncSessionLocal() as session:
                            tenant_ids = await _load_active_tenant_ids(session)
                    except Exception as exc:
                        logger.error("Dashboard tenant discovery failed", extra={"error": str(exc)})
                        tenant_ids = []

                    for tenant_id in tenant_ids:
                        tenant_ctx = TenantContext(
                            tenant_id=tenant_id,
                            user_id="system-scheduler",
                            role="system",
                            plant_ids=[],
                            is_super_admin=False,
                        )
                        try:
                            async with AsyncSessionLocal() as session:
                                service = DashboardService(session, tenant_ctx)
                                hot_summary = {"fleet_devices": 0, "summary_devices": 0}
                                last_energy_run = last_energy_run_by_tenant.get(
                                    tenant_id,
                                    datetime.min.replace(tzinfo=timezone.utc),
                                )
                                should_refresh_energy = (now_utc - last_energy_run).total_seconds() >= energy_interval
                                energy_refreshed = False
                                if should_refresh_energy:
                                    await service.materialize_energy_and_loss_snapshots()
                                    current_local = now_utc.astimezone().replace(tzinfo=None)
                                    await service.materialize_monthly_energy_snapshot(
                                        year=current_local.year,
                                        month=current_local.month,
                                    )
                                    await service.materialize_dashboard_summary_snapshot()
                                    last_energy_run_by_tenant[tenant_id] = now_utc
                                    energy_refreshed = True
                                logger.info(
                                    "Dashboard snapshot cycle completed",
                                    extra={
                                        "tenant_id": tenant_id,
                                        "fleet_devices": hot_summary.get("fleet_devices", 0),
                                        "summary_devices": hot_summary.get("summary_devices", 0),
                                        "hot_interval_seconds": hot_interval,
                                        "energy_refreshed": energy_refreshed,
                                        "energy_interval_seconds": energy_interval,
                                    },
                                )
                        except Exception as exc:
                            logger.error(
                                "Dashboard snapshot scheduler failed",
                                extra={"tenant_id": tenant_id, "error": str(exc)},
                            )
            except Exception as exc:
                logger.error("Dashboard snapshot scheduler failed", extra={"error": str(exc)})

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=hot_interval)
            except asyncio.TimeoutError:
                pass
            expected_next = datetime.now(timezone.utc) + timedelta(seconds=hot_interval)

    if settings.DEVICE_SERVICE_RUN_EMBEDDED_SCHEDULERS and settings.DASHBOARD_SNAPSHOT_ENABLED:
        dashboard_task = asyncio.create_task(run_dashboard_snapshot_scheduler())
        logger.info(
            "Dashboard snapshot scheduler started",
            extra={
                "hot_interval_seconds": settings.DASHBOARD_SNAPSHOT_INTERVAL_SECONDS,
                "energy_interval_seconds": settings.DASHBOARD_ENERGY_REFRESH_SECONDS,
            },
        )

    async def run_live_projection_reconciler():
        interval_seconds = max(60, int(settings.DASHBOARD_RECONCILE_INTERVAL_SECONDS))
        while not stop_event.is_set():
            await _run_live_projection_reconciliation_cycle(refresh_fleet_snapshot=True)

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
            except asyncio.TimeoutError:
                pass

    if settings.DEVICE_SERVICE_RUN_EMBEDDED_SCHEDULERS and settings.DASHBOARD_RECONCILE_INTERVAL_SECONDS > 0:
        projection_reconciler_task = asyncio.create_task(run_live_projection_reconciler())
        logger.info(
            "Live projection reconciler started",
            extra={"interval_seconds": settings.DASHBOARD_RECONCILE_INTERVAL_SECONDS},
        )

    async def run_state_interval_retention_scheduler():
        interval_seconds = max(300, int(settings.STATE_INTERVAL_CLEANUP_INTERVAL_SECONDS))
        while not stop_event.is_set():
            await _run_state_interval_retention_cycle()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
            except asyncio.TimeoutError:
                pass

    if (
        settings.DEVICE_SERVICE_RUN_EMBEDDED_SCHEDULERS
        and settings.STATE_INTERVAL_RETENTION_ENABLED
        and settings.STATE_INTERVAL_RETENTION_DAYS > 0
    ):
        state_interval_retention_task = asyncio.create_task(run_state_interval_retention_scheduler())
        logger.info(
            "State interval retention scheduler started",
            extra={
                "interval_seconds": settings.STATE_INTERVAL_CLEANUP_INTERVAL_SECONDS,
                "retention_days": settings.STATE_INTERVAL_RETENTION_DAYS,
                "batch_size": settings.STATE_INTERVAL_CLEANUP_BATCH_SIZE,
                "max_batches_per_run": settings.STATE_INTERVAL_CLEANUP_MAX_BATCHES_PER_RUN,
                "stale_open_alert_days": settings.STATE_INTERVAL_STALE_OPEN_ALERT_DAYS,
            },
        )

    async def run_dashboard_snapshot_retention_scheduler():
        interval_seconds = max(300, int(settings.DASHBOARD_SNAPSHOT_CLEANUP_INTERVAL_SECONDS))
        while not stop_event.is_set():
            await _run_dashboard_snapshot_retention_cycle()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
            except asyncio.TimeoutError:
                pass

    if (
        settings.DEVICE_SERVICE_RUN_EMBEDDED_SCHEDULERS
        and settings.DASHBOARD_SNAPSHOT_CLEANUP_ENABLED
        and settings.DASHBOARD_SNAPSHOT_TTL_SECONDS > 0
    ):
        dashboard_snapshot_retention_task = asyncio.create_task(run_dashboard_snapshot_retention_scheduler())
        logger.info(
            "Dashboard snapshot retention scheduler started",
            extra={
                "interval_seconds": settings.DASHBOARD_SNAPSHOT_CLEANUP_INTERVAL_SECONDS,
                "ttl_seconds": settings.DASHBOARD_SNAPSHOT_TTL_SECONDS,
                "batch_size": settings.DASHBOARD_SNAPSHOT_CLEANUP_BATCH_SIZE,
            },
        )

    async def run_recent_telemetry_sample_cleanup_scheduler():
        from app.database import AsyncSessionLocal
        from app.services.live_projection import LiveProjectionService

        interval_seconds = max(60, int(settings.RECENT_TELEMETRY_SAMPLE_CLEANUP_INTERVAL_SECONDS))
        batch_size = max(50, int(settings.RECENT_TELEMETRY_SAMPLE_CLEANUP_BATCH_SIZE))
        while not stop_event.is_set():
            try:
                async with AsyncSessionLocal() as session:
                    service = LiveProjectionService(session)
                    summary = await service.cleanup_recent_telemetry_overflow(batch_size=batch_size)
                    if summary["cleaned"] > 0:
                        logger.info(
                            "Recent telemetry sample overflow cleanup completed",
                            extra=summary,
                        )
            except Exception as exc:
                logger.error(
                    "Recent telemetry sample overflow cleanup failed",
                    extra={"error": str(exc)},
                )
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
            except asyncio.TimeoutError:
                pass

    if settings.DEVICE_SERVICE_RUN_EMBEDDED_SCHEDULERS and settings.RECENT_TELEMETRY_SAMPLE_CLEANUP_ENABLED:
        recent_telemetry_sample_cleanup_task = asyncio.create_task(run_recent_telemetry_sample_cleanup_scheduler())
        logger.info(
            "Recent telemetry sample overflow cleanup scheduler started",
            extra={
                "interval_seconds": settings.RECENT_TELEMETRY_SAMPLE_CLEANUP_INTERVAL_SECONDS,
                "batch_size": settings.RECENT_TELEMETRY_SAMPLE_CLEANUP_BATCH_SIZE,
            },
        )

    yield

    # Shutdown
    from app.services.shared_http import close_all as close_shared_http_clients

    if trends_task:
        stop_event.set()
        try:
            await asyncio.wait_for(trends_task, timeout=10)
        except asyncio.TimeoutError:
            trends_task.cancel()
    if dashboard_task:
        stop_event.set()
        try:
            await asyncio.wait_for(dashboard_task, timeout=10)
        except asyncio.TimeoutError:
            dashboard_task.cancel()
    if projection_reconciler_task:
        stop_event.set()
        try:
            await asyncio.wait_for(projection_reconciler_task, timeout=10)
        except asyncio.TimeoutError:
            projection_reconciler_task.cancel()
    if state_interval_retention_task:
        stop_event.set()
        try:
            await asyncio.wait_for(state_interval_retention_task, timeout=10)
        except asyncio.TimeoutError:
            state_interval_retention_task.cancel()
    if dashboard_snapshot_retention_task:
        stop_event.set()
        try:
            await asyncio.wait_for(dashboard_snapshot_retention_task, timeout=10)
        except asyncio.TimeoutError:
            dashboard_snapshot_retention_task.cancel()
    if recent_telemetry_sample_cleanup_task:
        stop_event.set()
        try:
            await asyncio.wait_for(recent_telemetry_sample_cleanup_task, timeout=10)
        except asyncio.TimeoutError:
            recent_telemetry_sample_cleanup_task.cancel()
    logger.info("Shutting down Device Service - closing shared HTTP clients")
    await close_shared_http_clients()
    logger.info("Shutting down Device Service - closing database connections")
    if settings.DEVICE_SERVICE_ENABLE_FLEET_STREAM:
        await fleet_stream_broadcaster.stop()
    await engine.dispose()
    logger.info("Device Service shutdown complete")


async def _bootstrap_demo_devices() -> None:
    """Create the standard demo devices if they are missing."""
    from app.database import AsyncSessionLocal
    from app.schemas.device import DeviceCreate
    from app.services.device import DeviceService

    demo_tenant_id = os.getenv("BOOTSTRAP_DEMO_TENANT_ID", "SH00000001").strip() or "SH00000001"
    demo_plant_id = os.getenv("BOOTSTRAP_DEMO_PLANT_ID", "demo-plant").strip() or "demo-plant"

    created = 0
    skipped = 0
    async with AsyncSessionLocal() as session:
        service = DeviceService(session)
        for device in DEMO_DEVICES:
            existing = await session.scalar(
                select(Device).where(
                    Device.tenant_id == demo_tenant_id,
                    Device.deleted_at.is_(None),
                    Device.device_name == device["device_name"],
                )
            )
            if existing is not None:
                skipped += 1
                continue
            payload = DeviceCreate(
                tenant_id=demo_tenant_id,
                plant_id=demo_plant_id,
                device_name=device["device_name"],
                device_type=device["device_type"],
                device_id_class="active",
                data_source_type=device["data_source_type"],
                phase_type=device["phase_type"],
                manufacturer=device["manufacturer"],
                model=device["model"],
                location=device["location"],
                metadata_json=json.dumps(device["metadata_json"]),
            )
            await service.create_device(payload)
            created += 1

    logger.info(
        "demo_device_bootstrap_completed",
        extra={"created_count": created, "skipped_count": skipped},
    )


app = FastAPI(
    title="Device Service",
    description="Energy Intelligence Platform - Device Management Service",
    version=settings.APP_VERSION,
    docs_url="/docs" if settings.ENVIRONMENT != "production" else None,
    redoc_url="/redoc" if settings.ENVIRONMENT != "production" else None,
    lifespan=lifespan,
)
add_api_response_compression(app)
app.add_middleware(AuthMiddleware)

app.include_router(api_router, prefix="/api/v1")


@app.middleware("http")
async def append_service_started_at_header(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Service-Started-At"] = SERVICE_STARTED_AT
    return response


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "error": "VALIDATION_ERROR",
            "message": "Invalid request payload",
            "code": "VALIDATION_ERROR",
            "details": _make_json_safe(_sanitize_validation_errors(exc.errors())),
        },
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if isinstance(exc.detail, dict):
        payload = dict(exc.detail)
        if "error" in payload:
            return JSONResponse(status_code=exc.status_code, content=payload)
        error_block = payload.get("error")
        if isinstance(error_block, dict):
            payload.setdefault("code", error_block.get("code", "HTTP_ERROR"))
            payload.setdefault("message", error_block.get("message", "Request failed"))
        else:
            payload.setdefault("code", "HTTP_ERROR")
            payload.setdefault("message", "Request failed")
        return JSONResponse(status_code=exc.status_code, content=payload)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": "HTTP_ERROR", "message": str(exc.detail), "code": "HTTP_ERROR"},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception in device-service")
    return JSONResponse(
        status_code=500,
        content={
            "error": "INTERNAL_ERROR",
            "message": "Unexpected server error",
            "code": "INTERNAL_ERROR",
        },
    )


@app.get("/health", tags=["health"])
async def health_check():
    """Health check endpoint for Kubernetes probes."""
    return JSONResponse(
        content={
            "status": "healthy",
            "service": "device-service",
            "version": settings.APP_VERSION,
            "dependency_dns": get_dependency_dns_status(force_refresh=True),
        },
        status_code=200
    )


@app.get("/ready", tags=["health"])
async def readiness_check():
    """Readiness check endpoint for Kubernetes probes."""
    try:
        # Check database connectivity
        from sqlalchemy import text
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        
        return JSONResponse(
            content={
                "status": "ready",
                "service": "device-service",
            },
            status_code=200
        )
    except Exception as e:
        logger.error(f"Readiness check failed: {e}")
        return JSONResponse(
            content={
                "status": "not_ready",
                "service": "device-service",
                "error": "dependency_check_failed",
            },
            status_code=503
        )


@app.get("/metrics", tags=["health"])
async def metrics():
    payload, content_type = metrics_payload()
    return Response(content=payload, media_type=content_type)
