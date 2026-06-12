from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests._bootstrap import bootstrap_test_imports

bootstrap_test_imports()

from shared.tenant_context import TenantContext
from src.api.routes import analytics
from src.models.schemas import AnalyticsRequest, FleetAnalyticsRequest
from src.services.device_scope import AnalyticsDeviceScopeService


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _FakeAsyncClient:
    def __init__(self, responses):
        self._responses = list(responses)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def get(self, *args, **kwargs):
        if not self._responses:
            raise AssertionError("No fake response queued for httpx.AsyncClient.get")
        return self._responses.pop(0)


@pytest.mark.asyncio
async def test_device_scope_filters_accessible_devices(monkeypatch):
    ctx = TenantContext(
        tenant_id="SH00000001",
        user_id="user-1",
        role="plant_manager",
        plant_ids=["plant-a"],
        is_super_admin=False,
    )

    fake_payload = {
        "data": [
            {"device_id": "dev-a", "plant_id": "plant-a"},
            {"device_id": "dev-b", "plant_id": "plant-b"},
        ],
        "total_pages": 1,
    }
    monkeypatch.setattr(
        "src.services.device_scope.httpx.AsyncClient",
        lambda *args, **kwargs: _FakeAsyncClient([_FakeResponse(fake_payload)]),
    )

    service = AnalyticsDeviceScopeService(ctx)
    assert await service.resolve_accessible_device_ids() == ["dev-a"]


@pytest.mark.asyncio
async def test_device_scope_rejects_out_of_scope_requested_devices(monkeypatch):
    ctx = TenantContext(
        tenant_id="SH00000001",
        user_id="user-1",
        role="operator",
        plant_ids=["plant-a"],
        is_super_admin=False,
    )

    fake_payload = {
        "data": [
            {"device_id": "dev-a", "plant_id": "plant-a"},
            {"device_id": "dev-b", "plant_id": "plant-b"},
        ],
        "total_pages": 1,
    }
    monkeypatch.setattr(
        "src.services.device_scope.httpx.AsyncClient",
        lambda *args, **kwargs: _FakeAsyncClient([_FakeResponse(fake_payload)]),
    )

    service = AnalyticsDeviceScopeService(ctx)
    with pytest.raises(analytics.HTTPException) as exc_info:
        await service.normalize_requested_device_ids(["dev-b"])
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_run_fleet_analytics_normalizes_device_scope_before_enqueuing(monkeypatch):
    now = datetime.now(timezone.utc)
    request = FleetAnalyticsRequest(
        device_ids=[],
        start_time=now - timedelta(hours=1),
        end_time=now,
        analysis_type="anomaly",
        model_name="anomaly_ensemble",
        parameters={"foo": "bar"},
    )

    ctx = TenantContext(
        tenant_id="SH00000001",
        user_id="user-1",
        role="plant_manager",
        plant_ids=["plant-a"],
        is_super_admin=False,
    )
    app = SimpleNamespace(state=SimpleNamespace())
    app_request = SimpleNamespace(app=app, state=SimpleNamespace(tenant_context=ctx))
    result_repo = MagicMock()
    result_repo.create_job = AsyncMock()
    result_repo.update_job_status = AsyncMock()
    result_repo.update_job_queue_metadata = AsyncMock()
    result_repo.count_jobs = AsyncMock(return_value=0)
    result_repo.find_active_duplicate = AsyncMock(return_value=None)
    job_queue = MagicMock()
    job_queue.submit_job = AsyncMock()

    recorded = {}

    async def fake_normalize(self, requested_device_ids):
        recorded["requested_device_ids"] = list(requested_device_ids)
        return ["dev-a", "dev-b"]

    monkeypatch.setattr(analytics, "check_worker_alive", AsyncMock(return_value=True))
    monkeypatch.setattr(
        "src.api.routes.analytics.AnalyticsDeviceScopeService.normalize_requested_device_ids",
        fake_normalize,
    )

    response = await analytics.run_fleet_analytics(request, app_request, job_queue, result_repo)

    assert response.status.value == "pending"
    assert recorded["requested_device_ids"] == []
    assert request.device_ids == ["dev-a", "dev-b"]
    create_call = result_repo.create_job.await_args.kwargs
    assert create_call["parameters"]["device_ids"] == ["dev-a", "dev-b"]
    result_repo.update_job_queue_metadata.assert_awaited_once()
    status_call = result_repo.update_job_status.await_args.kwargs
    assert status_call["phase"] == "queued"
    submit_call = job_queue.submit_job.await_args.kwargs
    assert submit_call["job_id"] == response.job_id
    assert "\"job_type\":\"fleet_parent_analytics\"" in submit_call["raw_payload"]
    assert "\"device_ids\":[\"dev-a\",\"dev-b\"]" in submit_call["raw_payload"]


@pytest.mark.asyncio
async def test_run_analytics_normalizes_single_device_scope_before_enqueuing(monkeypatch):
    now = datetime.now(timezone.utc)
    request = AnalyticsRequest(
        device_id="dev-b",
        start_time=now - timedelta(hours=1),
        end_time=now,
        analysis_type="anomaly",
        model_name="anomaly_ensemble",
    )

    ctx = TenantContext(
        tenant_id="SH00000001",
        user_id="user-1",
        role="plant_manager",
        plant_ids=["plant-a"],
        is_super_admin=False,
    )
    app_request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()), state=SimpleNamespace(tenant_context=ctx))
    result_repo = MagicMock()
    result_repo.create_job = AsyncMock()
    result_repo.update_job_queue_metadata = AsyncMock()
    result_repo.count_jobs = AsyncMock(return_value=0)
    result_repo.find_active_duplicate = AsyncMock(return_value=None)
    job_queue = MagicMock()
    job_queue.submit_job = AsyncMock()
    job_queue.size = MagicMock(return_value=0)

    recorded = {}

    async def fake_normalize(self, requested_device_ids):
        recorded["requested_device_ids"] = list(requested_device_ids)
        return ["dev-a"]

    monkeypatch.setattr(analytics, "check_worker_alive", AsyncMock(return_value=True))
    monkeypatch.setattr(
        "src.api.routes.analytics.AnalyticsDeviceScopeService.normalize_requested_device_ids",
        fake_normalize,
    )

    response = await analytics.run_analytics(request, app_request, job_queue, result_repo)

    assert response.status.value == "pending"
    assert recorded["requested_device_ids"] == ["dev-b"]
    assert request.device_id == "dev-a"
    create_call = result_repo.create_job.await_args.kwargs
    assert create_call["device_id"] == "dev-a"
    submit_call = job_queue.submit_job.await_args.kwargs
    assert submit_call["job_id"] == response.job_id


@pytest.mark.asyncio
async def test_run_analytics_rejects_out_of_scope_single_device(monkeypatch):
    now = datetime.now(timezone.utc)
    request = AnalyticsRequest(
        device_id="dev-b",
        start_time=now - timedelta(hours=1),
        end_time=now,
        analysis_type="anomaly",
        model_name="anomaly_ensemble",
    )

    ctx = TenantContext(
        tenant_id="SH00000001",
        user_id="user-1",
        role="plant_manager",
        plant_ids=["plant-a"],
        is_super_admin=False,
    )
    app_request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()), state=SimpleNamespace(tenant_context=ctx))
    result_repo = MagicMock()
    result_repo.create_job = AsyncMock()
    result_repo.update_job_queue_metadata = AsyncMock()
    result_repo.count_jobs = AsyncMock(return_value=0)
    result_repo.find_active_duplicate = AsyncMock(return_value=None)
    job_queue = MagicMock()
    job_queue.submit_job = AsyncMock()
    job_queue.size = MagicMock(return_value=0)

    monkeypatch.setattr(analytics, "check_worker_alive", AsyncMock(return_value=True))

    async def fake_normalize(self, requested_device_ids):
        raise analytics.HTTPException(
            status_code=403,
            detail={
                "error": "ANALYTICS_SCOPE_FORBIDDEN",
                "message": "Fleet analytics can only run for devices inside your assigned plant scope.",
            },
        )

    monkeypatch.setattr(
        "src.api.routes.analytics.AnalyticsDeviceScopeService.normalize_requested_device_ids",
        fake_normalize,
    )

    with pytest.raises(analytics.HTTPException) as exc_info:
        await analytics.run_analytics(request, app_request, job_queue, result_repo)

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["error"] == "ANALYTICS_SCOPE_FORBIDDEN"
    result_repo.create_job.assert_not_awaited()
    job_queue.submit_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_analytics_reuses_duplicate_job_before_admission(monkeypatch):
    now = datetime.now(timezone.utc)
    request = AnalyticsRequest(
        device_id="dev-b",
        start_time=now - timedelta(hours=1),
        end_time=now,
        analysis_type="anomaly",
        model_name="anomaly_ensemble",
    )

    ctx = TenantContext(
        tenant_id="SH00000001",
        user_id="user-1",
        role="plant_manager",
        plant_ids=["plant-a"],
        is_super_admin=False,
    )
    app_request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()), state=SimpleNamespace(tenant_context=ctx))
    result_repo = MagicMock()
    result_repo.create_job = AsyncMock()
    result_repo.update_job_queue_metadata = AsyncMock()
    result_repo.count_jobs = AsyncMock(return_value=0)
    result_repo.find_active_duplicate = AsyncMock(
        return_value=SimpleNamespace(job_id="existing-job", status="pending")
    )
    job_queue = MagicMock()
    job_queue.submit_job = AsyncMock()
    job_queue.size = MagicMock(return_value=0)

    async def fake_normalize(self, requested_device_ids):
        return ["dev-a"]

    monkeypatch.setattr(analytics, "check_worker_alive", AsyncMock(return_value=True))
    monkeypatch.setattr(
        "src.api.routes.analytics.AnalyticsDeviceScopeService.normalize_requested_device_ids",
        fake_normalize,
    )

    response = await analytics.run_analytics(request, app_request, job_queue, result_repo)

    assert response.job_id == "existing-job"
    assert response.status.value == "pending"
    assert response.message == "Matching analytics job already queued"
    result_repo.count_jobs.assert_not_awaited()
    result_repo.create_job.assert_not_awaited()
    result_repo.update_job_queue_metadata.assert_not_awaited()
    job_queue.submit_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_super_admin_normalize_uses_requested_devices_without_full_scope_fetch():
    ctx = TenantContext(
        tenant_id="SH00000001",
        user_id="super-user",
        role="super_admin",
        plant_ids=[],
        is_super_admin=True,
    )
    service = AnalyticsDeviceScopeService(ctx)
    normalized = await service.normalize_requested_device_ids(["dev-a", "dev-a", "dev-b"])
    assert normalized == ["dev-a", "dev-b"]
