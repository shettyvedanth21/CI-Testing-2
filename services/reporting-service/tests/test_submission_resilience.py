from __future__ import annotations

import os
import sys
from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


SERVICE_ROOT = "/Users/vedanthshetty/Desktop/GIT-Testing/Shivex-Main/services/reporting-service"
SERVICES_ROOT = "/Users/vedanthshetty/Desktop/GIT-Testing/Shivex-Main/services"
if SERVICE_ROOT not in sys.path:
    sys.path.insert(0, SERVICE_ROOT)
if SERVICES_ROOT not in sys.path:
    sys.path.insert(1, SERVICES_ROOT)

os.environ.setdefault("DEVICE_SERVICE_URL", "http://device-service:8001")
os.environ.setdefault("ENERGY_SERVICE_URL", "http://energy-service:8002")
os.environ.setdefault("INFLUXDB_URL", "http://localhost:8086")
os.environ.setdefault("INFLUXDB_TOKEN", "test-token")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "memory://")

from src.handlers import comparison_reports as comparison_reports_module
from src.handlers import energy_reports as energy_reports_module
from src.models.energy_reports import Base as EnergyBase, EnergyReport, ReportStatus
from src.queue.report_queue import InMemoryReportQueue
from src.repositories.report_repository import ReportRepository
from src.schemas.requests import ComparisonReportRequest, ConsumptionReportRequest
from services.shared.tenant_context import TenantContext
from starlette.requests import Request


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


def _request(path: str) -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": [(b"x-tenant-id", b"SH00000001")],
        "query_string": b"",
    }
    request = Request(scope)
    request.state.tenant_context = TenantContext(
        tenant_id="SH00000001",
        user_id="user-1",
        role="org_admin",
        plant_ids=[],
        is_super_admin=False,
    )
    return request


@pytest.mark.asyncio
async def test_consumption_submission_enqueues_only_once(session_factory, monkeypatch: pytest.MonkeyPatch):
    queue = InMemoryReportQueue()

    async def _fake_validate(device_id: str, ctx):
        return {"device_id": device_id}

    async def _active_workers(_db):
        return 1

    monkeypatch.setattr(energy_reports_module, "get_report_queue", lambda: queue)
    monkeypatch.setattr(energy_reports_module, "validate_device_for_reporting", _fake_validate)
    monkeypatch.setattr(energy_reports_module, "count_active_workers", _active_workers)

    async with session_factory() as session:
        response = await energy_reports_module.create_energy_consumption_report(
            request=_request("/api/reports/energy/consumption"),
            body=ConsumptionReportRequest(
                start_date=date(2026, 4, 1),
                end_date=date(2026, 4, 2),
                device_id="DEVICE-1",
                tenant_id="SH00000001",
            ),
            db=session,
        )

    metrics = await queue.metrics()

    assert response.status == "pending"
    assert metrics["queue_depth"] == 1


@pytest.mark.asyncio
async def test_comparison_submission_reuses_active_duplicate(session_factory, monkeypatch: pytest.MonkeyPatch):
    queue = InMemoryReportQueue()

    async def _fake_validate(device_id: str, ctx):
        return {"device_id": device_id}

    async def _active_workers(_db):
        return 1

    monkeypatch.setattr(comparison_reports_module, "get_report_queue", lambda: queue)
    monkeypatch.setattr(energy_reports_module, "get_report_queue", lambda: queue)
    monkeypatch.setattr(comparison_reports_module, "validate_device_for_reporting", _fake_validate)
    monkeypatch.setattr(energy_reports_module, "count_active_workers", _active_workers)

    body = ComparisonReportRequest(
        comparison_type="machine_vs_machine",
        tenant_id="SH00000001",
        machine_a_id="DEVICE-A",
        machine_b_id="DEVICE-B",
        start_date=date(2026, 4, 1),
        end_date=date(2026, 4, 2),
    )

    async with session_factory() as session:
        first = await comparison_reports_module.create_comparison_report(
            request=_request("/api/reports/comparison"),
            body=body,
            db=session,
        )
        second = await comparison_reports_module.create_comparison_report(
            request=_request("/api/reports/comparison"),
            body=body,
            db=session,
        )
        repo = ReportRepository(session)
        reports = await repo.list_reports(tenant_id="SH00000001", limit=10, offset=0)

    metrics = await queue.metrics()

    assert first.report_id == second.report_id
    assert len(reports) == 1
    assert metrics["queue_depth"] == 1


@pytest.mark.asyncio
async def test_comparison_enqueue_failure_marks_report_for_recovery(session_factory, monkeypatch: pytest.MonkeyPatch):
    class FailingQueue:
        async def enqueue(self, job):
            raise RuntimeError("Redis connection refused")

        async def metrics(self):
            return {"queue_depth": 0, "pending_messages": 0, "dead_letter_count": 0}

    async def _fake_validate(device_id: str, ctx):
        return {"device_id": device_id}

    async def _active_workers(_db):
        return 1

    failing_queue = FailingQueue()
    monkeypatch.setattr(comparison_reports_module, "get_report_queue", lambda: failing_queue)
    monkeypatch.setattr(energy_reports_module, "get_report_queue", lambda: failing_queue)
    monkeypatch.setattr(comparison_reports_module, "validate_device_for_reporting", _fake_validate)
    monkeypatch.setattr(energy_reports_module, "count_active_workers", _active_workers)

    async with session_factory() as session:
        with pytest.raises(Exception) as excinfo:
            await comparison_reports_module.create_comparison_report(
                request=_request("/api/reports/comparison"),
                body=ComparisonReportRequest(
                    comparison_type="machine_vs_machine",
                    tenant_id="SH00000001",
                    machine_a_id="DEVICE-A",
                    machine_b_id="DEVICE-B",
                    start_date=date(2026, 4, 1),
                    end_date=date(2026, 4, 2),
                ),
                db=session,
            )
        assert excinfo.value.status_code == 503
        assert excinfo.value.detail["code"] == "ENQUEUE_FAILED"

    async with session_factory() as session:
        result = await session.execute(
            select(EnergyReport).where(EnergyReport.status == ReportStatus.enqueue_failed)
        )
        failed_report = result.scalar_one_or_none()

    assert failed_report is not None
    assert failed_report.error_code == "ENQUEUE_FAILED"
