from datetime import datetime, date
import hashlib
import json
import logging
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.database import get_db
from src.queue import ReportJob, get_report_queue
from src.rate_limit import limiter
from src.schemas.requests import ConsumptionReportRequest
from src.schemas.responses import ReportResponse
from src.repositories.report_repository import ReportRepository
from src.services.job_runtime import count_active_workers, external_report_status, artifact_download_path, result_path
from src.services.device_scope import ReportingDeviceScopeService
from src.services.tenant_scope import build_service_tenant_context, normalize_tenant_id
from src.utils.localization import local_date_bounds_to_utc
from services.shared.tenant_context import TenantContext, resolve_request_tenant_id

router = APIRouter(tags=["energy-reports"])

logger = logging.getLogger(__name__)


def get_tenant_id(request: Request) -> str | None:
    return resolve_request_tenant_id(request)


def resolve_submission_tenant_id(app_request: Request, body_tenant_id: str | None) -> str:
    resolved = normalize_tenant_id(
        resolve_request_tenant_id(app_request, explicit_tenant_id=body_tenant_id)
    )
    if resolved is None:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "TENANT_SCOPE_REQUIRED",
                "message": "Tenant scope is required to submit a report.",
            },
        )
    return resolved


async def resolve_all_devices(ctx: TenantContext) -> list[str]:
    import logging

    logger = logging.getLogger(__name__)
    try:
        device_ids = await ReportingDeviceScopeService(ctx).resolve_accessible_device_ids()
    except httpx.RequestError as exc:
        logger.error("report_device_scope_request_failed", extra={"error": str(exc)})
        raise HTTPException(
            status_code=503,
            detail={
                "error": "DEVICE_SERVICE_UNAVAILABLE",
                "message": f"Cannot connect to device service: {str(exc)}",
            },
        ) from exc
    except httpx.HTTPStatusError as exc:
        logger.error("report_device_scope_http_error", extra={"status_code": exc.response.status_code})
        raise HTTPException(
            status_code=502,
            detail={
                "error": "DEVICE_SERVICE_ERROR",
                "message": f"Device service returned status {exc.response.status_code}",
            },
        ) from exc
    except RuntimeError as exc:
        logger.error("report_device_scope_runtime_error", extra={"error": str(exc)})
        raise HTTPException(
            status_code=503,
            detail={
                "error": "DEVICE_SCOPE_UNAVAILABLE",
                "message": str(exc),
            },
        ) from exc

    logger.info("Resolved %s accessible devices for tenant %s", len(device_ids), ctx.require_tenant())
    return device_ids


def normalize_dates_to_utc(start_date: date, end_date: date) -> tuple[datetime, datetime]:
    """
    Normalize dates to UTC with day-boundary alignment.
    - Floor start to 00:00:00 in platform local time
    - Ceil end to 23:59:59.999999 in platform local time
    - Convert both bounds to UTC for storage/querying
    Returns tuple of (start_datetime, end_datetime)
    """
    return local_date_bounds_to_utc(start_date, end_date)


def validate_date_duration_seconds(start_dt: datetime, end_dt: datetime, min_seconds: int = 86400) -> bool:
    """
    Validate duration using seconds instead of days to avoid timezone drift.
    min_seconds defaults to 86400 (24 hours).
    """
    # normalize_dates_to_utc() uses an inclusive end-of-day timestamp
    # (23:59:59.999999). Treat that inclusive window as a full calendar day
    # when enforcing the minimum duration contract.
    duration_seconds = (end_dt - start_dt).total_seconds() + 1e-6
    return duration_seconds >= min_seconds


def validate_date_range_days(start_dt: datetime, end_dt: datetime, max_days: int = 90) -> bool:
    duration_days = (end_dt.date() - start_dt.date()).days + 1
    return duration_days <= max_days


async def validate_device_for_reporting(device_id: str, ctx: TenantContext) -> dict:
    """
    Validate device exists.
    Returns device data if valid.
    Raises HTTPException if invalid.
    """
    try:
        return await ReportingDeviceScopeService(ctx).validate_accessible_device(device_id)
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "DEVICE_SERVICE_UNAVAILABLE",
                "message": f"Cannot connect to device service: {str(exc)}",
            },
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "error": "DEVICE_SERVICE_ERROR",
                "message": f"Device service returned status {exc.response.status_code}",
            },
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "DEVICE_SCOPE_UNAVAILABLE",
                "message": str(exc),
            },
        ) from exc


async def enforce_report_admission(db: AsyncSession, tenant_id: str) -> int:
    active_workers = await count_active_workers(db)
    if active_workers < 1:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "WORKER_UNAVAILABLE",
                "message": "Reporting worker is unavailable. Please retry shortly.",
            },
        )

    try:
        queue_metrics = await get_report_queue().metrics()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "QUEUE_UNAVAILABLE",
                "message": f"Reporting queue is unavailable: {exc}",
            },
        ) from exc
    queue_depth = int(queue_metrics.get("queue_depth", 0))
    if queue_depth >= settings.REPORT_QUEUE_REJECT_THRESHOLD:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "QUEUE_OVERLOADED",
                "message": "Reporting queue is overloaded. Please retry shortly.",
                "queue_depth": queue_depth,
            },
        )

    repo = ReportRepository(db, ctx=build_service_tenant_context(tenant_id))
    status_counts = await repo.count_by_status(tenant_id=tenant_id)
    pending_count = int(status_counts.get("pending", 0))
    if pending_count >= settings.REPORT_TENANT_MAX_PENDING_JOBS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": "TENANT_QUEUE_LIMIT_REACHED",
                "message": "Too many pending reports for this tenant. Please wait for current jobs to finish.",
                "pending_jobs": pending_count,
            },
        )

    active_count = await repo.count_active_jobs_for_tenant(tenant_id)
    if active_count >= settings.REPORT_TENANT_MAX_ACTIVE_JOBS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": "TENANT_ACTIVE_CAP_EXCEEDED",
                "message": f"Tenant has {active_count} active/processing reports (limit: {settings.REPORT_TENANT_MAX_ACTIVE_JOBS}). Please wait for current jobs to finish.",
                "active_jobs": active_count,
            },
        )

    return pending_count


def build_report_dedup_signature(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def build_duplicate_report_response(duplicate) -> ReportResponse:
    dup_status = external_report_status(duplicate.status)
    artifact_ready = bool(duplicate.s3_key)
    return ReportResponse(
        report_id=duplicate.report_id,
        status=dup_status,
        created_at=duplicate.created_at.isoformat() if duplicate.created_at else datetime.utcnow().isoformat(),
        queue_position=0 if dup_status == "pending" else None,
        estimated_wait_seconds=15 if dup_status == "pending" else None,
        estimated_completion_seconds=15,
        result_ready=dup_status == "completed",
        artifact_ready=artifact_ready,
        download_url=artifact_download_path(duplicate.report_id) if artifact_ready else None,
        result_url=result_path(duplicate.report_id),
    )


async def enqueue_report_or_mark_failed(
    *,
    repo: ReportRepository,
    report_id: str,
    tenant_id: str,
    report_type: str,
) -> None:
    try:
        await get_report_queue().enqueue(
            ReportJob(
                report_id=report_id,
                tenant_id=tenant_id,
                report_type=report_type,
            ),
        )
    except Exception:
        logger.exception("report_enqueue_failed report_id=%s", report_id)
        await repo.update_report(
            report_id,
            tenant_id=tenant_id,
            status="enqueue_failed",
            error_code="ENQUEUE_FAILED",
            error_message="Report created but queue enqueue failed; will be retried on next startup",
        )
        raise HTTPException(
            status_code=503,
            detail={
                "error": "ENQUEUE_FAILED",
                "message": "Report was created but could not be enqueued. It will be retried automatically.",
                "code": "ENQUEUE_FAILED",
                "report_id": report_id,
            },
        ) from None


@router.post("/consumption", response_model=ReportResponse)
@limiter.limit(settings.REPORT_SUBMIT_RATE_LIMIT)
async def create_energy_consumption_report(
    request: Request,
    body: ConsumptionReportRequest,
    db: AsyncSession = Depends(get_db)
):
    import logging
    logger = logging.getLogger(__name__)
    
    logger.info("="*60)
    logger.info("ENERGY REPORT REQUEST RECEIVED")
    logger.info(f"  start_date: {body.start_date}")
    logger.info(f"  end_date: {body.end_date}")
    logger.info(f"  device_id: {body.device_id}")
    logger.info(f"  tenant_id: {body.tenant_id}")
    logger.info("="*60)

    tenant_id = resolve_submission_tenant_id(request, body.tenant_id)
    body.tenant_id = tenant_id
    tenant_ctx = build_service_tenant_context(tenant_id)
    request_ctx = TenantContext.from_request(request)
    
    request_device_id = (body.device_id or "").strip()
    if not request_device_id:
        raise HTTPException(
            status_code=400,
            detail={"error": "VALIDATION_ERROR", "message": "device_id is required"}
        )

    device_ids: list[str] = []
    if request_device_id.upper() == "ALL":
        logger.info("Received 'all' device selection, resolving to actual device IDs")
        resolved_ids = await resolve_all_devices(request_ctx)
        
        if not resolved_ids:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "NO_VALID_DEVICES",
                    "message": "No energy-capable devices found for this tenant."
                }
            )
        
        device_ids = resolved_ids
        logger.info(f"Resolved device IDs: {device_ids}")
    else:
        await validate_device_for_reporting(request_device_id, request_ctx)
        device_ids = [request_device_id]
    
    start_dt, end_dt = normalize_dates_to_utc(body.start_date, body.end_date)
    duration_seconds = (end_dt - start_dt).total_seconds()
    
    logger.info("="*60)
    logger.info("DATE NORMALIZATION RESULTS")
    logger.info(f"  Original start: {body.start_date}")
    logger.info(f"  Original end: {body.end_date}")
    logger.info(f"  UTC start_dt: {start_dt}")
    logger.info(f"  UTC end_dt: {end_dt}")
    logger.info(f"  Duration seconds: {duration_seconds}")
    logger.info(f"  Duration days: {duration_seconds / 86400}")
    logger.info("="*60)
    
    if not validate_date_duration_seconds(start_dt, end_dt):
        logger.error(f"Date validation FAILED: duration {duration_seconds} seconds < 86400")
        raise HTTPException(
            status_code=400,
            detail={
                "error": "INVALID_DATE_RANGE",
                "message": f"Date range must be at least 24 hours apart. Current: {duration_seconds/86400:.1f} days"
            }
        )
    if not validate_date_range_days(start_dt, end_dt):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "INVALID_DATE_RANGE",
                "message": "Invalid date range. Please select a date range between 1-90 days within the last year.",
            },
        )
    
    repo = ReportRepository(db, ctx=tenant_ctx)
    dedup_payload = {
        "tenant_id": tenant_id,
        "report_type": "consumption",
        "device_id": request_device_id.upper() if request_device_id.upper() == "ALL" else request_device_id,
        "resolved_device_ids": sorted(device_ids),
        "start_date": str(body.start_date),
        "end_date": str(body.end_date),
        "report_name": body.report_name or "",
    }
    dedup_signature = build_report_dedup_signature(dedup_payload)

    duplicate = await repo.find_active_duplicate(
        tenant_id=tenant_id,
        report_type="consumption",
        dedup_signature=dedup_signature,
    )
    if duplicate:
        return build_duplicate_report_response(duplicate)

    queue_position = await enforce_report_admission(db, tenant_id)
    
    report_id = str(uuid4())
    
    params = body.model_dump()
    params["start_date"] = str(params["start_date"])
    params["end_date"] = str(params["end_date"])
    params["resolved_device_ids"] = device_ids
    params["dedup_signature"] = dedup_signature
    
    await repo.create_report(
        report_id=report_id,
        tenant_id=tenant_id,
        report_type="consumption",
        params=params
    )
    await enqueue_report_or_mark_failed(
        repo=repo,
        report_id=report_id,
        tenant_id=tenant_id,
        report_type="consumption",
    )
    
    return ReportResponse(
        report_id=report_id,
        status="pending",
        created_at=datetime.utcnow().isoformat(),
        queue_position=queue_position,
        estimated_wait_seconds=(queue_position + 1) * 15,
        estimated_completion_seconds=15,
        result_ready=False,
        artifact_ready=False,
        download_url=None,
        result_url=result_path(report_id),
    )
