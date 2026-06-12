from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests._bootstrap import bootstrap_test_imports

bootstrap_test_imports()

from src.workers.job_worker import JobWorker


class _Ctx:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _job(
    job_id: str,
    *,
    tenant_id: str,
    parent_job_id: str | None = None,
    status: str = "pending",
    job_kind: str = "fleet_child",
    created_at: datetime | None = None,
    device_id: str | None = None,
    results: dict | None = None,
    message: str | None = None,
):
    return SimpleNamespace(
        job_id=job_id,
        tenant_id=tenant_id,
        parent_job_id=parent_job_id,
        status=status,
        job_kind=job_kind,
        created_at=created_at or datetime.now(timezone.utc),
        parameters={"tenant_id": tenant_id},
        device_id=device_id or job_id,
        analysis_type="anomaly",
        model_name="isolation_forest",
        date_range_start=created_at or datetime.now(timezone.utc),
        date_range_end=(created_at or datetime.now(timezone.utc)) + timedelta(hours=1),
        results=results or {},
        error_message=message,
        message=message,
    )


def test_select_child_jobs_for_dispatch_respects_tenant_and_parent_caps():
    worker = JobWorker(MagicMock(), max_concurrent=3)
    worker._fleet_parent_max_active_children = 1

    base = datetime.now(timezone.utc)
    candidates = [
        _job("A1", tenant_id="tenant-a", parent_job_id="parent-a", created_at=base + timedelta(seconds=1)),
        _job("A2", tenant_id="tenant-a", parent_job_id="parent-a", created_at=base + timedelta(seconds=2)),
        _job("B1", tenant_id="tenant-b", parent_job_id="parent-b1", created_at=base + timedelta(seconds=3)),
        _job("B2", tenant_id="tenant-b", parent_job_id="parent-b1", created_at=base + timedelta(seconds=4)),
        _job("B3", tenant_id="tenant-b", parent_job_id="parent-b2", created_at=base + timedelta(seconds=5)),
    ]
    occupied_rows = [
        _job(
            f"running-{idx}",
            tenant_id="tenant-a",
            status="running",
            job_kind="single",
            parent_job_id=None,
            created_at=base - timedelta(seconds=idx + 1),
        )
        for idx in range(7)
    ]

    selected = worker._select_child_jobs_for_dispatch(candidates, occupied_rows, global_headroom=3)

    assert [job.job_id for job in selected] == ["A1", "B1", "B3"]


@pytest.mark.asyncio
async def test_reconcile_fleet_parent_completes_from_child_truth(monkeypatch):
    worker = JobWorker(MagicMock(), max_concurrent=3)
    parent_job = SimpleNamespace(
        job_id="parent-1",
        analysis_type="anomaly",
        parameters={"device_ids": ["d1", "d2", "d3"]},
        results={
            "children": {"d1": "child-1", "d2": "child-2"},
            "skipped_children": [{"device_id": "d3", "reason": "dataset_not_ready"}],
        },
        status="running",
    )
    child_rows = [
        SimpleNamespace(
            job_id="child-1",
            device_id="d1",
            status="completed",
            results={"formatted": {"device_id": "d1", "score": 0.9}},
            error_message=None,
            message=None,
        ),
        SimpleNamespace(
            job_id="child-2",
            device_id="d2",
            status="failed",
            results={},
            error_message="model failed",
            message="model failed",
        ),
    ]

    fake_repo = SimpleNamespace(
        get_job=AsyncMock(return_value=parent_job),
        list_jobs_for_parent=AsyncMock(return_value=child_rows),
        update_job_progress=AsyncMock(),
        save_results=AsyncMock(),
        update_job_status=AsyncMock(),
    )

    monkeypatch.setattr("src.workers.job_worker.async_session_maker", lambda: _Ctx())
    monkeypatch.setattr("src.workers.job_worker.MySQLResultRepository", lambda session, ctx: fake_repo)

    class _Formatter:
        def format_fleet_results(self, **kwargs):
            return {"analysis_type": "fleet", "device_summaries": [{"device_id": "d1"}]}

    monkeypatch.setattr("src.workers.job_worker.ResultFormatter", _Formatter)

    await worker._reconcile_fleet_parent("parent-1")

    first_progress = fake_repo.update_job_progress.await_args_list[0].kwargs
    assert first_progress["phase"] == "child_execution"
    assert first_progress["phase_progress"] == pytest.approx(1.0)

    saved_results = fake_repo.save_results.await_args.kwargs["results"]
    metadata = saved_results["formatted"]["execution_metadata"]
    assert metadata["selected_device_count"] == 3
    assert metadata["coverage_pct"] == pytest.approx(33.3)
    assert metadata["devices_failed"][0]["device_id"] == "d2"
    assert metadata["devices_skipped"][0]["device_id"] == "d3"

    final_status = fake_repo.update_job_status.await_args.kwargs
    assert final_status["status"].value == "completed"
    assert "1/3 devices analyzed" in final_status["message"]


@pytest.mark.asyncio
async def test_reconcile_fleet_parent_marks_all_business_blocked_children_completed(monkeypatch):
    worker = JobWorker(MagicMock(), max_concurrent=3)
    parent_job = SimpleNamespace(
        job_id="parent-blocked",
        analysis_type="anomaly",
        parameters={"device_ids": ["d1", "d2"]},
        results={"children": {"d1": "child-1", "d2": "child-2"}},
        status="running",
    )
    child_rows = [
        SimpleNamespace(
            job_id="child-1",
            device_id="d1",
            status="completed",
            results={
                "coverage_result": {
                    "level": "no_coverage",
                    "usable_for_business_decisions": False,
                    "message": "No telemetry was available for the selected window.",
                },
                "formatted": {"device_id": "d1", "status": "no_data"},
            },
            error_message=None,
            message=None,
        ),
        SimpleNamespace(
            job_id="child-2",
            device_id="d2",
            status="completed",
            results={
                "coverage_result": {
                    "level": "insufficient_coverage",
                    "usable_for_business_decisions": False,
                    "message": "Telemetry coverage is insufficient for a trustworthy result.",
                },
                "formatted": {"device_id": "d2", "status": "insufficient_coverage"},
            },
            error_message=None,
            message=None,
        ),
    ]

    fake_repo = SimpleNamespace(
        get_job=AsyncMock(return_value=parent_job),
        list_jobs_for_parent=AsyncMock(return_value=child_rows),
        update_job_progress=AsyncMock(),
        save_results=AsyncMock(),
        update_job_status=AsyncMock(),
    )

    monkeypatch.setattr("src.workers.job_worker.async_session_maker", lambda: _Ctx())
    monkeypatch.setattr("src.workers.job_worker.MySQLResultRepository", lambda session, ctx: fake_repo)

    class _Formatter:
        def format_fleet_results(self, **kwargs):
            return {"analysis_type": "fleet", "device_summaries": []}

    monkeypatch.setattr("src.workers.job_worker.ResultFormatter", _Formatter)

    await worker._reconcile_fleet_parent("parent-blocked")

    saved_results = fake_repo.save_results.await_args.kwargs["results"]
    assert saved_results["coverage_result"]["usable_for_business_decisions"] is False
    assert saved_results["coverage_result"]["level"] == "insufficient_coverage"

    final_status = fake_repo.update_job_status.await_args.kwargs
    assert final_status["status"].value == "completed"
    assert final_status["phase"] == "insufficient_coverage"
    assert final_status["error_message"] is None


@pytest.mark.asyncio
async def test_dispatch_pending_child_jobs_releases_claim_when_queue_submit_fails(monkeypatch):
    queue = MagicMock()
    queue.submit_job = AsyncMock(side_effect=RuntimeError("redis unavailable"))
    worker = JobWorker(queue, max_concurrent=3)

    candidate = _job(
        "child-1",
        tenant_id="tenant-a",
        parent_job_id="parent-a",
        created_at=datetime.now(timezone.utc),
    )
    fake_repo = SimpleNamespace(
        list_jobs=AsyncMock(side_effect=AssertionError("generic list_jobs should not be used for worker scans")),
        list_jobs_for_worker_scan=AsyncMock(
            side_effect=[
                [],
                [],
                [candidate],
            ]
        ),
        claim_child_dispatch=AsyncMock(side_effect=lambda **_: True),
        release_child_dispatch=AsyncMock(),
    )

    events: list[str] = []

    async def _claim(**kwargs):
        events.append(f"claim:{kwargs['job_id']}")
        return True

    async def _submit_job(**kwargs):
        events.append(f"submit:{kwargs['job_id']}")
        raise RuntimeError("redis unavailable")

    async def _release(job_id: str):
        events.append(f"release:{job_id}")

    fake_repo.claim_child_dispatch = AsyncMock(side_effect=_claim)
    fake_repo.release_child_dispatch = AsyncMock(side_effect=_release)
    queue.submit_job = AsyncMock(side_effect=_submit_job)

    monkeypatch.setattr("src.workers.job_worker.async_session_maker", lambda: _Ctx())
    monkeypatch.setattr("src.workers.job_worker.MySQLResultRepository", lambda session, ctx: fake_repo)

    with pytest.raises(RuntimeError, match="redis unavailable"):
        await worker._dispatch_pending_child_jobs()

    assert events == ["claim:child-1", "submit:child-1", "release:child-1"]


@pytest.mark.asyncio
async def test_recover_stale_running_jobs_uses_narrow_recovery_scan(monkeypatch):
    worker = JobWorker(MagicMock(), max_concurrent=3)
    stale_job = _job(
        "stale-child-1",
        tenant_id="tenant-a",
        parent_job_id="parent-a",
        created_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        status="running",
    )
    stale_job.worker_lease_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    stale_job.attempt = 1
    queue = AsyncMock()
    queue.submit_job = AsyncMock()
    worker._queue = queue

    fake_repo = SimpleNamespace(
        list_jobs=AsyncMock(side_effect=AssertionError("generic list_jobs should not be used for stale recovery")),
        list_running_jobs_for_recovery=AsyncMock(return_value=[stale_job]),
        update_job_status=AsyncMock(),
        update_job_queue_metadata=AsyncMock(),
    )

    monkeypatch.setattr("src.workers.job_worker.async_session_maker", lambda: _Ctx())
    monkeypatch.setattr("src.workers.job_worker.MySQLResultRepository", lambda session, ctx: fake_repo)

    await worker._recover_stale_running_jobs()

    fake_repo.list_running_jobs_for_recovery.assert_awaited_once()
    queue.submit_job.assert_awaited_once()
