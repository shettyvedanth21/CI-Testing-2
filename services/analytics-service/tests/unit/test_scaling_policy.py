from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests._bootstrap import bootstrap_test_imports

bootstrap_test_imports()

from shared.tenant_context import TenantContext
from src.api.routes import analytics
from src.config.settings import get_settings
from src.models.schemas import AnalyticsRequest
from src.services.scaling_policy import AnalyticsScalingPolicy


class _RepoProbe:
    def __init__(self, *, global_pending=0, global_active=0, tenant_pending=None, tenant_active=None):
        self.global_pending = global_pending
        self.global_active = global_active
        self.tenant_pending = tenant_pending or {}
        self.tenant_active = tenant_active or {}

    async def count_jobs(self, statuses=None, tenant_id=None, attempts_gte=None):
        if attempts_gte is not None:
            return 0
        normalized = tuple(sorted(statuses or []))
        if tenant_id is None:
            if normalized == ("pending",):
                return self.global_pending
            return self.global_active
        if normalized == ("pending",):
            return int(self.tenant_pending.get(tenant_id, 0))
        return int(self.tenant_active.get(tenant_id, 0))


@pytest.mark.asyncio
async def test_scaling_policy_rejects_global_backlog(monkeypatch):
    settings = get_settings()
    repo = _RepoProbe(global_pending=settings.queue_backlog_reject_threshold)

    decision = await AnalyticsScalingPolicy(settings, repo).evaluate_submission(
        tenant_id="tenant-a",
        requested_jobs=1,
    )

    assert decision.allowed is False
    assert decision.status_code == 503
    assert decision.error_code == "ANALYTICS_BACKLOG_OVERLOADED"


@pytest.mark.asyncio
async def test_scaling_policy_enforces_tenant_fairness_without_blocking_other_tenants(monkeypatch):
    settings = get_settings()
    repo = _RepoProbe(
        global_pending=0,
        global_active=0,
        tenant_pending={"tenant-a": settings.tenant_max_queued_jobs},
        tenant_active={"tenant-a": 0},
    )
    policy = AnalyticsScalingPolicy(settings, repo)

    blocked = await policy.evaluate_submission(tenant_id="tenant-a", requested_jobs=1)
    allowed = await policy.evaluate_submission(tenant_id="tenant-b", requested_jobs=1)

    assert blocked.allowed is False
    assert blocked.status_code == 429
    assert blocked.error_code == "TENANT_QUEUE_CAP_EXCEEDED"
    assert allowed.allowed is True


@pytest.mark.asyncio
async def test_scaling_policy_allows_submission_when_only_active_cap_is_saturated(monkeypatch):
    settings = get_settings()
    repo = _RepoProbe(
        global_pending=0,
        global_active=settings.tenant_max_active_jobs,
        tenant_pending={"tenant-a": 0},
        tenant_active={"tenant-a": settings.tenant_max_active_jobs},
    )

    decision = await AnalyticsScalingPolicy(settings, repo).evaluate_submission(
        tenant_id="tenant-a",
        requested_jobs=1,
    )

    assert decision.allowed is True
    assert decision.status_code == 202


@pytest.mark.asyncio
async def test_run_analytics_returns_explicit_tenant_cap_rejection(monkeypatch):
    now = datetime.now(timezone.utc)
    request = AnalyticsRequest(
        device_id="dev-a",
        start_time=now - timedelta(hours=1),
        end_time=now,
        analysis_type="anomaly",
        model_name="anomaly_ensemble",
    )

    ctx = TenantContext(
        tenant_id="tenant-a",
        user_id="user-1",
        role="plant_manager",
        plant_ids=["plant-a"],
        is_super_admin=False,
    )
    app_request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(analytics_rejections={})),
        state=SimpleNamespace(tenant_context=ctx),
    )
    result_repo = MagicMock()
    result_repo.count_jobs = AsyncMock(side_effect=[0, get_settings().tenant_max_queued_jobs])
    result_repo.create_job = AsyncMock()
    result_repo.update_job_queue_metadata = AsyncMock()
    job_queue = MagicMock()
    job_queue.submit_job = AsyncMock()

    monkeypatch.setattr(analytics, "check_worker_alive", AsyncMock(return_value=True))
    monkeypatch.setattr(
        "src.api.routes.analytics.AnalyticsDeviceScopeService.normalize_requested_device_ids",
        AsyncMock(return_value=["dev-a"]),
    )

    with pytest.raises(analytics.HTTPException) as exc_info:
        await analytics.run_analytics(request, app_request, job_queue, result_repo)

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail["error"] == "TENANT_QUEUE_CAP_EXCEEDED"
    assert app_request.app.state.analytics_rejections["tenant_cap"] == 1
    result_repo.create_job.assert_not_awaited()
    job_queue.submit_job.assert_not_awaited()
