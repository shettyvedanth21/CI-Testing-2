import hashlib
import json
from datetime import datetime, date
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.database import get_db
from src.queue import ReportJob, get_report_queue
from src.rate_limit import limiter
from src.schemas.requests import ComparisonReportRequest
from src.schemas.responses import ReportResponse
from src.repositories.report_repository import ReportRepository
from src.services.job_runtime import result_path
from src.services.tenant_scope import build_service_tenant_context, normalize_tenant_id
from src.handlers.energy_reports import (
    build_duplicate_report_response,
    build_report_dedup_signature,
    enqueue_report_or_mark_failed,
    enforce_report_admission,
    resolve_submission_tenant_id,
    resolve_all_devices,
    normalize_dates_to_utc,
    validate_date_range_days,
    validate_date_duration_seconds,
    validate_device_for_reporting,
)
from services.shared.tenant_context import TenantContext

router = APIRouter(tags=["comparison-reports"])
import logging
logger = logging.getLogger(__name__)


def convert_dates_to_str(obj):
    if isinstance(obj, date):
        return obj.isoformat()
    elif isinstance(obj, dict):
        return {k: convert_dates_to_str(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [convert_dates_to_str(item) for item in obj]
    return obj


@router.post("", response_model=ReportResponse)
@router.post("/", response_model=ReportResponse)
@limiter.limit(settings.REPORT_SUBMIT_RATE_LIMIT)
async def create_comparison_report(
    request: Request,
    body: ComparisonReportRequest,
    db: AsyncSession = Depends(get_db)
):
    tenant_id = normalize_tenant_id(resolve_submission_tenant_id(request, body.tenant_id))
    if tenant_id is None:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "TENANT_SCOPE_REQUIRED",
                "message": "Tenant scope is required to submit a comparison report.",
            },
        )
    body.tenant_id = tenant_id
    tenant_ctx = build_service_tenant_context(tenant_id)
    request_ctx = TenantContext.from_request(request)
    repo = ReportRepository(db, ctx=tenant_ctx)
    queue_position = await enforce_report_admission(db, tenant_id)
    
    if body.comparison_type == "machine_vs_machine":
        machine_a_id = body.machine_a_id
        machine_b_id = body.machine_b_id
        
        if machine_a_id == "all" or machine_b_id == "all":
            resolved_a = await resolve_all_devices(request_ctx)
            resolved_b = await resolve_all_devices(request_ctx)
            
            if not resolved_a or not resolved_b:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": "NO_VALID_DEVICES",
                        "message": "No energy-capable devices found for this tenant."
                    }
                )
            
            machine_a_id = resolved_a[0] if resolved_a else None
            machine_b_id = resolved_b[0] if resolved_b else None
        
        if machine_a_id:
            await validate_device_for_reporting(machine_a_id, request_ctx)
        if machine_b_id:
            await validate_device_for_reporting(machine_b_id, request_ctx)
        
        if body.start_date and body.end_date:
            start_dt, end_dt = normalize_dates_to_utc(body.start_date, body.end_date)
            if not validate_date_duration_seconds(start_dt, end_dt):
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": "INVALID_DATE_RANGE",
                        "message": "Date range must be at least 24 hours apart."
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
    elif body.comparison_type == "period_vs_period":
        if body.period_a_start and body.period_a_end:
            period_a_start_dt, period_a_end_dt = normalize_dates_to_utc(body.period_a_start, body.period_a_end)
            if not validate_date_range_days(period_a_start_dt, period_a_end_dt):
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": "INVALID_DATE_RANGE",
                        "message": "Invalid date range. Please select a date range between 1-90 days within the last year.",
                    },
                )
        if body.period_b_start and body.period_b_end:
            period_b_start_dt, period_b_end_dt = normalize_dates_to_utc(body.period_b_start, body.period_b_end)
            if not validate_date_range_days(period_b_start_dt, period_b_end_dt):
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": "INVALID_DATE_RANGE",
                        "message": "Invalid date range. Please select a date range between 1-90 days within the last year.",
                    },
                )
    
    params = body.model_dump()
    params = convert_dates_to_str(params)
    dedup_payload = {
        "tenant_id": tenant_id,
        "comparison_type": body.comparison_type,
        "machine_a_id": machine_a_id if body.comparison_type == "machine_vs_machine" else None,
        "machine_b_id": machine_b_id if body.comparison_type == "machine_vs_machine" else None,
        "device_id": body.device_id if body.comparison_type == "period_vs_period" else None,
        "start_date": str(body.start_date) if body.start_date else None,
        "end_date": str(body.end_date) if body.end_date else None,
        "period_a_start": str(body.period_a_start) if body.period_a_start else None,
        "period_a_end": str(body.period_a_end) if body.period_a_end else None,
        "period_b_start": str(body.period_b_start) if body.period_b_start else None,
        "period_b_end": str(body.period_b_end) if body.period_b_end else None,
    }
    dedup_signature = build_report_dedup_signature(dedup_payload)
    duplicate = await repo.find_active_duplicate(
        tenant_id=tenant_id,
        report_type="comparison",
        dedup_signature=dedup_signature,
    )
    if duplicate:
        return build_duplicate_report_response(duplicate)

    report_id = str(uuid4())
    params["dedup_signature"] = dedup_signature
    
    await repo.create_report(
        report_id=report_id,
        tenant_id=tenant_id,
        report_type="comparison",
        params=params
    )
    await enqueue_report_or_mark_failed(
        repo=repo,
        report_id=report_id,
        tenant_id=tenant_id,
        report_type="comparison",
    )
    
    return ReportResponse(
        report_id=report_id,
        status="pending",
        created_at=datetime.utcnow().isoformat(),
        queue_position=queue_position,
        estimated_wait_seconds=(queue_position + 1) * 15,
        result_ready=False,
        artifact_ready=False,
        result_url=result_path(report_id),
    )
