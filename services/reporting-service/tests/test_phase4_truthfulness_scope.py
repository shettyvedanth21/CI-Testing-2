from __future__ import annotations

import os
import sys
from datetime import datetime
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.requests import Request

sys.path.insert(0, "/Users/vedanthshetty/Desktop/GIT-Testing/Shivex-Main")
sys.path.insert(1, "/Users/vedanthshetty/Desktop/GIT-Testing/Shivex-Main/services/reporting-service")
sys.path.insert(2, "/Users/vedanthshetty/Desktop/GIT-Testing/Shivex-Main/services")

os.environ.setdefault("DEVICE_SERVICE_URL", "http://device-service:8001")
os.environ.setdefault("ENERGY_SERVICE_URL", "http://energy-service:8002")
os.environ.setdefault("INFLUXDB_URL", "http://localhost:8086")
os.environ.setdefault("INFLUXDB_TOKEN", "test-token")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from src.handlers import report_common as report_common_module
from src.models.energy_reports import Base as EnergyBase, EnergyReport, ReportStatus, ReportType
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


def _request(path: str, tenant_id: str = "TENANT-A") -> Request:
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
        role="plant_manager",
        plant_ids=["PLANT-1"],
        is_super_admin=False,
    )
    request.state.role = "plant_manager"
    return request


@pytest.mark.asyncio
async def test_failed_report_result_returns_truthful_unavailable_contract(session_factory, monkeypatch):
    monkeypatch.setattr(report_common_module, "_resolve_accessible_device_ids", AsyncMock(return_value=None))

    async with session_factory() as session:
        session.add(
            EnergyReport(
                report_id="report-failed",
                tenant_id="TENANT-A",
                report_type=ReportType.consumption,
                status=ReportStatus.failed,
                params={"resolved_device_ids": ["DEVICE-1"]},
                error_code="REPORT_RUNTIME_FAILED",
                error_message="Worker crashed",
                created_at=datetime(2026, 5, 1, 10, 0, 0),
            )
        )
        await session.commit()

        with pytest.raises(HTTPException) as excinfo:
            await report_common_module.get_report_result(
                report_id="report-failed",
                request=_request("/api/reports/report-failed/result"),
                tenant_id=None,
                db=session,
            )

    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["error"] == "RESULT_UNAVAILABLE"
    assert excinfo.value.detail["status"] == "failed"
    assert excinfo.value.detail["error_code"] == "REPORT_RUNTIME_FAILED"
    assert excinfo.value.detail["error_message"] == "Worker crashed"


@pytest.mark.asyncio
async def test_completed_report_without_artifact_returns_download_not_ready_contract(session_factory, monkeypatch):
    monkeypatch.setattr(report_common_module, "_resolve_accessible_device_ids", AsyncMock(return_value=None))

    coverage_result = {
        "level": "no_coverage",
        "coverage_pct": 0.0,
        "usable_for_business_decisions": False,
        "artifact_generation_allowed": False,
        "terminal_status": "business_blocked",
        "message": "No telemetry is available in the selected time range.",
    }

    async with session_factory() as session:
        session.add(
            EnergyReport(
                report_id="report-no-artifact",
                tenant_id="TENANT-A",
                report_type=ReportType.consumption,
                status=ReportStatus.completed,
                params={"resolved_device_ids": ["DEVICE-1"]},
                result_json={"coverage_result": coverage_result, "summary": {"total_kwh": 0}},
                created_at=datetime(2026, 5, 1, 11, 0, 0),
                completed_at=datetime(2026, 5, 1, 11, 5, 0),
            )
        )
        await session.commit()

        with pytest.raises(HTTPException) as excinfo:
            await report_common_module.download_report(
                report_id="report-no-artifact",
                request=_request("/api/reports/report-no-artifact/download"),
                tenant_id=None,
                db=session,
            )

    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["error"] == "DOWNLOAD_NOT_READY"
    assert excinfo.value.detail["result_ready"] is True
    assert excinfo.value.detail["artifact_ready"] is False
    assert excinfo.value.detail["coverage_result"]["level"] == "no_coverage"


@pytest.mark.asyncio
async def test_completed_no_data_report_result_remains_viewable(session_factory, monkeypatch):
    monkeypatch.setattr(report_common_module, "_resolve_accessible_device_ids", AsyncMock(return_value=None))

    coverage_result = {
        "level": "no_coverage",
        "coverage_pct": 0.0,
        "usable_for_business_decisions": False,
        "artifact_generation_allowed": False,
        "terminal_status": "business_blocked",
        "message": "No telemetry is available in the selected time range.",
    }

    async with session_factory() as session:
        session.add(
            EnergyReport(
                report_id="report-no-data",
                tenant_id="TENANT-A",
                report_type=ReportType.consumption,
                status=ReportStatus.completed,
                params={"resolved_device_ids": ["DEVICE-1"]},
                result_json={"coverage_result": coverage_result, "summary": {"total_kwh": 0}},
                created_at=datetime(2026, 5, 1, 12, 0, 0),
                completed_at=datetime(2026, 5, 1, 12, 5, 0),
            )
        )
        await session.commit()

        payload = await report_common_module.get_report_result(
            report_id="report-no-data",
            request=_request("/api/reports/report-no-data/result"),
            tenant_id=None,
            db=session,
        )

    assert payload["coverage_result"]["level"] == "no_coverage"
    assert payload["summary"]["total_kwh"] == 0


@pytest.mark.asyncio
async def test_result_route_hides_out_of_scope_report(session_factory, monkeypatch):
    monkeypatch.setattr(report_common_module, "_resolve_accessible_device_ids", AsyncMock(return_value=["DEVICE-1"]))

    async with session_factory() as session:
        session.add(
            EnergyReport(
                report_id="report-hidden",
                tenant_id="TENANT-A",
                report_type=ReportType.consumption,
                status=ReportStatus.completed,
                params={"resolved_device_ids": ["DEVICE-2"]},
                result_json={"summary": {"total_kwh": 10}},
                created_at=datetime(2026, 5, 1, 13, 0, 0),
                completed_at=datetime(2026, 5, 1, 13, 5, 0),
            )
        )
        await session.commit()

        with pytest.raises(HTTPException) as excinfo:
            await report_common_module.get_report_result(
                report_id="report-hidden",
                request=_request("/api/reports/report-hidden/result"),
                tenant_id=None,
                db=session,
            )

    assert excinfo.value.status_code == 404
