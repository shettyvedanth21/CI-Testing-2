from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from starlette.requests import Request
from unittest.mock import AsyncMock
from fastapi import HTTPException

from tests._bootstrap import bootstrap_test_imports

bootstrap_test_imports()

from services.shared.tenant_context import TenantContext
from src.api.routes import analytics
from src.models.schemas import AnalyticsType, JobStatus
from src.utils.exceptions import JobNotFoundError


def _request(*, tenant_id: str = "SH00000001", role: str = "org_admin") -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/analytics/results/job-1",
        "headers": [],
        "query_string": b"",
    }
    request = Request(scope)
    request.state.tenant_context = TenantContext(
        tenant_id=tenant_id,
        user_id="user-1",
        role=role,
        plant_ids=["PLANT-1"],
        is_super_admin=False,
    )
    request.state.feature_entitlements = None
    return request


def _request_with_app(*, tenant_id: str = "SH00000001", role: str = "org_admin", pending_jobs=None) -> Request:
    request = _request(tenant_id=tenant_id, role=role)
    request.scope["app"] = SimpleNamespace(state=SimpleNamespace(pending_jobs=pending_jobs or {}))
    return request


@pytest.mark.asyncio
async def test_failed_analytics_result_returns_truthful_contract(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(analytics, "_resolve_accessible_job_device_ids", AsyncMock(return_value=["DEV-1"]))

    job = SimpleNamespace(
        status=JobStatus.FAILED.value,
        error_code="MODEL_EXECUTION_FAILED",
        error_message="Model pipeline crashed",
    )
    repo = SimpleNamespace(get_job_scoped=AsyncMock(return_value=job))

    with pytest.raises(HTTPException) as excinfo:
        await analytics.get_analytics_results("job-failed", _request(), result_repo=repo)

    exc = excinfo.value
    assert exc.status_code == 409
    assert exc.detail["error"] == "RESULT_UNAVAILABLE"
    assert exc.detail["status"] == "failed"
    assert exc.detail["result_ready"] is False
    assert exc.detail["error_code"] == "MODEL_EXECUTION_FAILED"
    assert exc.detail["error_message"] == "Model pipeline crashed"


@pytest.mark.asyncio
async def test_completed_no_data_analytics_result_remains_viewable(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(analytics, "_resolve_accessible_job_device_ids", AsyncMock(return_value=["DEV-1"]))

    coverage_result = {
        "level": "no_coverage",
        "coverage_pct": 0.0,
        "usable_for_business_decisions": False,
        "artifact_generation_allowed": False,
        "terminal_status": "business_blocked",
        "message": "No telemetry is available in the selected time range.",
    }
    completed_at = datetime.now(timezone.utc)
    job = SimpleNamespace(
        status=JobStatus.COMPLETED.value,
        device_id="DEV-1",
        analysis_type=AnalyticsType.ANOMALY.value,
        model_name="anomaly_ensemble",
        date_range_start=completed_at,
        date_range_end=completed_at,
        results={"coverage_result": coverage_result, "formatted": {"status": "no_data"}},
        accuracy_metrics=None,
        execution_time_seconds=2,
        completed_at=completed_at,
    )
    repo = SimpleNamespace(get_job_scoped=AsyncMock(return_value=job))

    response = await analytics.get_analytics_results("job-no-data", _request(), result_repo=repo)

    assert response.status == JobStatus.COMPLETED
    assert response.coverage_result == coverage_result
    assert response.results["formatted"]["status"] == "no_data"


@pytest.mark.asyncio
async def test_completed_insufficient_coverage_analytics_result_remains_viewable(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(analytics, "_resolve_accessible_job_device_ids", AsyncMock(return_value=["DEV-1"]))

    coverage_result = {
        "level": "insufficient_coverage",
        "coverage_pct": 12.5,
        "usable_for_business_decisions": False,
        "artifact_generation_allowed": False,
        "terminal_status": "business_blocked",
        "message": "Telemetry coverage is insufficient for this analysis.",
    }
    completed_at = datetime.now(timezone.utc)
    job = SimpleNamespace(
        status=JobStatus.COMPLETED.value,
        device_id="DEV-1",
        analysis_type=AnalyticsType.ANOMALY.value,
        model_name="anomaly_ensemble",
        date_range_start=completed_at,
        date_range_end=completed_at,
        results={
            "coverage_result": coverage_result,
            "formatted": {
                "analysis_type": "anomaly_detection",
                "status": "insufficient_coverage",
                "summary": coverage_result["message"],
            },
        },
        accuracy_metrics=None,
        execution_time_seconds=2,
        completed_at=completed_at,
    )
    repo = SimpleNamespace(get_job_scoped=AsyncMock(return_value=job))

    response = await analytics.get_analytics_results("job-insufficient", _request(), result_repo=repo)

    assert response.status == JobStatus.COMPLETED
    assert response.coverage_result == coverage_result
    assert response.results["formatted"]["analysis_type"] == "anomaly_detection"
    assert response.results["formatted"]["status"] == "insufficient_coverage"


@pytest.mark.asyncio
async def test_formatted_results_route_enforces_accessible_device_scope(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, object] = {}

    async def fake_get_job_scoped(job_id: str, tenant_id=None, accessible_device_ids=None):
        captured["job_id"] = job_id
        captured["tenant_id"] = tenant_id
        captured["accessible_device_ids"] = accessible_device_ids
        raise JobNotFoundError("hidden")

    monkeypatch.setattr(analytics, "_resolve_accessible_job_device_ids", AsyncMock(return_value=["DEV-ALLOWED"]))
    repo = SimpleNamespace(get_job_scoped=fake_get_job_scoped)

    with pytest.raises(HTTPException) as excinfo:
        await analytics.get_formatted_results(
            "job-hidden",
            _request(role="plant_manager"),
            result_repo=repo,
        )

    exc = excinfo.value
    assert exc.status_code == 404
    assert captured["tenant_id"] == "SH00000001"
    assert captured["accessible_device_ids"] == ["DEV-ALLOWED"]


@pytest.mark.asyncio
async def test_formatted_results_failed_job_returns_truthful_409_contract(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(analytics, "_resolve_accessible_job_device_ids", AsyncMock(return_value=["DEV-1"]))

    job = SimpleNamespace(
        status=JobStatus.FAILED.value,
        error_code="MODEL_EXECUTION_FAILED",
        error_message="Formatter input never materialized",
    )
    repo = SimpleNamespace(get_job_scoped=AsyncMock(return_value=job))

    with pytest.raises(HTTPException) as excinfo:
        await analytics.get_formatted_results("job-failed", _request(), result_repo=repo)

    exc = excinfo.value
    assert exc.status_code == 409
    assert exc.detail["error"] == "RESULT_UNAVAILABLE"
    assert exc.detail["status"] == "failed"
    assert exc.detail["result_ready"] is False
    assert exc.detail["error_code"] == "MODEL_EXECUTION_FAILED"
    assert exc.detail["error_message"] == "Formatter input never materialized"


@pytest.mark.asyncio
async def test_formatted_results_completed_without_payload_returns_truthful_404(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(analytics, "_resolve_accessible_job_device_ids", AsyncMock(return_value=["DEV-1"]))

    job = SimpleNamespace(
        status=JobStatus.COMPLETED.value,
        results={"coverage_result": {"terminal_status": "business_blocked"}},
    )
    repo = SimpleNamespace(get_job_scoped=AsyncMock(return_value=job))

    with pytest.raises(HTTPException) as excinfo:
        await analytics.get_formatted_results("job-no-formatted", _request(), result_repo=repo)

    exc = excinfo.value
    assert exc.status_code == 404
    assert exc.detail == "Formatted results not available for this job"


@pytest.mark.asyncio
async def test_job_status_returns_truthful_pending_contract_from_in_memory_queue(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(analytics, "_resolve_accessible_job_device_ids", AsyncMock(return_value=["DEV-1"]))
    repo = SimpleNamespace(get_job_scoped=AsyncMock(side_effect=JobNotFoundError("queued only")))
    created_at = datetime.now(timezone.utc)

    response = await analytics.get_job_status(
        "job-pending",
        _request_with_app(
            pending_jobs={
                "job-pending": {
                    "created_at": created_at,
                    "message": "Job queued successfully",
                }
            }
        ),
        result_repo=repo,
    )

    assert response.status == JobStatus.PENDING
    assert response.message == "Job queued successfully"
    assert response.phase == "queued"
    assert response.phase_label == "Queued"
    assert response.phase_progress == 0.0
    assert response.result_ready is False
    assert response.artifact_ready is False
    assert response.download_ready is False
    assert response.result_url == "/api/v1/analytics/results/job-pending"
    assert response.download_url is None
