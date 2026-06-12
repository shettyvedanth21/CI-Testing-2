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
from src.models import Base, WasteAnalysisJob, WasteGranularity, WasteScope, WasteStatus
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
        role="org_admin",
        plant_ids=[],
        is_super_admin=False,
    )
    request.state.role = "org_admin"
    return request


@pytest.mark.asyncio
async def test_completed_no_data_waste_job_exposes_truthful_status_and_result(session_factory):
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
            WasteAnalysisJob(
                id="waste-no-data",
                tenant_id="TENANT-A",
                job_name="No data waste job",
                scope=WasteScope.selected,
                device_ids=["DEVICE-1"],
                start_date=date(2026, 5, 1),
                end_date=date(2026, 5, 2),
                granularity=WasteGranularity.daily,
                status=WasteStatus.completed,
                progress_pct=100,
                stage="No Data",
                result_json={
                    "tenant_id": "TENANT-A",
                    "coverage_result": coverage_result,
                    "device_summaries": [],
                    "skipped_devices": [{"device_id": "DEVICE-1"}],
                },
                created_at=datetime(2026, 5, 1, 10, 0, 0),
                completed_at=datetime(2026, 5, 1, 10, 5, 0),
            )
        )
        await session.commit()

        status_payload = await waste_analysis_module.get_status(
            job_id="waste-no-data",
            request=_request("/api/v1/waste/analysis/waste-no-data/status"),
            db=session,
        )
        result_payload = await waste_analysis_module.get_result(
            job_id="waste-no-data",
            request=_request("/api/v1/waste/analysis/waste-no-data/result"),
            db=session,
        )

    assert status_payload.phase == "no_coverage"
    assert status_payload.result_ready is True
    assert status_payload.download_ready is False
    assert status_payload.coverage_result["level"] == "no_coverage"
    assert result_payload["coverage_result"]["level"] == "no_coverage"


@pytest.mark.asyncio
async def test_waste_result_and_download_routes_hide_other_tenant_jobs(session_factory):
    async with session_factory() as session:
        session.add(
            WasteAnalysisJob(
                id="waste-other-tenant",
                tenant_id="TENANT-B",
                job_name="Other tenant",
                scope=WasteScope.selected,
                device_ids=["DEVICE-9"],
                start_date=date(2026, 5, 1),
                end_date=date(2026, 5, 2),
                granularity=WasteGranularity.daily,
                status=WasteStatus.completed,
                progress_pct=100,
                stage="Completed",
                result_json={"tenant_id": "TENANT-B", "device_summaries": [{"device_id": "DEVICE-9"}]},
                s3_key="waste-reports/TENANT-B/job.pdf",
                created_at=datetime(2026, 5, 1, 11, 0, 0),
                completed_at=datetime(2026, 5, 1, 11, 5, 0),
            )
        )
        await session.commit()

        with pytest.raises(HTTPException) as result_exc:
            await waste_analysis_module.get_result(
                job_id="waste-other-tenant",
                request=_request("/api/v1/waste/analysis/waste-other-tenant/result", tenant_id="TENANT-A"),
                db=session,
            )

        with pytest.raises(HTTPException) as download_exc:
            await waste_analysis_module.get_download(
                job_id="waste-other-tenant",
                request=_request("/api/v1/waste/analysis/waste-other-tenant/download", tenant_id="TENANT-A"),
                db=session,
            )

    assert result_exc.value.status_code == 404
    assert download_exc.value.status_code == 404
