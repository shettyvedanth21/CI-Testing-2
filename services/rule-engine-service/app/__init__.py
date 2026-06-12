"""Rule Engine Service - Energy Intelligence Platform.

This module initializes the FastAPI application with all required configurations.
"""

import asyncio
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.config import settings
from app.database import AsyncSessionLocal, engine
from app.queue import get_notification_queue
from app.repositories.notification_outbox import NotificationOutboxRepository
from app.workers.notification_worker import recover_stale_attempted_on_startup
from shared.auth_middleware import AuthMiddleware
from shared.feature_entitlements import require_feature
from services.shared.debug_bootstrap import init_debug
from services.shared.startup_contract import validate_startup_contract

init_debug()
import logging

logger = logging.getLogger(__name__)

try:
    from prometheus_client import Counter as PCounter, Gauge as PGauge, generate_latest
except ImportError:
    PCounter = None
    PGauge = None
    generate_latest = None

if PGauge is not None:
    _RULE_QUEUE_DEPTH = PGauge("rule_engine_queue_depth", "Notification queue depth")
    _RULE_PENDING_MESSAGES = PGauge("rule_engine_pending_messages", "Stream pending messages")
    _RULE_ACTIVE_WORKERS = PGauge("rule_engine_active_workers", "Workers with recent Redis heartbeat")
    _RULE_DEAD_LETTER_DEPTH = PGauge("rule_engine_dead_letter_depth", "Current dead letter stream depth")

_EVENT_COUNTER_DEFS = [
    ("rule_engine_retry_events_total", "counters:rule_engine_retry_events", "Notification retry events"),
    ("rule_engine_dead_letter_events_total", "counters:rule_engine_dead_letter_events", "Dead letter messages added"),
]

_HEARTBEAT_ZSET = "notification_worker_heartbeats"
_HEARTBEAT_TTL_SECONDS = 90


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for graceful startup and shutdown."""
    from app.logging_config import configure_logging
    from app.shared_http import close_shared_http_clients
    from app.alert_rate_limiter import close_alert_rate_limiter

    validate_startup_contract()
    configure_logging()
    logger.info(
        "Starting Rule Engine Service",
        extra={
            "service": "rule-engine-service",
            "version": settings.APP_VERSION,
            "environment": settings.ENVIRONMENT,
        }
    )
    if settings.QUEUE_BACKEND == "redis":
        await get_notification_queue().metrics()

    await recover_stale_attempted_on_startup()
    
    yield
    
    # Shutdown
    logger.info("Shutting down Rule Engine Service - closing database connections")
    await close_alert_rate_limiter()
    await close_shared_http_clients()
    await engine.dispose()
    logger.info("Rule Engine Service shutdown complete")


app = FastAPI(
    title="Rule Engine Service",
    description="Energy Intelligence Platform - Real-time Rule Evaluation Service",
    version=settings.APP_VERSION,
    docs_url="/docs" if settings.ENVIRONMENT != "production" else None,
    redoc_url="/redoc" if settings.ENVIRONMENT != "production" else None,
    lifespan=lifespan,
)
app.add_middleware(AuthMiddleware)

app.include_router(api_router, prefix="/api/v1", dependencies=[Depends(require_feature("rules"))])


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
    logger.exception("Unhandled exception in rule-engine-service")
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
            "service": "rule-engine-service",
            "version": settings.APP_VERSION,
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
        if settings.QUEUE_BACKEND == "redis":
            await get_notification_queue().metrics()
        
        return JSONResponse(
            content={
                "status": "ready",
                "service": "rule-engine-service",
            },
            status_code=200
        )
    except Exception as e:
        logger.error(f"Readiness check failed: {e}")
        return JSONResponse(
            content={
                "status": "not_ready",
                "service": "rule-engine-service",
                "error": str(e),
            },
            status_code=503
        )


@app.get("/metrics", tags=["health"])
async def metrics():
    if PGauge is None or generate_latest is None:
        return {"error": "prometheus_client not installed"}

    queue_metrics = await get_notification_queue().metrics()

    active_workers = 0
    if settings.REDIS_URL:
        try:
            import time as _time
            from redis.asyncio import Redis as AIORedis
            redis = AIORedis.from_url(settings.REDIS_URL, decode_responses=True)
            now = _time.time()
            await redis.zremrangebyscore(_HEARTBEAT_ZSET, "-inf", now - _HEARTBEAT_TTL_SECONDS)
            active_workers = await redis.zcard(_HEARTBEAT_ZSET)
            await redis.close()
        except Exception:
            pass

    _RULE_QUEUE_DEPTH.set(queue_metrics.get("queue_depth", 0))
    _RULE_PENDING_MESSAGES.set(queue_metrics.get("pending_messages", 0))
    _RULE_ACTIVE_WORKERS.set(active_workers)
    _RULE_DEAD_LETTER_DEPTH.set(queue_metrics.get("dead_letter_count", 0))

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
