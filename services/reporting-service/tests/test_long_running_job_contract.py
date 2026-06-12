from __future__ import annotations

import os
import sys
from datetime import date, datetime

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.requests import Request

sys.path.insert(0, "/Users/vedanthshetty/Desktop/GIT-Testing/Shivex-Main/services/reporting-service")
sys.path.insert(1, "/Users/vedanthshetty/Desktop/GIT-Testing/Shivex-Main/services")

os.environ.setdefault("DEVICE_SERVICE_URL", "http://device-service:8001")
os.environ.setdefault("ENERGY_SERVICE_URL", "http://energy-service:8002")
os.environ.setdefault("INFLUXDB_URL", "http://localhost:8086")
os.environ.setdefault("INFLUXDB_TOKEN", "test-token")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "memory://")

from src.handlers import energy_reports as energy_reports_module
from src.handlers import report_common as report_common_module
from src.models.energy_reports import Base as EnergyBase, EnergyReport, ReportStatus, ReportType
from src.queue.report_queue import InMemoryReportQueue
from src.schemas.requests import ConsumptionReportRequest
from services.shared.tenant_context import TenantContext


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(EnergyBase.metadata.create_all)
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
async def test_worker_unavailable_submission_returns_clean_503(session_factory, monkeypatch):
    queue = InMemoryReportQueue()

    async def _fake_validate(device_id: str, ctx):
        return {"device_id": device_id}

    async def _no_workers(_db):
        return 0

    monkeypatch.setattr(energy_reports_module, "get_report_queue", lambda: queue)
    monkeypatch.setattr(energy_reports_module, "validate_device_for_reporting", _fake_validate)
    monkeypatch.setattr(energy_reports_module, "count_active_workers", _no_workers)

    async with session_factory() as session:
        with pytest.raises(HTTPException) as excinfo:
            await energy_reports_module.create_energy_consumption_report(
                body=ConsumptionReportRequest(
                    start_date=date(2026, 4, 1),
                    end_date=date(2026, 4, 2),
                    device_id="DEVICE-1",
                    tenant_id="SH00000001",
                ),
                request=_request("/api/reports/energy/consumption"),
                db=session,
            )

    assert excinfo.value.status_code == 503
    assert excinfo.value.detail["error"] == "WORKER_UNAVAILABLE"


@pytest.mark.asyncio
async def test_status_endpoint_exposes_phase_progress_eta_and_readiness(session_factory, monkeypatch):
    async def _no_scope_filter(_request):
        return None

    monkeypatch.setattr(report_common_module, "_resolve_accessible_device_ids", _no_scope_filter)

    async with session_factory() as session:
        session.add(
            EnergyReport(
                report_id="report-running",
                tenant_id="SH00000001",
                report_type=ReportType.consumption,
                status=ReportStatus.processing,
                params={"resolved_device_ids": ["DEVICE-1"]},
                progress=42,
                phase="loading_data",
                phase_label="Loading telemetry and device scope",
                phase_progress=0.25,
                created_at=datetime(2026, 4, 26, 10, 0, 0),
                enqueued_at=datetime(2026, 4, 26, 10, 0, 0),
                processing_started_at=datetime(2026, 4, 26, 10, 1, 0),
            )
        )
        await session.commit()

        payload = await report_common_module.get_report_status(
            report_id="report-running",
            request=_request("/api/reports/report-running/status"),
            tenant_id=None,
            db=session,
        )

    assert payload["status"] == "running"
    assert payload["phase"] == "loading_data"
    assert payload["phase_label"] == "Loading telemetry and device scope"
    assert payload["phase_progress"] == pytest.approx(0.25)
    assert payload["result_ready"] is False
    assert payload["artifact_ready"] is False
    assert payload["estimated_completion_seconds"] is not None


@pytest.mark.asyncio
async def test_history_endpoint_is_tenant_scoped_and_reports_readiness(session_factory, monkeypatch):
    async def _no_scope_filter(_request):
        return None

    monkeypatch.setattr(report_common_module, "_resolve_accessible_device_ids", _no_scope_filter)

    async with session_factory() as session:
        session.add_all(
            [
                EnergyReport(
                    report_id="report-a-ready",
                    tenant_id="SH00000001",
                    report_type=ReportType.consumption,
                    status=ReportStatus.completed,
                    params={"resolved_device_ids": ["DEVICE-1"]},
                    result_json={
                        "coverage_result": {
                            "level": "partial_coverage",
                            "coverage_pct": 42.86,
                            "usable_for_business_decisions": True,
                            "artifact_generation_allowed": True,
                            "terminal_status": "business_complete",
                            "message": "Telemetry coverage is partial; results are usable with coverage warnings.",
                        }
                    },
                    progress=100,
                    phase="completed",
                    phase_label="Completed",
                    phase_progress=1.0,
                    s3_key="reports/SH00000001/report-a-ready.pdf",
                    created_at=datetime(2026, 4, 26, 9, 0, 0),
                    completed_at=datetime(2026, 4, 26, 9, 5, 0),
                ),
                EnergyReport(
                    report_id="report-b-hidden",
                    tenant_id="SH00000002",
                    report_type=ReportType.consumption,
                    status=ReportStatus.completed,
                    params={"resolved_device_ids": ["DEVICE-2"]},
                    created_at=datetime(2026, 4, 26, 8, 0, 0),
                    completed_at=datetime(2026, 4, 26, 8, 5, 0),
                ),
            ]
        )
        await session.commit()

        payload = await report_common_module.list_reports(
            request=_request("/api/reports/history"),
            tenant_id=None,
            limit=20,
            offset=0,
            report_type=None,
            db=session,
        )

    assert [report["report_id"] for report in payload["reports"]] == ["report-a-ready"]
    assert payload["reports"][0]["result_ready"] is True
    assert payload["reports"][0]["artifact_ready"] is True
    assert payload["reports"][0]["download_url"] == "/api/reports/report-a-ready/download"
    assert payload["reports"][0]["coverage_result"]["level"] == "partial_coverage"


@pytest.mark.asyncio
async def test_result_endpoint_returns_stable_not_ready_contract(session_factory, monkeypatch):
    async def _no_scope_filter(_request):
        return None

    monkeypatch.setattr(report_common_module, "_resolve_accessible_device_ids", _no_scope_filter)

    async with session_factory() as session:
        session.add(
            EnergyReport(
                report_id="report-pending",
                tenant_id="SH00000001",
                report_type=ReportType.consumption,
                status=ReportStatus.pending,
                params={"resolved_device_ids": ["DEVICE-1"]},
                created_at=datetime(2026, 4, 26, 11, 0, 0),
            )
        )
        await session.commit()

        with pytest.raises(HTTPException) as excinfo:
            await report_common_module.get_report_result(
                report_id="report-pending",
                request=_request("/api/reports/report-pending/result"),
                tenant_id=None,
                db=session,
            )

    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["error"] == "RESULT_NOT_READY"
    assert excinfo.value.detail["status"] == "pending"
