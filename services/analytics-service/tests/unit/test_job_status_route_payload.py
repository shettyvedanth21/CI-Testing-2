"""Tests for analytics status payload enrichment."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests._bootstrap import bootstrap_test_imports

bootstrap_test_imports()

from src.api.routes import analytics
from src.infrastructure.mysql_repository import MySQLResultRepository
from src.models.database import AnalyticsJob, Base


class _Ctx:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_build_status_response_exposes_phase_and_eta(monkeypatch):
    now = datetime.now(timezone.utc)
    job = SimpleNamespace(
        status="running",
        progress=61.5,
        message="Training temporal autoencoder",
        error_message=None,
        error_code=None,
        created_at=now,
        started_at=now,
        completed_at=None,
        queue_position=9,
        attempt=1,
        worker_lease_expires_at=now,
        last_heartbeat_at=now,
        phase="model_execution",
        phase_label="Training temporal autoencoder",
        phase_progress=0.33,
    )

    monkeypatch.setattr(analytics, "async_session_maker", lambda: _Ctx())

    class _FakeEstimator:
        def __init__(self, _session):
            pass

        async def estimate(self, _job):
            return SimpleNamespace(
                queue_position=2,
                estimated_wait_seconds=120,
                estimated_completion_seconds=300,
                estimate_quality="medium",
                activity_state="active",
                eta_reliable=True,
                heartbeat_age_seconds=12,
            )

    monkeypatch.setattr(analytics, "JobStatusEstimator", _FakeEstimator)

    response = await analytics._build_status_response("job-1", job)

    assert response.phase == "model_execution"
    assert response.phase_label == "Training temporal autoencoder"
    assert response.phase_progress == pytest.approx(0.33)
    assert response.queue_position == 2
    assert response.estimated_wait_seconds == 120
    assert response.estimated_completion_seconds == 300
    assert response.estimate_quality == "medium"
    assert response.last_heartbeat_at == now
    assert response.activity_state == "active"
    assert response.eta_reliable is True
    assert response.heartbeat_age_seconds == 12
    assert response.result_ready is False
    assert response.artifact_ready is False
    assert response.download_ready is False
    assert response.result_url == "/api/v1/analytics/results/job-1"
    assert response.download_url is None


@pytest.mark.asyncio
async def test_build_status_response_only_marks_result_ready_when_completed_result_exists(monkeypatch):
    now = datetime.now(timezone.utc)
    job = SimpleNamespace(
        status="completed",
        progress=100.0,
        message="Completed",
        error_message=None,
        error_code=None,
        created_at=now,
        started_at=now,
        completed_at=now,
        queue_position=1,
        attempt=1,
        worker_lease_expires_at=None,
        last_heartbeat_at=None,
        phase="completed",
        phase_label="Completed",
        phase_progress=1.0,
        results={"formatted": {"summary": "ready"}},
    )

    monkeypatch.setattr(analytics, "async_session_maker", lambda: _Ctx())

    class _FakeEstimator:
        def __init__(self, _session):
            pass

        async def estimate(self, _job):
            return SimpleNamespace(
                queue_position=None,
                estimated_wait_seconds=None,
                estimated_completion_seconds=None,
                estimate_quality="high",
                activity_state=None,
                eta_reliable=None,
                heartbeat_age_seconds=None,
            )

    monkeypatch.setattr(analytics, "JobStatusEstimator", _FakeEstimator)

    response = await analytics._build_status_response("job-ready", job)

    assert response.result_ready is True
    assert response.result_url == "/api/v1/analytics/results/job-ready"
    assert response.artifact_ready is False
    assert response.download_ready is False
    assert response.download_url is None


@pytest.mark.asyncio
async def test_fleet_progress_can_read_parent_results_from_listed_job():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(AnalyticsJob.__table__.create)

    now = datetime.now(timezone.utc)

    async with session_factory() as session:
        session.add(
            AnalyticsJob(
                job_id="fleet-parent-1",
                device_id="ALL",
                job_kind="fleet_parent",
                parent_job_id=None,
                analysis_type="anomaly",
                model_name="isolation_forest",
                date_range_start=now,
                date_range_end=now,
                parameters={"tenant_id": "SH00000001", "device_ids": ["D1", "D2", "D3"]},
                status="running",
                progress=25.0,
                results={"skipped_children": ["D3"]},
            )
        )
        session.add(
            AnalyticsJob(
                job_id="fleet-child-1",
                device_id="D1",
                job_kind="fleet_child",
                parent_job_id="fleet-parent-1",
                analysis_type="anomaly",
                model_name="isolation_forest",
                date_range_start=now,
                date_range_end=now,
                parameters={"tenant_id": "SH00000001"},
                status="completed",
                progress=100.0,
            )
        )
        session.add(
            AnalyticsJob(
                job_id="fleet-child-2",
                device_id="D2",
                job_kind="fleet_child",
                parent_job_id="fleet-parent-1",
                analysis_type="anomaly",
                model_name="isolation_forest",
                date_range_start=now,
                date_range_end=now,
                parameters={"tenant_id": "SH00000001"},
                status="running",
                progress=40.0,
            )
        )
        await session.commit()

        repo = MySQLResultRepository(session)
        jobs = await repo.list_jobs(job_kinds=["fleet_parent"])
        assert len(jobs) == 1

        fleet_progress = await analytics._build_fleet_progress(session, jobs[0])

    await engine.dispose()

    assert fleet_progress is not None
    assert fleet_progress.selected_device_count == 3
    assert fleet_progress.completed_devices == 1
    assert fleet_progress.running_devices == 1
    assert fleet_progress.skipped_devices == 1


@pytest.mark.asyncio
async def test_build_list_status_responses_batches_estimation_results_and_fleet_progress(monkeypatch):
    now = datetime.now(timezone.utc)
    jobs = [
        SimpleNamespace(
            job_id="pending-1",
            job_kind="single",
            device_id="D1",
            parameters={},
            status="pending",
            progress=0.0,
            message="Queued",
            error_message=None,
            error_code=None,
            created_at=now,
            started_at=None,
            completed_at=None,
            queue_position=7,
            attempt=1,
            worker_lease_expires_at=None,
            last_heartbeat_at=None,
            phase="queued",
            phase_label="Queued",
            phase_progress=0.0,
        ),
        SimpleNamespace(
            job_id="fleet-parent-1",
            job_kind="fleet_parent",
            device_id="ALL",
            parameters={"device_ids": ["D1", "D2", "D3"], "fleet_mode": "best_effort_exact"},
            status="completed",
            progress=100.0,
            message="Completed",
            error_message=None,
            error_code=None,
            created_at=now,
            started_at=now,
            completed_at=now,
            queue_position=None,
            attempt=1,
            worker_lease_expires_at=None,
            last_heartbeat_at=None,
            phase="completed",
            phase_label="Completed",
            phase_progress=1.0,
        ),
    ]

    estimator_calls: list[list[str]] = []

    class _FakeEstimator:
        def __init__(self, _session):
            pass

        async def estimate_many(self, raw_jobs):
            estimator_calls.append([job.job_id for job in raw_jobs])
            return {
                "pending-1": SimpleNamespace(
                    queue_position=2,
                    estimated_wait_seconds=90,
                    estimated_completion_seconds=150,
                    estimate_quality="medium",
                    activity_state=None,
                    eta_reliable=None,
                    heartbeat_age_seconds=None,
                ),
                "fleet-parent-1": SimpleNamespace(
                    queue_position=None,
                    estimated_wait_seconds=None,
                    estimated_completion_seconds=None,
                    estimate_quality="high",
                    activity_state=None,
                    eta_reliable=None,
                    heartbeat_age_seconds=None,
                ),
            }

    monkeypatch.setattr(analytics, "JobStatusEstimator", _FakeEstimator)

    async def _fake_results_map(_session, job_ids):
        return {
            "fleet-parent-1": {
                "formatted": {"summary": "ready"},
                "skipped_children": ["D3"],
            }
        }

    async def _fake_fleet_progress_map(_session, raw_jobs, results_by_job_id):
        assert results_by_job_id["fleet-parent-1"]["skipped_children"] == ["D3"]
        return {
            "fleet-parent-1": analytics.FleetProgressResponse(
                selected_device_count=3,
                child_jobs_total=2,
                queued_devices=0,
                running_devices=0,
                completed_devices=2,
                failed_devices=0,
                skipped_devices=1,
                coverage_pct=66.7,
            )
        }

    monkeypatch.setattr(analytics, "_load_job_results_map", _fake_results_map)
    monkeypatch.setattr(analytics, "_build_fleet_progress_map", _fake_fleet_progress_map)
    monkeypatch.setattr(analytics, "async_session_maker", lambda: _Ctx())

    responses = await analytics._build_list_status_responses(jobs)

    assert estimator_calls == [["pending-1", "fleet-parent-1"]]
    assert len(responses) == 2
    assert responses[0].job_id == "pending-1"
    assert responses[0].queue_position == 2
    assert responses[0].estimated_wait_seconds == 90
    assert responses[0].result_ready is False
    assert responses[1].job_id == "fleet-parent-1"
    assert responses[1].workflow_kind == "fleet"
    assert responses[1].result_ready is True
    assert responses[1].fleet_progress is not None
    assert responses[1].fleet_progress.skipped_devices == 1
