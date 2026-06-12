
"""Data Export Service - Main FastAPI Application.

Provides minimal health/readiness endpoints and manages the continuous
export worker lifecycle.
"""

import signal
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Body, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from config import get_settings
from logging_config import get_logger, setup_logging
from rate_limit import configure_rate_limiting, limiter
from worker import ExportWorker
from services.shared.auth_middleware import AuthMiddleware
from services.shared.debug_bootstrap import init_debug
from services.shared.startup_contract import validate_startup_contract
from services.shared.tenant_context import TenantContext

init_debug()


# Setup structured logging
setup_logging()
logger = get_logger(__name__)

# Global worker instance
_worker: ExportWorker | None = None


class HealthResponse(BaseModel):
    status: str
    version: str
    timestamp: str


class ReadyResponse(BaseModel):
    ready: bool
    checks: dict


# -------------------------------
# NEW – export trigger request
# -------------------------------

class ExportRequest(BaseModel):
    device_id: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    request_id: Optional[str] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _worker

    validate_startup_contract()
    settings = get_settings()

    logger.info(
        "Starting Data Export Service",
        extra={
            "version": settings.service_version,
            "environment": settings.environment,
        },
    )

    _worker = ExportWorker(settings)
    await _worker.start()

    logger.info("Export worker started successfully")

    try:
        yield
    finally:
        logger.info("Shutting down Data Export Service...")

        if _worker:
            await _worker.stop()

        logger.info("Data Export Service shutdown complete")


app = FastAPI(
    title="Data Export Service",
    description="Continuous telemetry data export to S3 for analytics",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)
configure_rate_limiting(app)
app.add_middleware(AuthMiddleware)


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
    logger.exception("Unhandled exception in data-export-service")
    return JSONResponse(
        status_code=500,
        content={
            "error": "INTERNAL_ERROR",
            "message": "Unexpected server error",
            "code": "INTERNAL_ERROR",
        },
    )


@app.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    settings = get_settings()

    return HealthResponse(
        status="healthy",
        version=settings.service_version,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


@app.get("/ready", response_model=ReadyResponse)
async def readiness_check() -> ReadyResponse:
    checks = {
        "worker_running": _worker is not None and _worker.is_running(),
        "checkpoint_store_connected": await _check_checkpoint_store(),
        "s3_accessible": await _check_s3_access(),
    }

    ready = all(checks.values())

    if not ready:
        raise HTTPException(
            status_code=503,
            detail={"ready": False, "checks": checks},
        )

    return ReadyResponse(ready=True, checks=checks)


# -------------------------------------------------
# NEW – on-demand export trigger
# -------------------------------------------------

@app.post("/api/v1/exports/run")
@limiter.limit(get_settings().export_run_rate_limit)
async def run_export(request: Request, req: ExportRequest = Body(...)):
    if not _worker or not _worker.is_running():
        raise HTTPException(
            status_code=503,
            detail="Export worker is not running",
        )

    try:
        tenant_id = TenantContext.from_request(request).require_tenant()
        if (req.start_time is None) ^ (req.end_time is None):
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "VALIDATION_ERROR",
                    "code": "VALIDATION_ERROR",
                    "message": "start_time and end_time must be provided together",
                },
            )
        if req.start_time and req.end_time and req.end_time <= req.start_time:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "VALIDATION_ERROR",
                    "code": "VALIDATION_ERROR",
                    "message": "end_time must be after start_time",
                },
            )

        settings = get_settings()
        if req.start_time and req.end_time:
            window_hours = (req.end_time - req.start_time).total_seconds() / 3600.0
            if window_hours > settings.max_force_export_window_hours:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "error": "VALIDATION_ERROR",
                        "code": "VALIDATION_ERROR",
                        "message": (
                            f"Requested export window exceeds max allowed "
                            f"{settings.max_force_export_window_hours} hours"
                        ),
                        "max_force_export_window_hours": settings.max_force_export_window_hours,
                    },
                )

        device_ids: list[str] | None = None
        if req.device_id:
            await _require_device_scope(req.device_id, tenant_id)
        else:
            device_ids = await _worker.checkpoint_store.list_tenant_device_ids(
                tenant_id,
                _worker.settings.get_device_ids(),
            )
            if not device_ids:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "error": "NO_EXPORTABLE_DEVICES",
                        "code": "NO_EXPORTABLE_DEVICES",
                        "message": "No configured export devices are available for this tenant.",
                    },
                )

        await _worker.force_export(
            device_id=req.device_id,
            device_ids=device_ids,
            start_time=req.start_time,
            end_time=req.end_time,
        )

        return {
            "status": "accepted",
            "device_id": req.device_id,
            "device_ids": device_ids,
            "request_id": req.request_id,
            "mode": "forced_range" if req.start_time and req.end_time else "force_full",
            "start_time": req.start_time.isoformat() if req.start_time else None,
            "end_time": req.end_time.isoformat() if req.end_time else None,
        }

    except HTTPException:
        raise
    except Exception:
        logger.exception("On-demand export failed")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "EXPORT_TRIGGER_FAILED",
                "message": "Failed to trigger export",
            },
        )


# -------------------------------------------------
# NEW – export status
# -------------------------------------------------

@app.get("/api/v1/exports/status/{device_id}")
async def get_export_status(device_id: str, request: Request):
    if not _worker:
        raise HTTPException(
            status_code=503,
            detail="Export worker is not running",
        )

    tenant_id = TenantContext.from_request(request).require_tenant()
    await _require_device_scope(device_id, tenant_id)
    return await _worker.exporter.get_export_status(device_id)


async def _require_device_scope(device_id: str, tenant_id: str) -> None:
    if not _worker:
        raise HTTPException(status_code=503, detail="Export worker is not running")

    if await _worker.checkpoint_store.device_belongs_to_tenant(device_id, tenant_id):
        return

    raise HTTPException(
        status_code=404,
        detail={
            "error": "DEVICE_NOT_FOUND",
            "code": "DEVICE_NOT_FOUND",
            "message": "Device not found in the authenticated tenant scope.",
        },
    )


async def _check_checkpoint_store() -> bool:
    if not _worker:
        return False

    try:
        await _worker.checkpoint_store.health_check()
        return True
    except Exception as e:
        logger.warning(f"Checkpoint store health check failed: {e}")
        return False


async def _check_s3_access() -> bool:
    if not _worker:
        return False

    try:
        await _worker.s3_writer.health_check()
        return True
    except Exception as e:
        logger.warning(f"S3 health check failed: {e}")
        return False


def _signal_handler(sig: int, frame) -> None:
    logger.info(f"Received signal {sig}, initiating graceful shutdown...")
    sys.exit(0)


signal.signal(signal.SIGTERM, _signal_handler)
signal.signal(signal.SIGINT, _signal_handler)


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()

    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        access_log=False,
    )
