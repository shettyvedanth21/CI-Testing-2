"""Analytics API endpoints."""

import asyncio
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from uuid import uuid4

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, inspect, select

from src.api.dependencies import get_job_queue, get_result_repository
from src.config.settings import get_settings
from src.infrastructure.database import async_session_maker
from src.infrastructure.mysql_repository import MySQLResultRepository
from src.models.database import WorkerHeartbeat, FailureEventLabel, AccuracyEvaluation, AnalyticsJob
from src.models.schemas import (
    AnalyticsJobResponse,
    AnalyticsPreflightDeviceStatus,
    AnalyticsPreflightRequest,
    AnalyticsPreflightResponse,
    AnalyticsRequest,
    AnalyticsResultsResponse,
    AnalyticsType,
    FleetProgressResponse,
    FleetAnalyticsRequest,
    JobStatus,
    JobStatusResponse,
    SupportedModelsResponse,
)
from src.rate_limit import limiter
from src.services.result_repository import ResultRepository
from src.utils.exceptions import JobNotFoundError
from src.workers.job_queue import QueueBackend
from services.shared.job_context import BoundJobPayload
from services.shared.telemetry_coverage import build_device_coverage_result

from src.services.analytics.accuracy_evaluator import AccuracyEvaluator
from src.services.device_scope import AnalyticsDeviceScopeService
from src.services.job_status_estimator import JobStatusEstimator
from src.services.scaling_policy import AnalyticsScalingPolicy
from src.utils.validators import validate_date_range
from services.shared.tenant_context import build_tenant_scoped_internal_headers, resolve_request_tenant_id

logger = structlog.get_logger()

router = APIRouter()
ANALYTICS_MAX_RANGE_DAYS = 30


def _enforce_analytics_date_range(start_time: datetime, end_time: datetime) -> None:
    try:
        validate_date_range(start_time, end_time, max_days=ANALYTICS_MAX_RANGE_DAYS)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "INVALID_DATE_RANGE",
                "message": f"Analytics supports up to {ANALYTICS_MAX_RANGE_DAYS} days per run.",
                "detail": str(exc),
            },
        ) from exc


async def _load_job_results_if_needed(session, job) -> dict | None:
    state = inspect(job, raiseerr=False)
    if state is None or "results" not in state.unloaded:
        results = getattr(job, "results", None)
        return results if isinstance(results, dict) else None

    row = await session.execute(
        select(AnalyticsJob.results).where(AnalyticsJob.job_id == str(job.job_id)).limit(1)
    )
    results = row.scalar_one_or_none()
    return results if isinstance(results, dict) else None


async def _analytics_has_viewable_result(session, job) -> bool:
    if str(getattr(job, "status", "")) != JobStatus.COMPLETED.value:
        return False
    results = await _load_job_results_if_needed(session, job)
    return bool(results)


def _analytics_download_contract(job_id: str, result_ready: bool) -> dict[str, object]:
    return {
        "result_ready": result_ready,
        "artifact_ready": False,
        "download_ready": False,
        "result_url": f"/api/v1/analytics/results/{job_id}",
        "download_url": None,
    }


def _analytics_result_unavailable_detail(job_id: str, job) -> dict[str, object]:
    status_value = str(getattr(job, "status", "") or "")
    failed = status_value == JobStatus.FAILED.value
    return {
        "error": "RESULT_UNAVAILABLE" if failed else "RESULT_NOT_READY",
        "message": (
            "Analytics result is unavailable because the job failed."
            if failed
            else "Analytics result is not ready yet."
        ),
        "job_id": job_id,
        "status": status_value,
        "result_ready": False,
        "artifact_ready": False,
        "download_ready": False,
        "error_code": getattr(job, "error_code", None),
        "error_message": getattr(job, "error_message", None),
    }


async def _build_status_response(job_id: str, job) -> JobStatusResponse:
    async with async_session_maker() as session:
        estimate = await JobStatusEstimator(session).estimate(job)
        fleet_progress = await _build_fleet_progress(session, job)
        result_ready = await _analytics_has_viewable_result(session, job)
        results = await _load_job_results_if_needed(session, job) or {}

    return _build_status_response_payload(
        job_id=job_id,
        job=job,
        estimate=estimate,
        fleet_progress=fleet_progress,
        result_ready=result_ready,
        results=results,
    )


def _build_status_response_payload(
    *,
    job_id: str,
    job,
    estimate,
    fleet_progress: FleetProgressResponse | None,
    result_ready: bool,
    results: dict | None,
) -> JobStatusResponse:
    readiness = _analytics_download_contract(job_id, result_ready)
    workflow_kind = "fleet" if fleet_progress is not None else "single"
    coverage_result = results.get("coverage_result") if isinstance(results, dict) else None

    return JobStatusResponse(
        job_id=job_id,
        status=JobStatus(job.status),
        workflow_kind=workflow_kind,
        progress=job.progress,
        message=job.message,
        error_message=job.error_message,
        error_code=job.error_code,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        queue_position=estimate.queue_position if estimate.queue_position is not None else job.queue_position,
        attempt=job.attempt,
        worker_lease_expires_at=job.worker_lease_expires_at,
        last_heartbeat_at=getattr(job, "last_heartbeat_at", None),
        estimated_wait_seconds=estimate.estimated_wait_seconds,
        estimated_completion_seconds=estimate.estimated_completion_seconds,
        estimate_quality=estimate.estimate_quality,
        activity_state=estimate.activity_state,
        eta_reliable=estimate.eta_reliable,
        heartbeat_age_seconds=estimate.heartbeat_age_seconds,
        phase=getattr(job, "phase", None),
        phase_label=getattr(job, "phase_label", None),
        phase_progress=getattr(job, "phase_progress", None),
        result_ready=bool(readiness["result_ready"]),
        artifact_ready=bool(readiness["artifact_ready"]),
        download_ready=bool(readiness["download_ready"]),
        result_url=readiness["result_url"],
        download_url=readiness["download_url"],
        fleet_progress=fleet_progress,
        coverage_result=coverage_result if isinstance(coverage_result, dict) else None,
    )


async def _load_job_results_map(session, job_ids: list[str]) -> dict[str, dict | None]:
    normalized_ids = [str(job_id) for job_id in job_ids if str(job_id).strip()]
    if not normalized_ids:
        return {}

    rows = await session.execute(
        select(AnalyticsJob.job_id, AnalyticsJob.results).where(AnalyticsJob.job_id.in_(normalized_ids))
    )
    return {
        str(job_id): results if isinstance(results, dict) else None
        for job_id, results in rows.all()
    }


async def _build_fleet_progress_map(
    session,
    jobs: list,
    results_by_job_id: dict[str, dict | None],
) -> dict[str, FleetProgressResponse]:
    parent_jobs = []
    for job in jobs:
        if str(getattr(job, "job_kind", "")) == "fleet_parent":
            parent_jobs.append(job)
            continue

        params = getattr(job, "parameters", None) or {}
        if str(getattr(job, "device_id", "")) == "ALL" or params.get("fleet_mode"):
            parent_jobs.append(job)

    if not parent_jobs:
        return {}

    parent_job_ids = [str(job.job_id) for job in parent_jobs]
    child_rows = await session.execute(
        select(
            AnalyticsJob.parent_job_id,
            AnalyticsJob.status,
            func.count().label("job_count"),
        )
        .where(AnalyticsJob.parent_job_id.in_(parent_job_ids))
        .group_by(AnalyticsJob.parent_job_id, AnalyticsJob.status)
    )

    counts_by_parent: dict[str, dict[str, int]] = defaultdict(dict)
    for parent_job_id, child_status, job_count in child_rows.all():
        if parent_job_id is None:
            continue
        counts_by_parent[str(parent_job_id)][str(child_status)] = int(job_count or 0)

    fleet_progress_by_job_id: dict[str, FleetProgressResponse] = {}
    for job in parent_jobs:
        job_id = str(job.job_id)
        results = results_by_job_id.get(job_id) or {}
        skipped_children = list(results.get("skipped_children") or [])
        params = getattr(job, "parameters", None) or {}
        selected_device_count = len(list(params.get("device_ids") or [])) or None
        counts = counts_by_parent.get(job_id, {})
        child_jobs_total = sum(counts.values()) or None
        completed_devices = int(counts.get(JobStatus.COMPLETED.value, 0))

        coverage_pct = None
        if selected_device_count and selected_device_count > 0:
            coverage_pct = round((completed_devices / selected_device_count) * 100, 1)

        fleet_progress_by_job_id[job_id] = FleetProgressResponse(
            selected_device_count=selected_device_count,
            child_jobs_total=child_jobs_total,
            queued_devices=int(counts.get(JobStatus.PENDING.value, 0)),
            running_devices=int(counts.get(JobStatus.RUNNING.value, 0)),
            completed_devices=completed_devices,
            failed_devices=int(counts.get(JobStatus.FAILED.value, 0)),
            skipped_devices=len(skipped_children),
            coverage_pct=coverage_pct,
        )

    return fleet_progress_by_job_id


async def _build_list_status_responses(jobs: list) -> list[JobStatusResponse]:
    if not jobs:
        return []

    async with async_session_maker() as session:
        estimator = JobStatusEstimator(session)
        estimates_by_job_id = await estimator.estimate_many(jobs)

        jobs_requiring_results = [
            str(job.job_id)
            for job in jobs
            if str(getattr(job, "status", "")) == JobStatus.COMPLETED.value
            or str(getattr(job, "job_kind", "")) == "fleet_parent"
            or str(getattr(job, "device_id", "")) == "ALL"
            or bool((getattr(job, "parameters", None) or {}).get("fleet_mode"))
        ]
        results_by_job_id = await _load_job_results_map(session, jobs_requiring_results)
        fleet_progress_by_job_id = await _build_fleet_progress_map(session, jobs, results_by_job_id)

    responses: list[JobStatusResponse] = []
    for job in jobs:
        job_id = str(job.job_id)
        results = results_by_job_id.get(job_id) or {}
        result_ready = str(getattr(job, "status", "")) == JobStatus.COMPLETED.value and bool(results)
        responses.append(
            _build_status_response_payload(
                job_id=job_id,
                job=job,
                estimate=estimates_by_job_id.get(job_id),
                fleet_progress=fleet_progress_by_job_id.get(job_id),
                result_ready=result_ready,
                results=results,
            )
        )

    return responses


async def _build_fleet_progress(session, job) -> FleetProgressResponse | None:
    if str(getattr(job, "job_kind", "")) != "fleet_parent":
        params = getattr(job, "parameters", None) or {}
        if str(getattr(job, "device_id", "")) != "ALL" and not params.get("fleet_mode"):
            return None

    repo = MySQLResultRepository(session)
    child_rows = await repo.list_jobs_for_parent(str(job.job_id))
    results = await _load_job_results_if_needed(session, job) or {}
    skipped_children = list(results.get("skipped_children") or [])
    selected_device_count = len(list((getattr(job, "parameters", None) or {}).get("device_ids") or [])) or None
    child_jobs_total = len(child_rows) if child_rows else None
    queued_devices = sum(1 for child in child_rows if child.status == JobStatus.PENDING.value)
    running_devices = sum(1 for child in child_rows if child.status == JobStatus.RUNNING.value)
    completed_devices = sum(1 for child in child_rows if child.status == JobStatus.COMPLETED.value)
    failed_devices = sum(1 for child in child_rows if child.status == JobStatus.FAILED.value)
    skipped_devices = len(skipped_children)

    coverage_pct = None
    if selected_device_count and selected_device_count > 0:
        coverage_pct = round((completed_devices / selected_device_count) * 100, 1)

    return FleetProgressResponse(
        selected_device_count=selected_device_count,
        child_jobs_total=child_jobs_total,
        queued_devices=queued_devices or 0,
        running_devices=running_devices or 0,
        completed_devices=completed_devices or 0,
        failed_devices=failed_devices or 0,
        skipped_devices=skipped_devices or 0,
        coverage_pct=coverage_pct,
    )


def get_tenant_id(request: Request) -> str | None:
    return resolve_request_tenant_id(request)


def _request_context(request: Request):
    return getattr(request.state, "tenant_context", None)


async def _check_device_telemetry_availability(
    device_id: str,
    start_time: datetime,
    end_time: datetime,
    tenant_id: str,
) -> AnalyticsPreflightDeviceStatus:
    settings = get_settings()
    url = f"{settings.data_service_url}/api/v1/data/telemetry/{device_id}/earliest"
    headers = build_tenant_scoped_internal_headers("analytics-service", tenant_id)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                url,
                params={"start_time": start_time.isoformat()},
                headers=headers,
            )
            response.raise_for_status()
            payload = response.json()
    except Exception:
        return AnalyticsPreflightDeviceStatus(
            device_id=device_id,
            has_telemetry_in_range=False,
            reason="availability_check_failed",
            message="Telemetry availability could not be verified before submission.",
        )

    item = None
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict):
            item = data.get("item")

    timestamp_raw = item.get("timestamp") if isinstance(item, dict) else None
    if not timestamp_raw:
        return AnalyticsPreflightDeviceStatus(
            device_id=device_id,
            has_telemetry_in_range=False,
            reason="no_telemetry_in_range",
            message="No telemetry found in the selected time range.",
        )

    try:
        earliest_timestamp = datetime.fromisoformat(str(timestamp_raw).replace("Z", "+00:00"))
    except Exception:
        return AnalyticsPreflightDeviceStatus(
            device_id=device_id,
            has_telemetry_in_range=False,
            reason="availability_check_failed",
            message="Telemetry availability could not be verified before submission.",
        )

    if earliest_timestamp > end_time:
        return AnalyticsPreflightDeviceStatus(
            device_id=device_id,
            has_telemetry_in_range=False,
            reason="no_telemetry_in_range",
            message="No telemetry found in the selected time range.",
        )

    return AnalyticsPreflightDeviceStatus(
        device_id=device_id,
        has_telemetry_in_range=True,
        reason="telemetry_available",
        message="Telemetry is available in the selected time range.",
    )


async def _build_preflight_response(
    device_ids: list[str],
    start_time: datetime,
    end_time: datetime,
    tenant_id: str,
) -> AnalyticsPreflightResponse:
    semaphore = asyncio.Semaphore(8)

    async def _bounded_check(device_id: str) -> AnalyticsPreflightDeviceStatus:
        async with semaphore:
            return await _check_device_telemetry_availability(
                device_id=device_id,
                start_time=start_time,
                end_time=end_time,
                tenant_id=tenant_id,
            )

    devices = await asyncio.gather(*[_bounded_check(device_id) for device_id in device_ids])
    with_telemetry = sum(1 for item in devices if item.reason == "telemetry_available")
    without_telemetry = sum(1 for item in devices if item.reason == "no_telemetry_in_range")
    unverified = sum(1 for item in devices if item.reason == "availability_check_failed")
    guaranteed_no_data = with_telemetry == 0 and without_telemetry > 0 and unverified == 0

    if guaranteed_no_data:
        message = "No telemetry is available in the selected time range."
    elif without_telemetry > 0:
        message = f"{without_telemetry} selected device(s) do not have telemetry in the selected time range."
    elif unverified > 0:
        message = "Telemetry availability could only be partially verified before submission."
    else:
        message = "Telemetry is available for the selected time range."

    coverage_result = build_device_coverage_result(
        selected_device_ids=[str(device_id) for device_id in device_ids],
        usable_device_ids=[str(item.device_id) for item in devices if item.reason == "telemetry_available"],
        skipped_devices=[
            {
                "device_id": str(item.device_id),
                "reason": str(item.reason),
                "message": str(item.message),
            }
            for item in devices
            if item.reason != "telemetry_available"
        ],
        warnings=[message] if guaranteed_no_data or without_telemetry > 0 or unverified > 0 else [],
        artifact_generation_allowed=with_telemetry > 0,
    ).to_dict()

    return AnalyticsPreflightResponse(
        devices=devices,
        checked_device_count=len(devices),
        devices_with_telemetry=with_telemetry,
        devices_without_telemetry=without_telemetry,
        devices_unverified=unverified,
        guaranteed_no_data=guaranteed_no_data,
        message=message,
        coverage_result=coverage_result,
    )


async def _resolve_accessible_job_device_ids(request: Request) -> list[str] | None:
    ctx = _request_context(request)
    if ctx is None or ctx.role not in {"plant_manager", "operator", "viewer"}:
        return None
    scope_service = AnalyticsDeviceScopeService(ctx)
    return await scope_service.resolve_accessible_device_ids()


def _build_job_payload(
    *,
    job_type: str,
    tenant_id: str | None,
    device_id: str | None,
    initiated_by_user_id: str,
    initiated_by_role: str,
    payload: dict,
) -> str:
    bound = BoundJobPayload(
        job_type=job_type,
        tenant_id=tenant_id,
        device_id=device_id,
        initiated_by_user_id=initiated_by_user_id,
        initiated_by_role=initiated_by_role,
        payload=payload,
    )
    bound.validate()
    return json.dumps(bound.__dict__, separators=(",", ":"), sort_keys=True, default=str)


async def check_worker_alive() -> bool:
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=120)
    async with async_session_maker() as session:
        result = await session.execute(
            select(func.count())
            .select_from(WorkerHeartbeat)
            .where(WorkerHeartbeat.last_heartbeat_at > cutoff)
        )
        count = result.scalar() or 0
    return count > 0


def _record_admission_rejection(app_request: Request, category: str) -> None:
    counters = getattr(app_request.app.state, "analytics_rejections", None)
    if isinstance(counters, dict):
        counters[category] = int(counters.get(category, 0) or 0) + 1

    prom_counters = getattr(app_request.app.state, "prom_counters", None)
    if isinstance(prom_counters, dict):
        counter = prom_counters.get(category)
        if counter is not None:
            counter.inc()


async def _enforce_admission_policy(
    *,
    app_request: Request,
    result_repository: ResultRepository,
    tenant_id: str | None,
    requested_jobs: int = 1,
) -> int:
    settings = get_settings()
    decision = await AnalyticsScalingPolicy(settings, result_repository).evaluate_submission(
        tenant_id=tenant_id,
        requested_jobs=requested_jobs,
    )
    if decision.allowed:
        return decision.queue_position

    category = "tenant_cap" if decision.status_code == status.HTTP_429_TOO_MANY_REQUESTS else "overloaded"
    _record_admission_rejection(app_request, category)
    raise HTTPException(
        status_code=decision.status_code,
        detail={
            "error": decision.error_code,
            "message": decision.message,
            **(decision.details or {}),
        },
    )


@router.post(
    "/run",
    response_model=AnalyticsJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
@limiter.limit(get_settings().analytics_run_rate_limit)
async def run_analytics(
    request: Request,
    body: AnalyticsRequest,
    job_queue: QueueBackend = Depends(get_job_queue),
    result_repository: ResultRepository = Depends(get_result_repository),
) -> AnalyticsJobResponse:
    """
    Submit a new analytics job.

    The job will be queued and processed asynchronously.
    Use the returned job_id to check status and retrieve results.
    """
    if not await check_worker_alive():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "WORKER_UNAVAILABLE",
                "message": "Analytics worker is starting up or unavailable. Please wait 30 seconds and try again.",
            },
        )

    tenant_id = get_tenant_id(request)
    ctx = _request_context(request)
    if ctx is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "MISSING_AUTH_CONTEXT", "message": "Authentication context missing"},
        )
    normalized_device_ids = await AnalyticsDeviceScopeService(ctx).normalize_requested_device_ids([body.device_id])
    body.device_id = normalized_device_ids[0]
    start_time = body.start_time or datetime.now(timezone.utc)
    end_time = body.end_time or start_time
    _enforce_analytics_date_range(start_time, end_time)
    duplicate_job = await result_repository.find_active_duplicate(
        tenant_id=tenant_id,
        device_id=body.device_id,
        analysis_type=body.analysis_type.value,
        model_name=body.model_name,
    )
    if duplicate_job is not None:
        logger.info(
            "analytics_job_reused_duplicate",
            job_id=duplicate_job.job_id,
            analysis_type=body.analysis_type.value,
            model_name=body.model_name,
            device_id=body.device_id,
        )
        return AnalyticsJobResponse(
            job_id=duplicate_job.job_id,
            status=JobStatus(duplicate_job.status),
            message="Matching analytics job already queued",
        )
    queue_position = await _enforce_admission_policy(
        app_request=request,
        result_repository=result_repository,
        tenant_id=tenant_id,
        requested_jobs=1,
    )
    job_id = str(uuid4())

    logger.info(
        "analytics_job_submitted",
        job_id=job_id,
        analysis_type=body.analysis_type.value,
        model_name=body.model_name,
        device_id=body.device_id,
    )

    parameters = dict(body.parameters or {})
    if tenant_id is not None:
        parameters["tenant_id"] = tenant_id
    await result_repository.create_job(
        job_id=job_id,
        device_id=body.device_id,
        analysis_type=body.analysis_type.value,
        model_name=body.model_name,
        date_range_start=start_time,
        date_range_end=end_time,
        parameters=parameters,
        job_kind="single",
    )
    await result_repository.update_job_queue_metadata(
        job_id=job_id,
        attempt=1,
        queue_enqueued_at=datetime.now(timezone.utc),
        queue_dispatched_at=datetime.now(timezone.utc),
        queue_position=max(0, int(queue_position)),
    )

    raw_payload = _build_job_payload(
        job_type="analytics",
        tenant_id=tenant_id,
        device_id=body.device_id,
        initiated_by_user_id=ctx.user_id,
        initiated_by_role=ctx.role,
        payload=body.model_dump(mode="json"),
    )
    await job_queue.submit_job(job_id=job_id, raw_payload=raw_payload, attempt=1)
    if not hasattr(request.app.state, "pending_jobs"):
        request.app.state.pending_jobs = {}
    request.app.state.pending_jobs[job_id] = {
        "created_at": datetime.now(timezone.utc),
        "message": "Job queued successfully",
    }

    return AnalyticsJobResponse(
        job_id=job_id,
        status=JobStatus.PENDING,
        message="Job queued successfully",
    )


@router.post(
    "/preflight",
    response_model=AnalyticsPreflightResponse,
)
@limiter.limit(get_settings().analytics_preflight_rate_limit)
async def preflight_analytics(
    request: Request,
    body: AnalyticsPreflightRequest,
) -> AnalyticsPreflightResponse:
    tenant_id = get_tenant_id(request)
    ctx = _request_context(request)
    if ctx is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "MISSING_AUTH_CONTEXT", "message": "Authentication context missing"},
        )
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "TENANT_SCOPE_REQUIRED", "message": "Tenant scope is required for analytics preflight."},
        )
    _enforce_analytics_date_range(body.start_time, body.end_time)

    normalized_device_ids = await AnalyticsDeviceScopeService(ctx).normalize_requested_device_ids(list(body.device_ids))
    if not normalized_device_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "NO_ACCESSIBLE_DEVICES",
                "message": "No accessible devices are available for analytics.",
            },
        )

    return await _build_preflight_response(
        normalized_device_ids,
        start_time=body.start_time,
        end_time=body.end_time,
        tenant_id=tenant_id,
    )


def _default_model_for(analysis_type: str) -> str:
    if analysis_type == AnalyticsType.ANOMALY.value:
        return "anomaly_ensemble"
    return "failure_ensemble"


@router.post(
    "/run-fleet",
    response_model=AnalyticsJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
@limiter.limit(get_settings().analytics_fleet_run_rate_limit)
async def run_fleet_analytics(
    request: Request,
    body: FleetAnalyticsRequest,
    job_queue: QueueBackend = Depends(get_job_queue),
    result_repo: ResultRepository = Depends(get_result_repository),
) -> AnalyticsJobResponse:
    """
    Submit strict fleet analytics as a parent job.
    Parent status fails if any child device fails.
    """
    if not await check_worker_alive():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "WORKER_UNAVAILABLE",
                "message": "Analytics worker is starting up or unavailable. Please wait 30 seconds and try again.",
            },
        )

    tenant_id = get_tenant_id(request)
    ctx = _request_context(request)
    if ctx is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "MISSING_AUTH_CONTEXT", "message": "Authentication context missing"},
        )

    scope_service = AnalyticsDeviceScopeService(ctx)
    normalized_device_ids = await scope_service.normalize_requested_device_ids(list(body.device_ids or []))
    if not normalized_device_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "NO_ACCESSIBLE_DEVICES",
                "message": "No accessible devices are available for fleet analysis.",
            },
        )

    body.device_ids = normalized_device_ids
    _enforce_analytics_date_range(body.start_time, body.end_time)
    await _enforce_admission_policy(
        app_request=request,
        result_repository=result_repo,
        tenant_id=tenant_id,
        requested_jobs=1,
    )
    parent_job_id = str(uuid4())
    body.parameters = {**(body.parameters or {}), **({"tenant_id": tenant_id} if tenant_id else {})}

    await result_repo.create_job(
        job_id=parent_job_id,
        device_id="ALL",
        analysis_type=body.analysis_type,
        model_name=body.model_name or _default_model_for(body.analysis_type),
        date_range_start=body.start_time,
        date_range_end=body.end_time,
        parameters={
            "fleet_mode": "best_effort_exact",
            "device_ids": normalized_device_ids,
            **(body.parameters or {}),
        },
        job_kind="fleet_parent",
    )
    await result_repo.update_job_status(
        job_id=parent_job_id,
        status=JobStatus.PENDING,
        progress=0.0,
        message="Fleet job queued",
        phase="queued",
        phase_label="Queued",
        phase_progress=0.0,
    )
    await result_repo.update_job_queue_metadata(
        job_id=parent_job_id,
        attempt=1,
        queue_enqueued_at=datetime.now(timezone.utc),
        queue_dispatched_at=datetime.now(timezone.utc),
    )
    raw_payload = _build_job_payload(
        job_type="fleet_parent_analytics",
        tenant_id=tenant_id,
        device_id="ALL",
        initiated_by_user_id=ctx.user_id,
        initiated_by_role=ctx.role,
        payload=body.model_dump(mode="json"),
    )
    await job_queue.submit_job(job_id=parent_job_id, raw_payload=raw_payload, attempt=1)

    return AnalyticsJobResponse(
        job_id=parent_job_id,
        status=JobStatus.PENDING,
        message="Fleet job queued",
    )


@router.get(
    "/status/{job_id}",
    response_model=JobStatusResponse,
)
async def get_job_status(
    job_id: str,
    app_request: Request,
    result_repo: ResultRepository = Depends(get_result_repository),
) -> JobStatusResponse:
    """Get the current status of an analytics job."""
    try:
        tenant_id = get_tenant_id(app_request)
        accessible_device_ids = await _resolve_accessible_job_device_ids(app_request)
        job = await result_repo.get_job_scoped(
            job_id,
            tenant_id=tenant_id,
            accessible_device_ids=accessible_device_ids,
        )
        return await _build_status_response(job_id, job)
    except JobNotFoundError:
        pending_jobs = getattr(app_request.app.state, "pending_jobs", {})
        pending = pending_jobs.get(job_id)
        if pending:
            readiness = _analytics_download_contract(job_id, None)
            return JobStatusResponse(
                job_id=job_id,
                status=JobStatus.PENDING,
                progress=0,
                message=pending.get("message") or "Job queued successfully",
                created_at=pending.get("created_at"),
                phase="queued",
                phase_label="Queued",
                phase_progress=0.0,
                estimate_quality="low",
                result_ready=bool(readiness["result_ready"]),
                artifact_ready=bool(readiness["artifact_ready"]),
                download_ready=bool(readiness["download_ready"]),
                result_url=readiness["result_url"],
                download_url=readiness["download_url"],
            )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found",
        )


@router.get(
    "/results/{job_id}",
    response_model=AnalyticsResultsResponse,
)
async def get_analytics_results(
    job_id: str,
    request: Request,
    result_repo: ResultRepository = Depends(get_result_repository),
) -> AnalyticsResultsResponse:
    """
    Retrieve results of a completed analytics job.

    Returns model outputs, accuracy metrics, and execution details.
    """
    try:
        tenant_id = get_tenant_id(request)
        accessible_device_ids = await _resolve_accessible_job_device_ids(request)
        job = await result_repo.get_job_scoped(
            job_id,
            tenant_id=tenant_id,
            accessible_device_ids=accessible_device_ids,
        )

        if job.status != JobStatus.COMPLETED.value:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=_analytics_result_unavailable_detail(job_id, job),
            )

        return AnalyticsResultsResponse(
            job_id=job_id,
            status=JobStatus(job.status),
            device_id=job.device_id,
            analysis_type=AnalyticsType(job.analysis_type),
            model_name=job.model_name,
            date_range_start=job.date_range_start,
            date_range_end=job.date_range_end,
            results=job.results,
            coverage_result=(job.results or {}).get("coverage_result") if isinstance(job.results, dict) else None,
            accuracy_metrics=job.accuracy_metrics,
            execution_time_seconds=job.execution_time_seconds,
            completed_at=job.completed_at,
        )
    except JobNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found",
        )


# ------------------------------------------------------------------
# ✅ PERMANENT FIX – advertise only runnable models
# ------------------------------------------------------------------
@router.get(
    "/models",
    response_model=SupportedModelsResponse,
)
async def get_supported_models() -> SupportedModelsResponse:
    """Get list of supported analytics models by type."""

    forecasting_models = ["prophet", "arima"]

    return SupportedModelsResponse(
        anomaly_detection=[
            "isolation_forest",
            "lstm_autoencoder",
            "cusum",
        ],
        failure_prediction=[
            "xgboost",
            "lstm_classifier",
            "degradation_tracker",
        ],
        forecasting=forecasting_models,
        ensembles=[
            {
                "id": "anomaly_ensemble",
                "display_name": "Anomaly Detection — 3 Model Ensemble",
                "models": [
                    {"name": "isolation_forest", "trains": True},
                    {
                        "name": "lstm_autoencoder",
                        "trains": True,
                        "min_data": "50 sequences (~80 min)",
                    },
                    {
                        "name": "cusum",
                        "trains": False,
                        "note": "Works from minute 1",
                    },
                ],
                "voting_rule": "Alert when 2 of 3 models flag",
            },
            {
                "id": "failure_ensemble",
                "display_name": "Failure Prediction — 3 Model Ensemble",
                "models": [
                    {"name": "xgboost", "trains": True},
                    {
                        "name": "lstm_classifier",
                        "trains": True,
                        "min_data": "50 sequences (~80 min)",
                    },
                    {
                        "name": "degradation_tracker",
                        "trains": False,
                        "note": "Physics-based — no training needed",
                    },
                ],
                "voting_rule": "CRITICAL=3/3, WARNING=2/3, WATCH=1/3",
            },
        ],
    )


@router.get(
    "/jobs",
    response_model=List[JobStatusResponse],
)
async def list_jobs(
    request: Request,
    status: Optional[JobStatus] = None,
    device_id: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
    result_repo: ResultRepository = Depends(get_result_repository),
) -> List[JobStatusResponse]:
    """List analytics jobs with optional filtering."""
    tenant_id = get_tenant_id(request)
    accessible_device_ids = await _resolve_accessible_job_device_ids(request)
    jobs = await result_repo.list_jobs(
        status=status.value if status else None,
        device_id=device_id,
        tenant_id=tenant_id,
        accessible_device_ids=accessible_device_ids,
        limit=limit,
        offset=offset,
    )

    return await _build_list_status_responses(jobs)


@router.get("/ops/queue")
async def get_queue_ops_snapshot(
    app_request: Request,
    result_repo: ResultRepository = Depends(get_result_repository),
) -> Dict[str, object]:
    """Operational queue snapshot for SRE dashboards."""
    settings = get_settings()
    pending_count = await result_repo.count_jobs(statuses=[JobStatus.PENDING.value])
    running_count = await result_repo.count_jobs(statuses=[JobStatus.RUNNING.value])
    failed_count = await result_repo.count_jobs(statuses=[JobStatus.FAILED.value])
    retry_count = await result_repo.count_jobs(attempts_gte=2)
    top_tenants = await result_repo.list_tenant_job_counts(
        statuses=[JobStatus.RUNNING.value],
        job_kinds=["single", "fleet_child"],
        limit=settings.ops_top_tenants_limit,
    )
    active_workers = 0
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=max(10, settings.worker_heartbeat_ttl_seconds))
    async with async_session_maker() as session:
        rows = await session.execute(select(WorkerHeartbeat).where(WorkerHeartbeat.last_heartbeat_at >= cutoff))
        active_workers = len(list(rows.scalars().all()))
    job_queue = getattr(app_request.app.state, "job_queue", None)
    queue_metrics_fetcher = getattr(job_queue, "metrics", None)
    queue_metrics = await queue_metrics_fetcher() if callable(queue_metrics_fetcher) else {}
    rejection_counters = getattr(app_request.app.state, "analytics_rejections", {}) or {}

    return {
        "queue_depth": pending_count,
        "consumer_lag_estimate": pending_count,
        "failed_job_count": failed_count,
        "active_workers": active_workers,
        "running_jobs": running_count,
        "retry_count": retry_count,
        "dead_letter_jobs": int(queue_metrics.get("dead_letter_messages", 0)),
        "claimed_messages": int(queue_metrics.get("claimed_messages", 0)),
        "stream_depth": int(queue_metrics.get("queued_messages", 0)),
        "top_tenants_by_active_jobs": top_tenants,
        "rejected_submissions": {
            "tenant_cap": int(rejection_counters.get("tenant_cap", 0) or 0),
            "overloaded": int(rejection_counters.get("overloaded", 0) or 0),
        },
        "capacity_policy": {
            "max_concurrent_jobs_per_worker": settings.max_concurrent_jobs,
            "global_active_job_limit": settings.global_active_job_limit,
            "queue_backlog_reject_threshold": settings.queue_backlog_reject_threshold,
            "tenant_max_queued_jobs": settings.tenant_max_queued_jobs,
            "tenant_max_active_jobs": settings.tenant_max_active_jobs,
            "fleet_parent_max_active_children": settings.fleet_parent_max_active_children,
            "queue_max_attempts": settings.queue_max_attempts,
            "stale_scan_interval_seconds": settings.stale_scan_interval_seconds,
        },
        "queue_backend": getattr(app_request.app.state, "queue_backend", "unknown"),
    }


@router.post("/labels/failure-events")
async def ingest_failure_event_label(payload: Dict[str, object]) -> Dict[str, object]:
    """Add a maintenance/failure ground-truth label event."""
    device_id = str(payload.get("device_id") or "").strip()
    event_time_raw = payload.get("event_time")
    if not device_id or not event_time_raw:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="device_id and event_time are required",
        )
    try:
        event_time = datetime.fromisoformat(str(event_time_raw).replace("Z", "+00:00"))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"invalid event_time: {exc}",
        )

    row = FailureEventLabel(
        device_id=device_id,
        event_time=event_time,
        event_type=str(payload.get("event_type") or "failure"),
        severity=str(payload.get("severity") or "") or None,
        source=str(payload.get("source") or "") or "manual",
        metadata_json=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
    )
    async with async_session_maker() as session:
        session.add(row)
        await session.commit()

    return {"status": "accepted", "id": row.id}


@router.post("/accuracy/evaluate")
@limiter.limit(get_settings().analytics_accuracy_rate_limit)
async def evaluate_accuracy(
    request: Request,
    device_id: Optional[str] = Query(default=None),
    lookback_days: int = Query(default=90, ge=1, le=3650),
    lead_window_hours: int = Query(default=24, ge=1, le=720),
) -> Dict[str, object]:
    """Run backtest evaluation against labeled events and persist summary."""
    async with async_session_maker() as session:
        result = await AccuracyEvaluator.evaluate_failure_predictions(
            session=session,
            device_id=device_id,
            lookback_days=lookback_days,
            lead_window_hours=lead_window_hours,
        )
    return {
        "analysis_type": "prediction",
        "scope_device_id": device_id,
        **result.as_dict(),
    }


@router.get("/accuracy/latest")
async def get_latest_accuracy(device_id: Optional[str] = Query(default=None)) -> Dict[str, object]:
    """Fetch latest persisted accuracy evaluation record."""
    async with async_session_maker() as session:
        q = (
            select(AccuracyEvaluation)
            .where(AccuracyEvaluation.analysis_type == "prediction")
            .order_by(AccuracyEvaluation.created_at.desc())
            .limit(1)
        )
        if device_id:
            q = q.where(AccuracyEvaluation.scope_device_id == device_id)
        row = (await session.execute(q)).scalar_one_or_none()

    if not row:
        return {"analysis_type": "prediction", "scope_device_id": device_id, "status": "no_evaluation"}

    return {
        "analysis_type": row.analysis_type,
        "scope_device_id": row.scope_device_id,
        "sample_size": row.sample_size,
        "labeled_events": row.labeled_events,
        "precision": row.precision,
        "recall": row.recall,
        "f1_score": row.f1_score,
        "false_alert_rate": row.false_alert_rate,
        "avg_lead_hours": row.avg_lead_hours,
        "is_certified": bool(row.is_certified),
        "notes": row.notes,
        "created_at": row.created_at,
    }


# ------------------------------------------------------------------
# ✅ STEP-1 – Dataset listing endpoint
# ------------------------------------------------------------------

@router.get("/datasets")
async def list_datasets(
    device_id: str = Query(..., description="Device ID"),
):
    """
    List available exported datasets for a device.

    This reads directly from S3/MinIO and returns available dataset objects.
    """

    s3_client = S3Client()
    dataset_service = DatasetService(s3_client)

    datasets = await dataset_service.list_available_datasets(
        device_id=device_id
    )

    return {
        "device_id": device_id,
        "datasets": datasets,
    }


@router.get("/retrain-status")
async def get_retrain_status(request: Request) -> dict:
    """Returns the last auto-retrain status per device."""
    retrainer = getattr(request.app.state, "retrainer", None)
    if not retrainer:
        return {}
    return retrainer.get_status()


@router.get("/formatted-results/{job_id}")
async def get_formatted_results(
    job_id: str,
    request: Request,
    result_repo: ResultRepository = Depends(get_result_repository),
) -> dict:
    """
    Returns dashboard-ready structured results for a completed job.
    """
    try:
        tenant_id = get_tenant_id(request)
        accessible_device_ids = await _resolve_accessible_job_device_ids(request)
        getter = getattr(result_repo, "get_job_scoped", None) or result_repo.get_job
        job = await getter(
            job_id,
            tenant_id=tenant_id,
            accessible_device_ids=accessible_device_ids,
        )
        if job.status != JobStatus.COMPLETED.value:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=_analytics_result_unavailable_detail(job_id, job),
            )
        formatted = (job.results or {}).get("formatted")
        if not formatted:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Formatted results not available for this job",
            )
        return formatted
    except JobNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found",
        )
