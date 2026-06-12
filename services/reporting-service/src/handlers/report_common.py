from typing import Any, Literal, Optional
from io import BytesIO

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.database import get_db
from src.models import ReportStatus
from src.rate_limit import limiter
from src.repositories.report_repository import ReportRepository
from src.repositories.scheduled_repository import ScheduledRepository
from src.services.report_revision_service import create_corrected_report_revision
from src.services.job_runtime import (
    ReportJobStatusEstimator,
    artifact_download_path,
    external_report_status,
    result_path,
)
from src.services.device_scope import ReportingDeviceScopeService
from src.services.report_scope import normalize_schedule_params_template
from src.services.tenant_scope import build_service_tenant_context, normalize_tenant_id
from src.storage.minio_client import minio_client, StorageError
from services.shared.tenant_context import TenantContext, resolve_request_tenant_id

router = APIRouter(tags=["reports"])
def _artifact_ready(report) -> bool:
    return bool(getattr(report, "s3_key", None))


def _result_ready(report) -> bool:
    normalized_status = external_report_status(report.status)
    return normalized_status == "completed" or _artifact_ready(report)


def _coverage_result(report) -> dict[str, Any] | None:
    payload = getattr(report, "result_json", None)
    if not isinstance(payload, dict):
        return None
    coverage = payload.get("coverage_result") or (payload.get("data_quality") or {}).get("coverage")
    return coverage if isinstance(coverage, dict) else None


def _report_result_unavailable_detail(report) -> dict[str, Any]:
    normalized_status = external_report_status(report.status)
    failed = normalized_status == "failed"
    return {
        "error": "RESULT_UNAVAILABLE" if failed else "RESULT_NOT_READY",
        "message": (
            "Report result is unavailable because generation failed."
            if failed
            else "Report result is not ready yet."
        ),
        "status": normalized_status,
        "report_id": report.report_id,
        "result_ready": False,
        "artifact_ready": _artifact_ready(report),
        "download_ready": _artifact_ready(report),
        "error_code": getattr(report, "error_code", None),
        "error_message": getattr(report, "error_message", None),
        "coverage_result": _coverage_result(report),
    }


def _report_download_unavailable_detail(report) -> dict[str, Any]:
    return {
        "error": "DOWNLOAD_NOT_READY",
        "message": "Report download is not ready yet.",
        "status": external_report_status(report.status),
        "report_id": report.report_id,
        "result_ready": _result_ready(report),
        "artifact_ready": False,
        "download_ready": False,
        "error_code": getattr(report, "error_code", None),
        "error_message": getattr(report, "error_message", None),
        "coverage_result": _coverage_result(report),
    }


async def _build_job_summary(report, db: AsyncSession) -> dict[str, Any]:
    estimate = await ReportJobStatusEstimator(db).estimate(report)
    download_url = artifact_download_path(report.report_id) if _artifact_ready(report) else None
    return {
        "report_id": report.report_id,
        "status": external_report_status(report.status),
        "backend_status": report.status.value if hasattr(report.status, "value") else str(report.status),
        "report_type": report.report_type.value if hasattr(report.report_type, "value") else str(report.report_type),
        "progress": getattr(report, "progress", 0),
        "phase": getattr(report, "phase", None),
        "phase_label": getattr(report, "phase_label", None),
        "phase_progress": getattr(report, "phase_progress", None),
        "queue_position": estimate.queue_position,
        "estimated_wait_seconds": estimate.estimated_wait_seconds,
        "estimated_completion_seconds": estimate.estimated_completion_seconds,
        "estimate_quality": estimate.estimate_quality,
        "result_ready": _result_ready(report),
        "artifact_ready": _artifact_ready(report),
        "download_ready": _artifact_ready(report),
        "result_url": result_path(report.report_id),
        "download_url": download_url,
        "error_code": getattr(report, "error_code", None),
        "error_message": getattr(report, "error_message", None),
        "coverage_result": _coverage_result(report),
        "created_at": report.created_at.isoformat() if report.created_at else None,
        "started_at": report.processing_started_at.isoformat() if report.processing_started_at else None,
        "completed_at": report.completed_at.isoformat() if report.completed_at else None,
    }


class ScheduleCreateRequest(BaseModel):
    report_type: Literal["consumption", "comparison"]
    frequency: Literal["daily", "weekly", "monthly"]
    params_template: dict[str, Any] = Field(default_factory=dict)


class CorrectedRevisionRequest(BaseModel):
    report_id: str
    revision_reason: str = Field(min_length=1)
    generated_from_reconciliation_run_id: str | None = None


def _resolve_request_tenant_id(request: Request, tenant_id: str | None = None) -> str:
    resolved = normalize_tenant_id(resolve_request_tenant_id(request, explicit_tenant_id=tenant_id))
    if resolved is None:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "TENANT_SCOPE_REQUIRED",
                "message": "Tenant scope is required.",
            },
        )
    return resolved


async def _resolve_accessible_device_ids(request: Request) -> list[str] | None:
    ctx = TenantContext.from_request(request)
    try:
        return await ReportingDeviceScopeService(ctx).resolve_accessible_device_ids()
    except HTTPException:
        raise
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "DEVICE_SERVICE_UNAVAILABLE",
                "message": str(exc),
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


@router.get("/history")
async def list_reports(
    request: Request,
    tenant_id: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    report_type: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db)
):
    tenant_id = _resolve_request_tenant_id(request, tenant_id)
    repo = ReportRepository(db, ctx=build_service_tenant_context(tenant_id))
    accessible_device_ids = await _resolve_accessible_device_ids(request)
    reports = await repo.list_reports(
        tenant_id,
        limit,
        offset,
        report_type,
        accessible_device_ids=accessible_device_ids,
    )
    
    return {"reports": [await _build_job_summary(r, db) for r in reports]}


@router.post("/schedules")
@limiter.limit(settings.REPORT_SCHEDULE_RATE_LIMIT)
async def create_schedule(
    request: Request,
    data: ScheduleCreateRequest,
    tenant_id: str | None = Query(None),
    db: AsyncSession = Depends(get_db)
):
    tenant_id = _resolve_request_tenant_id(request, tenant_id)
    accessible_device_ids = await _resolve_accessible_device_ids(request)
    repo = ScheduledRepository(db, ctx=build_service_tenant_context(tenant_id))
    payload = data.model_dump()
    payload["tenant_id"] = tenant_id
    try:
        payload["params_template"] = normalize_schedule_params_template(
            payload.get("params_template", {}),
            accessible_device_ids,
        )
        schedule = await repo.create_schedule(payload)
    except PermissionError as exc:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "SCHEDULE_SCOPE_FORBIDDEN",
                "message": str(exc),
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "VALIDATION_ERROR",
                "message": str(exc),
            },
        ) from exc
    
    return {
        "schedule_id": schedule.schedule_id,
        "tenant_id": schedule.tenant_id,
        "report_type": schedule.report_type.value if hasattr(schedule.report_type, 'value') else str(schedule.report_type),
        "frequency": schedule.frequency.value if hasattr(schedule.frequency, 'value') else str(schedule.frequency),
        "is_active": schedule.is_active,
        "next_run_at": schedule.next_run_at.isoformat() if schedule.next_run_at else None,
        "created_at": schedule.created_at.isoformat()
    }


@router.get("/schedules")
async def list_schedules(
    request: Request,
    tenant_id: str | None = Query(None),
    db: AsyncSession = Depends(get_db)
):
    tenant_id = _resolve_request_tenant_id(request, tenant_id)
    repo = ScheduledRepository(db, ctx=build_service_tenant_context(tenant_id))
    accessible_device_ids = await _resolve_accessible_device_ids(request)
    schedules = await repo.list_schedules(tenant_id, accessible_device_ids=accessible_device_ids)
    
    return {
        "schedules": [
            {
                "schedule_id": s.schedule_id,
                "tenant_id": s.tenant_id,
                "report_type": s.report_type.value if hasattr(s.report_type, 'value') else str(s.report_type),
                "frequency": s.frequency.value if hasattr(s.frequency, 'value') else str(s.frequency),
                "is_active": s.is_active,
                "last_run_at": s.last_run_at.isoformat() if s.last_run_at else None,
                "next_run_at": s.next_run_at.isoformat() if s.next_run_at else None,
                "last_status": s.last_status,
                "last_result_url": s.last_result_url,
                "params_template": s.params_template
            }
            for s in schedules
        ]
    }


@router.delete("/schedules/{schedule_id}")
async def delete_schedule(
    schedule_id: str,
    request: Request,
    tenant_id: str | None = Query(None),
    db: AsyncSession = Depends(get_db)
):
    tenant_id = _resolve_request_tenant_id(request, tenant_id)
    repo = ScheduledRepository(db, ctx=build_service_tenant_context(tenant_id))
    accessible_device_ids = await _resolve_accessible_device_ids(request)
    schedule = await repo.get_schedule(schedule_id, accessible_device_ids=accessible_device_ids)
    
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")
    
    if schedule.tenant_id != tenant_id:
        raise HTTPException(status_code=403, detail="Access denied")
    
    await repo.update_schedule(schedule_id, is_active=False)
    
    return {"message": "Schedule deactivated"}


@router.get("/{report_id}/status")
async def get_report_status(
    report_id: str,
    request: Request,
    tenant_id: str | None = Query(None),
    db: AsyncSession = Depends(get_db)
):
    tenant_id = _resolve_request_tenant_id(request, tenant_id)
    repo = ReportRepository(db, ctx=build_service_tenant_context(tenant_id))
    accessible_device_ids = await _resolve_accessible_device_ids(request)
    report = await repo.get_report(report_id, tenant_id, accessible_device_ids=accessible_device_ids)
    
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    
    return await _build_job_summary(report, db)


@router.get("/{report_id}/result")
async def get_report_result(
    report_id: str,
    request: Request,
    tenant_id: str | None = Query(None),
    db: AsyncSession = Depends(get_db)
):
    tenant_id = _resolve_request_tenant_id(request, tenant_id)
    repo = ReportRepository(db, ctx=build_service_tenant_context(tenant_id))
    accessible_device_ids = await _resolve_accessible_device_ids(request)
    report = await repo.get_report(report_id, tenant_id, accessible_device_ids=accessible_device_ids)
    
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    
    if report.status != ReportStatus.completed:
        raise HTTPException(
            status_code=409,
            detail=_report_result_unavailable_detail(report),
        )
    
    return report.result_json


@router.post("/internal/revisions/corrected")
async def create_corrected_revision(
    payload: CorrectedRevisionRequest,
    request: Request,
    tenant_id: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _resolve_request_tenant_id(request, tenant_id)
    result = await create_corrected_report_revision(
        db=db,
        report_id=payload.report_id,
        tenant_id=tenant_id,
        revision_reason=payload.revision_reason,
        generated_from_reconciliation_run_id=payload.generated_from_reconciliation_run_id,
    )
    return result


@router.get("/{report_id}/download")
async def download_report(
    report_id: str,
    request: Request,
    tenant_id: str | None = Query(None),
    db: AsyncSession = Depends(get_db)
):
    tenant_id = _resolve_request_tenant_id(request, tenant_id)
    repo = ReportRepository(db, ctx=build_service_tenant_context(tenant_id))
    accessible_device_ids = await _resolve_accessible_device_ids(request)
    report = await repo.get_report(report_id, tenant_id, accessible_device_ids=accessible_device_ids)
    
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    
    if not report.s3_key:
        raise HTTPException(status_code=409, detail=_report_download_unavailable_detail(report))
    
    try:
        pdf_bytes = await minio_client.async_download_pdf(report.s3_key)
        return StreamingResponse(
            BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={
                "Content-Disposition": f"attachment; filename=energy_report_{report_id}.pdf",
                "Content-Length": str(len(pdf_bytes))
            }
        )
    except StorageError:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "PDF_NOT_FOUND",
                "message": "Report file not available",
            },
        )
