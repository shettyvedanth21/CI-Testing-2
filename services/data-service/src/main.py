
"""
Data Service Main Application

Entry point for the Data Service that handles telemetry ingestion,
validation, enrichment, and persistence.
"""

# ✅ PERMANENT FIX FOR direct execution
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SERVICES_DIR = BASE_DIR.parent
PROJECT_ROOT = SERVICES_DIR.parent
for path in (PROJECT_ROOT, SERVICES_DIR, BASE_DIR):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

# --------------------------------------------------

import asyncio
import signal
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.config import settings
from src.config.cors import build_allowed_origins
from src.handlers import MQTTHandler
from src.services import (
    DLQRetryService,
    OutboxRelayService,
    ReconciliationService,
    RetentionCleanupService,
    TelemetryService,
    ensure_bucket_retention,
)
from src.api import create_router, create_websocket_router
from src.services.websocket_ticket_service import close_websocket_ticket_service
from src.utils.circuit_breaker import get_circuit_breaker_metrics
from src.utils import configure_logging, get_logger
from src.workers import TelemetryPipelineWorker
from shared.auth_middleware import AuthMiddleware
from services.shared.debug_bootstrap import init_debug
from services.shared.startup_contract import validate_startup_contract

init_debug()


configure_logging(settings.log_level)
logger = get_logger(__name__)


_MQTT_PLACEHOLDER_USERNAME = "internal:data-service"
_MQTT_PLACEHOLDER_PASSWORD = "local-dev-data-service-password"
_SENSITIVE_FIELD_NAMES = {
    "authorization",
    "password",
    "confirm_password",
    "refresh_token",
    "access_token",
    "token",
}


def _redact_sensitive_data(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if key.lower() in _SENSITIVE_FIELD_NAMES:
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = _redact_sensitive_data(item)
        return redacted
    if isinstance(value, list):
        return [_redact_sensitive_data(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_sensitive_data(item) for item in value]
    return value


def _sanitize_validation_errors(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sanitized: list[dict[str, Any]] = []
    for error in errors:
        clean = {key: value for key, value in error.items() if key != "input"}
        if "ctx" in clean:
            clean["ctx"] = _redact_sensitive_data(clean["ctx"])
        sanitized.append(clean)
    return sanitized


def _public_dependency_error(_exc: Exception) -> str:
    return "dependency_check_failed"


async def _influx_health_status() -> tuple[str, str | None]:
    telemetry_service = app_state.telemetry_service
    if telemetry_service is None:
        return "unavailable", "telemetry_service_unavailable"
    client = getattr(telemetry_service.influx_repository, "client", None)
    if client is None or not hasattr(client, "ping"):
        return "unavailable", "influx_client_unavailable"
    try:
        healthy = await asyncio.to_thread(client.ping)
    except Exception as exc:  # pragma: no cover - covered via tests through endpoint contract
        logger.warning("data_service_influx_health_check_failed", error=str(exc))
        return "unavailable", _public_dependency_error(exc)
    if healthy:
        return "connected", None
    return "unavailable", "ping_returned_false"


def validate_mqtt_startup_contract() -> None:
    if settings.environment.strip().lower() != "production":
        return

    missing = []
    if not (settings.mqtt_username or "").strip():
        missing.append("MQTT_USERNAME")
    if not (settings.mqtt_password or "").strip():
        missing.append("MQTT_PASSWORD")
    if missing:
        raise RuntimeError(f"STARTUP BLOCKED: Missing production MQTT broker credentials: {missing}")

    if settings.mqtt_username != _MQTT_PLACEHOLDER_USERNAME:
        raise RuntimeError(
            "STARTUP BLOCKED: Production data-service must use the dedicated internal MQTT principal "
            f"{_MQTT_PLACEHOLDER_USERNAME!r}."
        )

    if settings.mqtt_username == _MQTT_PLACEHOLDER_USERNAME and settings.mqtt_password == _MQTT_PLACEHOLDER_PASSWORD:
        raise RuntimeError(
            "STARTUP BLOCKED: MQTT broker credentials still use the local placeholder. "
            "Set production MQTT_USERNAME/MQTT_PASSWORD before enabling authenticated broker access."
        )


class ApplicationState:
    """Global application state management."""

    def __init__(self):
        self.telemetry_service: TelemetryService | None = None
        self.dlq_retry_service: DLQRetryService | None = None
        self.outbox_relay_service: OutboxRelayService | None = None
        self.reconciliation_service: ReconciliationService | None = None
        self.retention_cleanup_service: RetentionCleanupService | None = None
        self.mqtt_handler: MQTTHandler | None = None
        self.telemetry_pipeline_worker: TelemetryPipelineWorker | None = None
        self._shutdown_event = asyncio.Event()

    async def startup(self) -> None:
        logger.info(
            "Starting Data Service",
            version=settings.app_version,
            environment=settings.environment,
        )

        self.telemetry_service = TelemetryService()
        await self.telemetry_service.start()
        await ensure_bucket_retention(self.telemetry_service.influx_repository.client)

        if settings.app_role == "worker":
            self.telemetry_pipeline_worker = TelemetryPipelineWorker(self.telemetry_service)
            await self.telemetry_pipeline_worker.start()
            if settings.telemetry_worker_outbox_relay_enabled:
                self.outbox_relay_service = OutboxRelayService(
                    outbox_repository=self.telemetry_service.outbox_repository,
                    dlq_repository=self.telemetry_service.dlq_repository,
                )
                await self.outbox_relay_service.start()

            if settings.telemetry_worker_maintenance_enabled:
                self.reconciliation_service = ReconciliationService(
                    influx_repository=self.telemetry_service.influx_repository,
                    outbox_repository=self.telemetry_service.outbox_repository,
                )
                await self.reconciliation_service.start()

                self.retention_cleanup_service = RetentionCleanupService(
                    outbox_repository=self.telemetry_service.outbox_repository,
                    dlq_repository=self.telemetry_service.dlq_repository,
                )
                await self.retention_cleanup_service.start()

                self.dlq_retry_service = DLQRetryService(
                    telemetry_service=self.telemetry_service,
                    dlq_repository=self.telemetry_service.dlq_repository,
                    batch_limit=max(1, settings.dlq_retry_batch_limit),
                    base_backoff_seconds=max(1, settings.dlq_retry_base_backoff_seconds),
                    max_backoff_seconds=max(
                        settings.dlq_retry_base_backoff_seconds,
                        settings.dlq_retry_max_backoff_seconds,
                    ),
                )
                await self.dlq_retry_service.start()
        else:
            self.mqtt_handler = MQTTHandler(
                telemetry_service=self.telemetry_service,
            )
            self.mqtt_handler.connect()

        logger.info("Data Service startup complete")

    async def shutdown(self) -> None:
        logger.info("Shutting down Data Service...")

        if self.mqtt_handler:
            self.mqtt_handler.disconnect()
            self.mqtt_handler = None

        if self.telemetry_pipeline_worker:
            await self.telemetry_pipeline_worker.stop()
            self.telemetry_pipeline_worker = None

        if self.reconciliation_service:
            await self.reconciliation_service.stop()
            self.reconciliation_service = None

        if self.retention_cleanup_service:
            await self.retention_cleanup_service.stop()
            self.retention_cleanup_service = None

        if self.outbox_relay_service:
            await self.outbox_relay_service.stop()
            self.outbox_relay_service = None

        if self.dlq_retry_service:
            await self.dlq_retry_service.stop()
            self.dlq_retry_service = None

        if self.telemetry_service:
            await self.telemetry_service.close()
            self.telemetry_service = None

        await close_websocket_ticket_service()

        self._shutdown_event.set()

        logger.info("Data Service shutdown complete")

    async def wait_for_shutdown(self) -> None:
        await self._shutdown_event.wait()


app_state = ApplicationState()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    validate_startup_contract()
    validate_mqtt_startup_contract()
    await app_state.startup()
    yield
    await app_state.shutdown()


def create_application() -> FastAPI:
    app = FastAPI(
        title="Data Service",
        description="Telemetry ingestion, validation, and persistence service",
        version=settings.app_version,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )
    app.add_middleware(AuthMiddleware)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=build_allowed_origins(
            settings.frontend_base_url,
            settings.data_allowed_origins,
        ),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ✅ SINGLE REST API ROUTER
    api_router = create_router()
    app.include_router(api_router)

    # ✅ WebSocket routes
    ws_router = create_websocket_router()
    app.include_router(ws_router)

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={
                "error": "VALIDATION_ERROR",
                "message": "Invalid request payload",
                "code": "VALIDATION_ERROR",
                "details": _sanitize_validation_errors(exc.errors()),
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
        logger.exception("Unhandled exception in data-service")
        return JSONResponse(
            status_code=500,
            content={
                "error": "INTERNAL_ERROR",
                "message": "Unexpected server error",
                "code": "INTERNAL_ERROR",
            },
        )

    return app


def handle_signal(sig: int, frame) -> None:
    logger.info("Received shutdown signal", signal=sig)
    asyncio.create_task(app_state.shutdown())


app = create_application()


@app.get("/")
async def root():
    return {
        "service": "Data Service",
        "version": settings.app_version,
        "status": "running",
        "docs": "/docs",
    }


@app.get("/metrics")
async def metrics():
    try:
        from prometheus_client import generate_latest
        from starlette.responses import Response as StarletteResponse

        payload = generate_latest()
        return StarletteResponse(content=payload, media_type="text/plain; version=0.0.4; charset=utf-8")
    except ImportError:
        return {"error": "prometheus_client not installed"}


@app.get("/health")
async def health():
    dependency_reasons: list[str] = []
    redis_state = "connected"
    redis_error: str | None = None
    influx_state = "connected"
    influx_error: str | None = None
    mqtt_state = "not_applicable"
    mqtt_error: str | None = None
    if app_state.telemetry_service is not None:
        try:
            await app_state.telemetry_service._refresh_stage_metrics()  # noqa: SLF001
        except Exception as exc:
            redis_state = "unavailable"
            redis_error = _public_dependency_error(exc)
            logger.warning("data_service_redis_health_check_failed", error=str(exc))
            dependency_reasons.append("redis_unavailable")
    telemetry_stats = (
        app_state.telemetry_service.get_operational_stats()
        if app_state.telemetry_service is not None
        else None
    )
    dlq_retry_stats = (
        app_state.dlq_retry_service.get_stats()
        if app_state.dlq_retry_service is not None
        else None
    )
    telemetry_status = "healthy"
    telemetry_policy: dict[str, object] = {"state": "healthy", "reasons": []}
    if telemetry_stats is not None:
        stages = telemetry_stats.get("stages", {})
        workers = telemetry_stats.get("workers", {})
        try:
            outbox_counts = (
                await app_state.telemetry_service.outbox_repository.get_status_counts()
                if app_state.telemetry_service is not None
                else {"pending": 0, "failed": 0, "delivered": 0, "dead": 0}
            )
        except Exception as exc:
            redis_state = "unavailable"
            redis_error = _public_dependency_error(exc)
            logger.warning("data_service_redis_health_check_failed", error=str(exc))
            if "redis_unavailable" not in dependency_reasons:
                dependency_reasons.append("redis_unavailable")
            outbox_counts = {"pending": 0, "failed": 0, "delivered": 0, "dead": 0}
        telemetry_stats["outbox"] = outbox_counts
        max_oldest_age = max((float(stage.get("oldest_age_seconds") or 0.0) for stage in stages.values()), default=0.0)
        ready_workers = sum(1 for worker in workers.values() if worker.get("ready"))
        overloaded_reasons: list[str] = []
        degraded_reasons: list[str] = []
        if max_oldest_age >= float(settings.telemetry_health_lag_overload_seconds):
            overloaded_reasons.append("stage_lag_exceeded")
        elif max_oldest_age >= float(settings.telemetry_health_lag_warn_seconds):
            degraded_reasons.append("stage_lag_warning")
        if int(stages.get("projection", {}).get("backlog_depth") or 0) >= int(settings.telemetry_projection_overload_threshold):
            overloaded_reasons.append("projection_backlog_exceeded")
        if int(stages.get("energy", {}).get("backlog_depth") or 0) >= int(settings.telemetry_energy_overload_threshold):
            overloaded_reasons.append("energy_backlog_exceeded")
        if int(stages.get("rules", {}).get("backlog_depth") or 0) >= int(settings.telemetry_rules_overload_threshold):
            overloaded_reasons.append("rules_backlog_exceeded")
        if int(outbox_counts.get("pending") or 0) >= int(settings.outbox_pending_overload_threshold):
            overloaded_reasons.append("outbox_pending_exceeded")
        elif int(outbox_counts.get("pending") or 0) >= int(settings.outbox_pending_warn_threshold):
            degraded_reasons.append("outbox_pending_warning")
        dlq_stats = telemetry_stats.get("dlq") or {}
        dlq_retryable_backlog = int(dlq_stats.get("backlog_count") or 0)
        dlq_non_retryable_pending = int(dlq_stats.get("pending_non_retryable_count") or 0)
        if dlq_retryable_backlog >= int(settings.dlq_pending_overload_threshold):
            overloaded_reasons.append("dlq_pending_exceeded")
        elif dlq_retryable_backlog >= int(settings.dlq_pending_warn_threshold):
            degraded_reasons.append("dlq_pending_warning")
        if dlq_non_retryable_pending >= int(settings.dlq_non_retryable_pending_warn_threshold):
            degraded_reasons.append("dlq_non_retryable_pending_present")
        if settings.app_role == "api" and ready_workers == 0:
            degraded_reasons.append("no_ready_workers")
        influx_state, influx_error = await _influx_health_status()
        if influx_state != "connected":
            degraded_reasons.append("influx_unavailable")
            dependency_reasons.append("influx_unavailable")
        if settings.app_role == "api":
            mqtt_connected = bool(app_state.mqtt_handler and app_state.mqtt_handler.is_connected)
            mqtt_state = "connected" if mqtt_connected else "disconnected"
            if not mqtt_connected:
                mqtt_error = "mqtt_handler_disconnected"
                degraded_reasons.append("mqtt_disconnected")
                dependency_reasons.append("mqtt_disconnected")
        if overloaded_reasons:
            telemetry_status = "overloaded"
            telemetry_policy = {"state": "overloaded", "reasons": overloaded_reasons}
        elif degraded_reasons:
            telemetry_status = "degraded"
            telemetry_policy = {"state": "degraded", "reasons": degraded_reasons}
    elif app_state.telemetry_service is None:
        telemetry_status = "degraded"
        telemetry_policy = {"state": "degraded", "reasons": ["telemetry_service_unavailable"]}
    return {
        "status": telemetry_status,
        "service": "data-service",
        "version": settings.app_version,
        "circuit_breakers": get_circuit_breaker_metrics(),
        "telemetry": telemetry_stats,
        "telemetry_policy": telemetry_policy,
        "dlq_retry": dlq_retry_stats,
        "dependencies": {
            "redis": {"status": redis_state, "error": redis_error},
            "influxdb": {"status": influx_state, "error": influx_error},
            "mqtt": {"status": mqtt_state, "error": mqtt_error},
        },
        "dependency_reasons": list(dict.fromkeys(dependency_reasons)),
    }


signal.signal(signal.SIGTERM, handle_signal)
signal.signal(signal.SIGINT, handle_signal)


if __name__ == "__main__":
    import uvicorn

    logger.info(
        "Starting Data Service server",
        host=settings.host,
        port=settings.port,
    )

    uvicorn.run(
        "src.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        reload=settings.environment == "development",
    )
