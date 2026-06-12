from datetime import datetime
from io import BytesIO
from uuid import uuid4
import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.database import get_db
from src.repositories import WasteRepository
from src.schemas import (
    WasteAnalysisRunRequest,
    WasteAnalysisRunResponse,
    WasteDownloadResponse,
    WasteHistoryResponse,
    WasteStatusResponse,
)
from src.storage.minio_client import StorageError, minio_client
from src.queue import WasteJob, get_waste_queue
from src.utils.downloads import build_waste_download_path
from services.shared.tenant_context import TenantContext, normalize_tenant_id

router = APIRouter(tags=["waste-analysis"])
WASTE_MAX_RANGE_DAYS = 90

logger = logging.getLogger(__name__)


def get_request_context(request: Request) -> TenantContext:
    return TenantContext.from_request(request)


def get_tenant_id(request: Request) -> str:
    return get_request_context(request).require_tenant()


def _to_utc_iso(ts: datetime | None) -> str | None:
    if ts is None:
        return None
    return ts.replace(tzinfo=None).isoformat(timespec="seconds") + "Z"


def _validate_waste_date_range(start_date, end_date) -> None:
    duration_days = (end_date - start_date).days + 1
    if duration_days < 1 or duration_days > WASTE_MAX_RANGE_DAYS:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "INVALID_DATE_RANGE",
                "message": "Invalid date range. Please select a date range between 1-90 days within the last year.",
            },
        )


def _artifact_ready(job) -> bool:
    return bool(getattr(job, "s3_key", None))


def _result_ready(job) -> bool:
    payload = getattr(job, "result_json", None)
    if payload is None:
        return False
    if isinstance(payload, dict):
        return any(key != "tenant_id" for key in payload.keys())
    return True


def _artifact_recoverable(job) -> bool:
    return _result_ready(job) and getattr(job, "error_code", None) in {
        "ARTIFACT_GENERATION_FAILED",
        "ARTIFACT_UPLOAD_FAILED",
    }


def _download_ready(job) -> bool:
    return _artifact_ready(job) or _artifact_recoverable(job)


def _download_url(job) -> str | None:
    return build_waste_download_path(job.id) if _download_ready(job) else None


def _result_url(job) -> str:
    return f"/api/v1/waste/analysis/{job.id}/result"


def _coverage_result(job) -> dict | None:
    payload = getattr(job, "result_json", None)
    if not isinstance(payload, dict):
        return None
    coverage = payload.get("coverage_result")
    return coverage if isinstance(coverage, dict) else None


def _phase(job) -> str:
    status = job.status.value if hasattr(job.status, "value") else str(job.status)
    coverage = _coverage_result(job)
    if status == "completed" and isinstance(coverage, dict):
        level = coverage.get("level")
        if level in {"no_coverage", "insufficient_coverage"}:
            return str(level)
        if level == "partial_coverage":
            return "partial_coverage"
    if status == "pending":
        return "queued"
    if status == "completed":
        return "completed"
    if status == "failed":
        return "failed"
    progress = int(getattr(job, "progress_pct", 0) or 0)
    if progress < 10:
        return "preparing"
    if progress < 88:
        return "execution"
    return "artifact_generation"


def _phase_label(job) -> str | None:
    stage = getattr(job, "stage", None)
    if stage:
        return stage
    phase = _phase(job)
    defaults = {
        "queued": "Queued",
        "preparing": "Preparing analysis",
        "execution": "Running waste analysis",
        "artifact_generation": "Generating report artifact",
        "completed": "Completed",
        "partial_coverage": "Partial Coverage",
        "insufficient_coverage": "Insufficient Coverage",
        "no_coverage": "No Data",
        "failed": "Failed",
    }
    return defaults.get(phase)


def _phase_progress(job) -> float | None:
    progress = getattr(job, "progress_pct", None)
    if progress is None:
        return None
    return max(0.0, min(float(progress) / 100.0, 1.0))


def _requested_device_count(job) -> int | None:
    payload = getattr(job, "result_json", None)
    if isinstance(payload, dict):
        device_summaries = payload.get("device_summaries")
        skipped_devices = payload.get("skipped_devices")
        if isinstance(device_summaries, list) or isinstance(skipped_devices, list):
            return len(device_summaries or []) + len(skipped_devices or [])
    scope = job.scope.value if hasattr(job.scope, "value") else str(job.scope)
    if scope == "selected":
        return len(getattr(job, "device_ids", None) or [])
    return None


def _build_job_contract(job) -> dict:
    status = job.status.value if hasattr(job.status, "value") else str(job.status)
    download_url = _download_url(job)
    return {
        "job_id": job.id,
        "job_name": getattr(job, "job_name", None),
        "status": status,
        "backend_status": status,
        "progress_pct": int(getattr(job, "progress_pct", 0) or 0),
        "stage": getattr(job, "stage", None),
        "phase": _phase(job),
        "phase_label": _phase_label(job),
        "phase_progress": _phase_progress(job),
        "result_ready": _result_ready(job),
        "artifact_ready": _artifact_ready(job),
        "download_ready": _download_ready(job),
        "result_url": _result_url(job),
        "download_url": download_url,
        "coverage_result": _coverage_result(job),
        "error_code": getattr(job, "error_code", None),
        "error_message": getattr(job, "error_message", None),
        "created_at": _to_utc_iso(getattr(job, "created_at", None)),
        "started_at": _to_utc_iso(getattr(job, "started_at", None)),
        "completed_at": _to_utc_iso(getattr(job, "completed_at", None)),
        "scope": job.scope.value if hasattr(job.scope, "value") else str(job.scope),
        "start_date": job.start_date.isoformat() if getattr(job, "start_date", None) else None,
        "end_date": job.end_date.isoformat() if getattr(job, "end_date", None) else None,
        "granularity": job.granularity.value if hasattr(job.granularity, "value") else str(job.granularity),
        "requested_device_count": _requested_device_count(job),
    }


@router.post("/analysis/run", response_model=WasteAnalysisRunResponse, status_code=202)
async def run_analysis(
    app_request: Request,
    request: WasteAnalysisRunRequest,
    db: AsyncSession = Depends(get_db),
):
    ctx = get_request_context(app_request)
    tenant_id = ctx.require_tenant()
    if request.start_date > request.end_date:
        raise HTTPException(status_code=400, detail={"error": "VALIDATION_ERROR", "message": "start_date must be <= end_date"})
    _validate_waste_date_range(request.start_date, request.end_date)

    if request.scope == "selected" and not request.device_ids:
        raise HTTPException(status_code=400, detail={"error": "VALIDATION_ERROR", "message": "device_ids required when scope=selected"})

    repo = WasteRepository(db, ctx)
    duplicate = await repo.find_active_duplicate(
        tenant_id=tenant_id,
        scope=request.scope,
        device_ids=request.device_ids,
        start_date=request.start_date,
        end_date=request.end_date,
        granularity=request.granularity,
    )
    if duplicate:
        return WasteAnalysisRunResponse(
            **_build_job_contract(duplicate),
            estimated_completion_seconds=30,
        )

    global_pending = await repo.count_pending_jobs_global()
    if global_pending >= settings.WASTE_QUEUE_REJECT_THRESHOLD:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "QUEUE_BACKLOG_FULL",
                "message": "Waste analysis queue is at capacity. Please try again later.",
                "code": "QUEUE_BACKLOG_FULL",
            },
        )

    tenant_pending = await repo.count_pending_jobs_for_tenant(tenant_id)
    if tenant_pending >= settings.WASTE_TENANT_MAX_PENDING_JOBS:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "TENANT_QUEUE_CAPACITY_EXCEEDED",
                "message": f"Tenant has {tenant_pending} pending/running waste analysis jobs (limit: {settings.WASTE_TENANT_MAX_PENDING_JOBS}). Please wait for existing jobs to complete.",
                "code": "TENANT_QUEUE_CAPACITY_EXCEEDED",
            },
        )

    job_id = str(uuid4())
    await repo.create_job(
        job_id=job_id,
        tenant_id=tenant_id,
        job_name=request.job_name,
        scope=request.scope,
        device_ids=request.device_ids,
        start_date=request.start_date,
        end_date=request.end_date,
        granularity=request.granularity,
    )

    params = {
        "tenant_id": tenant_id,
        "scope": request.scope,
        "device_ids": request.device_ids,
        "start_date": request.start_date.isoformat(),
        "end_date": request.end_date.isoformat(),
        "granularity": request.granularity,
    }
    queue = get_waste_queue()
    try:
        await queue.enqueue(
            WasteJob(
                job_id=job_id,
                tenant_id=tenant_id,
                params_json=json.dumps(params, separators=(",", ":"), sort_keys=True),
            ),
        )
    except Exception:
        logger.exception("waste_enqueue_failed job_id=%s", job_id)
        await repo.update_job(
            job_id,
            status="enqueue_failed",
            error_code="ENQUEUE_FAILED",
            error_message="Job created but queue enqueue failed; will be retried on next startup",
        )
        raise HTTPException(
            status_code=503,
            detail={
                "error": "ENQUEUE_FAILED",
                "message": "Waste analysis job was created but could not be enqueued. It will be retried automatically.",
                "code": "ENQUEUE_FAILED",
                "job_id": job_id,
            },
        )

    created_job = await repo.get_job(job_id)
    if created_job is None:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "JOB_CREATE_FAILED",
                "message": "Waste analysis job was created but could not be reloaded.",
                "code": "JOB_CREATE_FAILED",
            },
        )

    return WasteAnalysisRunResponse(
        **_build_job_contract(created_job),
        estimated_completion_seconds=30,
    )


@router.get("/analysis/{job_id}/status", response_model=WasteStatusResponse)
async def get_status(job_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    ctx = get_request_context(request)
    ctx.require_tenant()
    repo = WasteRepository(db, ctx)
    job = await repo.get_job(job_id)
    if not job:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "JOB_NOT_FOUND",
                "message": "Waste analysis job not found.",
                "code": "JOB_NOT_FOUND",
            },
        )
    return WasteStatusResponse(
        **_build_job_contract(job),
    )


@router.get("/analysis/{job_id}/result")
async def get_result(job_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    ctx = get_request_context(request)
    ctx.require_tenant()
    repo = WasteRepository(db, ctx)
    job = await repo.get_job(job_id)
    if not job:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "JOB_NOT_FOUND",
                "message": "Waste analysis job not found.",
                "code": "JOB_NOT_FOUND",
            },
        )
    if not _result_ready(job):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "RESULT_NOT_READY",
                "message": "Waste analysis result is not ready yet.",
                "code": "RESULT_NOT_READY",
                "status": job.status.value if hasattr(job.status, "value") else str(job.status),
                "result_ready": False,
                "download_ready": _artifact_ready(job),
            },
        )
    return job.result_json


@router.get("/analysis/{job_id}/download", response_model=WasteDownloadResponse)
async def get_download(job_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    ctx = get_request_context(request)
    ctx.require_tenant()
    repo = WasteRepository(db, ctx)
    job = await repo.get_job(job_id)
    if not job:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "JOB_NOT_FOUND",
                "message": "Waste analysis job not found.",
                "code": "JOB_NOT_FOUND",
            },
        )
    if not _download_ready(job):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "DOWNLOAD_NOT_READY",
                "message": "Waste analysis download is not ready yet.",
                "code": "DOWNLOAD_NOT_READY",
                "status": job.status.value if hasattr(job.status, "value") else str(job.status),
                "result_ready": _result_ready(job),
                "download_ready": False,
            },
        )
    return WasteDownloadResponse(
        job_id=job.id,
        status=job.status.value if hasattr(job.status, "value") else str(job.status),
        download_url=build_waste_download_path(job.id),
        expires_in_seconds=900,
        result_ready=_result_ready(job),
        artifact_ready=_artifact_ready(job),
        download_ready=True,
    )


@router.get("/analysis/{job_id}/file")
async def download_file(job_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    ctx = get_request_context(request)
    ctx.require_tenant()
    repo = WasteRepository(db, ctx)
    job = await repo.get_job(job_id)
    if not job:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "JOB_NOT_FOUND",
                "message": "Waste analysis job not found.",
                "code": "JOB_NOT_FOUND",
            },
        )
    if not _download_ready(job):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "DOWNLOAD_NOT_READY",
                "message": "Waste analysis download is not ready yet.",
                "code": "DOWNLOAD_NOT_READY",
                "status": job.status.value if hasattr(job.status, "value") else str(job.status),
                "result_ready": _result_ready(job),
                "download_ready": False,
            },
        )
    try:
        if _artifact_ready(job):
            pdf_bytes = await minio_client.async_download_pdf(job.s3_key)
        else:
            from src.pdf.builder import async_generate_waste_pdf

            payload = dict(job.result_json or {})
            payload.setdefault("job_id", job.id)
            payload.setdefault(
                "scope",
                job.scope.value if hasattr(job.scope, "value") else str(job.scope),
            )
            payload.setdefault(
                "scope_label",
                "All Devices"
                if (job.scope.value if hasattr(job.scope, "value") else str(job.scope)) == "all"
                else f"Selected Devices ({len(getattr(job, 'device_ids', None) or [])})",
            )
            payload.setdefault("start_date", job.start_date.isoformat() if getattr(job, "start_date", None) else None)
            payload.setdefault("end_date", job.end_date.isoformat() if getattr(job, "end_date", None) else None)
            payload.setdefault(
                "granularity",
                job.granularity.value if hasattr(job.granularity, "value") else str(job.granularity),
            )
            payload.setdefault("device_summaries", [])
            payload.setdefault("warnings", [])
            payload.setdefault("insights", [])
            payload.setdefault("currency", "INR")
            pdf_bytes = await async_generate_waste_pdf(payload)
        return StreamingResponse(
            BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={
                "Content-Disposition": f"attachment; filename=waste_report_{job.id}.pdf",
                "Content-Length": str(len(pdf_bytes)),
            },
        )
    except StorageError:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "ARTIFACT_NOT_FOUND",
                "message": "Waste analysis artifact is no longer available.",
                "code": "ARTIFACT_NOT_FOUND",
            },
        )
    except Exception as exc:
        logger.exception("waste_download_generation_failed job_id=%s", job_id)
        raise HTTPException(
            status_code=503,
            detail={
                "error": "DOWNLOAD_GENERATION_FAILED",
                "message": "Waste analysis PDF could not be generated for download right now.",
                "code": "DOWNLOAD_GENERATION_FAILED",
                "detail": str(exc),
            },
        )


@router.get("/analysis/history", response_model=WasteHistoryResponse)
async def get_history(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    ctx = get_request_context(request)
    ctx.require_tenant()
    repo = WasteRepository(db, ctx)
    jobs = await repo.list_jobs(limit=limit, offset=offset)
    return WasteHistoryResponse(
        items=[_build_job_contract(j) for j in jobs]
    )
