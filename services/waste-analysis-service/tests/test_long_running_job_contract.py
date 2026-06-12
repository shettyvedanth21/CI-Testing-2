from __future__ import annotations

import os
import sys
from datetime import date, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.requests import Request


os.environ.setdefault("DATABASE_URL", "mysql+aiomysql://test:test@127.0.0.1:3306/test_db")
os.environ.setdefault("INFLUXDB_URL", "http://localhost:8086")
os.environ.setdefault("INFLUXDB_TOKEN", "test-token")
os.environ.setdefault("DEVICE_SERVICE_URL", "http://device-service:8000")
os.environ.setdefault("REPORTING_SERVICE_URL", "http://reporting-service:8085")
os.environ.setdefault("ENERGY_SERVICE_URL", "http://energy-service:8010")
os.environ.setdefault("MINIO_ENDPOINT", "http://localhost:9000")
os.environ.setdefault("MINIO_EXTERNAL_URL", "http://localhost:9000")
os.environ.setdefault("MINIO_ACCESS_KEY", "minio")
os.environ.setdefault("MINIO_SECRET_KEY", "minio123")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

SERVICE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVICE_ROOT.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
SERVICES_ROOT = REPO_ROOT / "services"
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))

from src.handlers import waste_analysis as waste_analysis_module
from src import main as waste_main_module
from src.config import settings as settings_module
from src.models import Base, WasteAnalysisJob, WasteGranularity, WasteScope, WasteStatus
from src.schemas import WasteAnalysisRunRequest
from services.shared.tenant_context import TenantContext


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        yield factory
    finally:
        await engine.dispose()


def _request(path: str, tenant_id: str = "SH00000001") -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": [(b"x-tenant-id", tenant_id.encode("utf-8"))],
        "query_string": b"",
    }
    request = Request(scope)
    request.state.tenant_context = TenantContext(
        tenant_id=tenant_id,
        user_id="user-1",
        role="org_admin",
        plant_ids=[],
        is_super_admin=False,
    )
    request.state.role = "org_admin"
    return request


@pytest.mark.asyncio
async def test_submit_returns_durable_identity_and_contract_urls(session_factory, monkeypatch):
    from src.queue import InMemoryWasteQueue, WasteJob

    fake_queue = InMemoryWasteQueue()
    monkeypatch.setattr(
        "src.handlers.waste_analysis.get_waste_queue",
        lambda: fake_queue,
    )

    async with session_factory() as session:
        response = await waste_analysis_module.run_analysis(
            app_request=_request("/api/v1/waste/analysis/run"),
            request=WasteAnalysisRunRequest(
                scope="selected",
                device_ids=["AD00000001", "AD00000002"],
                start_date=date(2026, 4, 1),
                end_date=date(2026, 4, 2),
                granularity="daily",
                job_name="Waste smoke",
            ),
            db=session,
        )

    assert response.job_id
    assert response.status == "pending"
    assert response.backend_status == "pending"
    assert response.result_ready is False
    assert response.download_ready is False
    assert response.phase == "queued"
    assert response.phase_label == "Queued"
    assert response.result_url == f"/api/v1/waste/analysis/{response.job_id}/result"
    assert response.download_url is None
    assert response.requested_device_count == 2
    assert response.scope == "selected"


@pytest.mark.asyncio
async def test_status_endpoint_exposes_phase_readiness_and_timestamps(session_factory):
    async with session_factory() as session:
        session.add(
            WasteAnalysisJob(
                id="waste-running",
                tenant_id="SH00000001",
                job_name="Running waste job",
                scope=WasteScope.selected,
                device_ids=["AD00000001"],
                start_date=date(2026, 4, 10),
                end_date=date(2026, 4, 11),
                granularity=WasteGranularity.daily,
                status=WasteStatus.running,
                progress_pct=42,
                stage="Loading telemetry and computing... (1 of 4)",
                result_json={"tenant_id": "SH00000001"},
                created_at=datetime(2026, 4, 26, 10, 0, 0),
                started_at=datetime(2026, 4, 26, 10, 1, 0),
            )
        )
        await session.commit()

        payload = await waste_analysis_module.get_status(
            job_id="waste-running",
            request=_request("/api/v1/waste/analysis/waste-running/status"),
            db=session,
        )

    assert payload.status == "running"
    assert payload.phase == "execution"
    assert payload.phase_label == "Loading telemetry and computing... (1 of 4)"
    assert payload.phase_progress == pytest.approx(0.42)
    assert payload.result_ready is False
    assert payload.download_ready is False
    assert payload.result_url == "/api/v1/waste/analysis/waste-running/result"
    assert payload.download_url is None
    assert payload.created_at == "2026-04-26T10:00:00Z"
    assert payload.started_at == "2026-04-26T10:01:00Z"


@pytest.mark.asyncio
async def test_history_is_tenant_scoped_and_surfaces_quality_gate_business_outcome(session_factory):
    async with session_factory() as session:
        session.add_all(
            [
                WasteAnalysisJob(
                    id="waste-quality-gate-with-result",
                    tenant_id="SH00000001",
                    job_name="Insufficient coverage",
                    scope=WasteScope.all,
                    device_ids=None,
                    start_date=date(2026, 4, 5),
                    end_date=date(2026, 4, 6),
                    granularity=WasteGranularity.daily,
                    status=WasteStatus.completed,
                    progress_pct=100,
                    stage="Insufficient coverage",
                    result_json={
                        "tenant_id": "SH00000001",
                        "quality_gate_passed": False,
                        "coverage_result": {
                            "level": "insufficient_coverage",
                            "coverage_pct": 0.0,
                            "usable_for_business_decisions": False,
                            "artifact_generation_allowed": False,
                            "terminal_status": "business_blocked",
                            "message": "Telemetry coverage is insufficient for a trustworthy result.",
                        },
                        "device_summaries": [],
                        "skipped_devices": [{"device_id": "AD00000001"}],
                    },
                    error_code="INSUFFICIENT_TELEMETRY_COVERAGE",
                    error_message="Waste analysis completed with insufficient telemetry coverage",
                    created_at=datetime(2026, 4, 26, 8, 0, 0),
                    started_at=datetime(2026, 4, 26, 8, 1, 0),
                    completed_at=datetime(2026, 4, 26, 8, 5, 0),
                ),
                WasteAnalysisJob(
                    id="waste-hidden-other-tenant",
                    tenant_id="SH00000002",
                    job_name="Other tenant",
                    scope=WasteScope.selected,
                    device_ids=["AD00000099"],
                    start_date=date(2026, 4, 5),
                    end_date=date(2026, 4, 6),
                    granularity=WasteGranularity.daily,
                    status=WasteStatus.completed,
                    progress_pct=100,
                    stage="Complete ✓",
                    result_json={"tenant_id": "SH00000002", "device_summaries": [{"device_id": "AD00000099"}]},
                    s3_key="waste-reports/waste-hidden-other-tenant/file.pdf",
                    created_at=datetime(2026, 4, 26, 7, 0, 0),
                    started_at=datetime(2026, 4, 26, 7, 1, 0),
                    completed_at=datetime(2026, 4, 26, 7, 5, 0),
                ),
            ]
        )
        await session.commit()

        payload = await waste_analysis_module.get_history(
            request=_request("/api/v1/waste/analysis/history"),
            limit=20,
            offset=0,
            db=session,
        )

    assert [item.job_id for item in payload.items] == ["waste-quality-gate-with-result"]
    assert payload.items[0].result_ready is True
    assert payload.items[0].download_ready is False
    assert payload.items[0].status == "completed"
    assert payload.items[0].phase == "insufficient_coverage"
    assert payload.items[0].coverage_result["usable_for_business_decisions"] is False
    assert payload.items[0].requested_device_count == 1


@pytest.mark.asyncio
async def test_result_endpoint_returns_stable_not_ready_contract(session_factory):
    async with session_factory() as session:
        session.add(
            WasteAnalysisJob(
                id="waste-pending",
                tenant_id="SH00000001",
                job_name="Pending job",
                scope=WasteScope.selected,
                device_ids=["AD00000001"],
                start_date=date(2026, 4, 7),
                end_date=date(2026, 4, 8),
                granularity=WasteGranularity.daily,
                status=WasteStatus.pending,
                progress_pct=0,
                stage="Queued",
                result_json={"tenant_id": "SH00000001"},
                created_at=datetime(2026, 4, 26, 11, 0, 0),
            )
        )
        await session.commit()

        with pytest.raises(HTTPException) as excinfo:
            await waste_analysis_module.get_result(
                job_id="waste-pending",
                request=_request("/api/v1/waste/analysis/waste-pending/result"),
                db=session,
            )

    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["error"] == "RESULT_NOT_READY"
    assert excinfo.value.detail["status"] == "pending"
    assert excinfo.value.detail["result_ready"] is False


@pytest.mark.asyncio
async def test_download_endpoint_returns_stable_not_ready_contract(session_factory):
    async with session_factory() as session:
        session.add(
            WasteAnalysisJob(
                id="waste-failed-partial",
                tenant_id="SH00000001",
                job_name="Failed partial",
                scope=WasteScope.selected,
                device_ids=["AD00000001"],
                start_date=date(2026, 4, 9),
                end_date=date(2026, 4, 10),
                granularity=WasteGranularity.daily,
                status=WasteStatus.failed,
                progress_pct=100,
                stage="Quality gate failed",
                result_json={"tenant_id": "SH00000001", "quality_gate_passed": False},
                error_code="QUALITY_GATE_FAILED",
                error_message="Quality gate failed",
                created_at=datetime(2026, 4, 26, 12, 0, 0),
                started_at=datetime(2026, 4, 26, 12, 1, 0),
                completed_at=datetime(2026, 4, 26, 12, 5, 0),
            )
        )
        await session.commit()

        with pytest.raises(HTTPException) as excinfo:
            await waste_analysis_module.get_download(
                job_id="waste-failed-partial",
                request=_request("/api/v1/waste/analysis/waste-failed-partial/download"),
                db=session,
            )

        result_payload = await waste_analysis_module.get_result(
            job_id="waste-failed-partial",
            request=_request("/api/v1/waste/analysis/waste-failed-partial/result"),
            db=session,
        )

    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["error"] == "DOWNLOAD_NOT_READY"
    assert excinfo.value.detail["result_ready"] is True
    assert result_payload["quality_gate_passed"] is False


@pytest.mark.asyncio
async def test_download_endpoint_recovers_from_artifact_upload_failure(session_factory, monkeypatch):
    async with session_factory() as session:
        session.add(
            WasteAnalysisJob(
                id="waste-artifact-recoverable",
                tenant_id="SH00000001",
                job_name="Recoverable artifact",
                scope=WasteScope.selected,
                device_ids=["AD00000001"],
                start_date=date(2026, 4, 9),
                end_date=date(2026, 4, 10),
                granularity=WasteGranularity.daily,
                status=WasteStatus.completed,
                progress_pct=100,
                stage="Result ready · PDF unavailable",
                result_json={
                    "tenant_id": "SH00000001",
                    "job_id": "waste-artifact-recoverable",
                    "scope_label": "Selected Devices (1)",
                    "start_date": "2026-04-09",
                    "end_date": "2026-04-10",
                    "currency": "INR",
                    "device_summaries": [],
                    "insights": ["Insight"],
                    "warnings": [],
                    "total_waste_cost": 0.0,
                    "total_idle_kwh": 0.0,
                    "total_idle_label": "0 min",
                    "total_energy_kwh": 0.0,
                    "total_energy_cost": 0.0,
                    "worst_device": "N/A",
                },
                error_code="ARTIFACT_UPLOAD_FAILED",
                error_message="Result generated successfully, but PDF upload failed",
                created_at=datetime(2026, 4, 26, 12, 0, 0),
                started_at=datetime(2026, 4, 26, 12, 1, 0),
                completed_at=datetime(2026, 4, 26, 12, 5, 0),
            )
        )
        await session.commit()

        download_contract = await waste_analysis_module.get_download(
            job_id="waste-artifact-recoverable",
            request=_request("/api/v1/waste/analysis/waste-artifact-recoverable/download"),
            db=session,
        )

        from src.pdf import builder as pdf_builder

        monkeypatch.setattr(pdf_builder, "generate_waste_pdf", lambda payload: b"pdf-fallback")
        response = await waste_analysis_module.download_file(
            job_id="waste-artifact-recoverable",
            request=_request("/api/v1/waste/analysis/waste-artifact-recoverable/file"),
            db=session,
        )
        body = b"".join([chunk async for chunk in response.body_iterator])

    assert download_contract.download_ready is True
    assert download_contract.artifact_ready is False
    assert download_contract.result_ready is True
    assert response.headers["Content-Disposition"] == "attachment; filename=waste_report_waste-artifact-recoverable.pdf"
    assert body == b"pdf-fallback"


@pytest.mark.asyncio
async def test_startup_requeue_requeues_stale_running_jobs_and_preserves_terminal_jobs(monkeypatch):
    from src.queue import InMemoryWasteQueue

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        session.add_all(
            [
                WasteAnalysisJob(
                    id="stale-running",
                    tenant_id="SH00000001",
                    job_name="Stale running",
                    scope=WasteScope.selected,
                    device_ids=["AD00000001"],
                    start_date=date(2026, 4, 1),
                    end_date=date(2026, 4, 2),
                    granularity=WasteGranularity.daily,
                    status=WasteStatus.running,
                    progress_pct=50,
                    stage="Running",
                    retry_count=0,
                    created_at=datetime(2026, 4, 20, 8, 0, 0),
                ),
                WasteAnalysisJob(
                    id="terminal-completed",
                    tenant_id="SH00000001",
                    job_name="Completed",
                    scope=WasteScope.selected,
                    device_ids=["AD00000002"],
                    start_date=date(2026, 4, 1),
                    end_date=date(2026, 4, 2),
                    granularity=WasteGranularity.daily,
                    status=WasteStatus.completed,
                    progress_pct=100,
                    stage="Complete ✓",
                    retry_count=0,
                    created_at=datetime(2026, 4, 20, 8, 0, 0),
                    completed_at=datetime(2026, 4, 20, 8, 10, 0),
                ),
            ]
        )
        await session.commit()

    fake_queue = InMemoryWasteQueue()
    monkeypatch.setattr(waste_main_module, "engine", engine)
    monkeypatch.setattr(
        "src.queue.get_waste_queue",
        lambda: fake_queue,
    )
    monkeypatch.setattr(settings_module, "WASTE_JOB_MAX_RETRIES", 3)
    await waste_main_module.requeue_stale_waste_jobs_on_startup()

    async with factory() as session:
        running = await session.get(WasteAnalysisJob, "stale-running")
        completed = await session.get(WasteAnalysisJob, "terminal-completed")

    assert running is not None
    assert running.status == WasteStatus.pending
    assert running.error_code == "SERVICE_RESTARTED"
    assert running.retry_count == 1
    assert completed is not None
    assert completed.status == WasteStatus.completed

    await engine.dispose()
