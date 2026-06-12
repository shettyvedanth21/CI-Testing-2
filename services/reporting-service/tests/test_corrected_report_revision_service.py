from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(1, str(ROOT / "services" / "reporting-service"))
sys.path.insert(2, str(ROOT / "services"))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from src.models.energy_reports import Base as EnergyReportBase, EnergyReport, ReportStatus, ReportType  # noqa: E402
from src.models.tenant_tariffs import Base as TenantTariffBase  # noqa: E402
from src.queue import InMemoryReportQueue  # noqa: E402
from src.repositories.report_repository import ReportRepository  # noqa: E402
from src.repositories.tariff_repository import TariffRepository  # noqa: E402
from src.services import report_revision_service as revision_module  # noqa: E402
from src.services.report_revision_service import create_corrected_report_revision  # noqa: E402
from src.services.tenant_scope import build_service_tenant_context  # noqa: E402


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(EnergyReportBase.metadata.create_all)
        await conn.run_sync(TenantTariffBase.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_corrected_report_revision_queues_new_revision_and_preserves_original_artifact(session_factory, monkeypatch):
    queue = InMemoryReportQueue()
    monkeypatch.setattr(revision_module, "get_report_queue", lambda: queue)

    async with session_factory() as session:
        tariff_repo = TariffRepository(session, build_service_tenant_context("SH00000001"))
        await tariff_repo.upsert_tariff(
            tenant_id="SH00000001",
            data={
                "tenant_id": "SH00000001",
                "energy_rate_per_kwh": 6.5,
                "currency": "INR",
                "effective_start_at": datetime(2026, 4, 1, 0, 0, 0),
            },
        )

        report_repo = ReportRepository(session, ctx=build_service_tenant_context("SH00000001"))
        original = await report_repo.create_report(
            report_id="report-original",
            report_type="consumption",
            params={"tenant_id": "SH00000001", "device_id": "DEVICE-1", "start_date": "2026-04-01", "end_date": "2026-04-20"},
            tenant_id="SH00000001",
        )
        await report_repo.update_report(
            "report-original",
            status=ReportStatus.completed,
            progress=100,
            result_json={"report_id": "report-original", "summary": {"total_kwh": 10.2}},
            s3_key="reports/SH00000001/report-original.pdf",
            completed_at=datetime(2026, 4, 21, 12, 0, 0),
        )

        payload = await create_corrected_report_revision(
            db=session,
            report_id="report-original",
            tenant_id="SH00000001",
            revision_reason="corrected_after_reconciliation_apply",
            generated_from_reconciliation_run_id="run-123",
        )

        job = await queue.get_job()
        refreshed_original = await report_repo.get_report("report-original", tenant_id="SH00000001")
        revision = await report_repo.get_report(payload["new_report_id"], tenant_id="SH00000001")

    assert job is not None
    assert job.report_id == payload["new_report_id"]
    assert payload["status"] == "queued"
    assert payload["tariff_version_id"] == 1
    assert refreshed_original is not None
    assert refreshed_original.is_authoritative is True
    assert refreshed_original.s3_key == "reports/SH00000001/report-original.pdf"
    assert refreshed_original.result_json == {"report_id": "report-original", "summary": {"total_kwh": 10.2}}
    assert revision is not None
    assert revision.supersedes_report_id == "report-original"
    assert revision.is_authoritative is False
    assert revision.generated_from_reconciliation_run_id == "run-123"
    assert revision.tariff_version_id == 1
    assert revision.s3_key is None


@pytest.mark.asyncio
async def test_finalize_revision_switches_authoritative_report_without_overwriting_original(session_factory):
    async with session_factory() as session:
        repo = ReportRepository(session, ctx=build_service_tenant_context("SH00000001"))
        await repo.create_report(
            report_id="report-original",
            report_type="consumption",
            params={"tenant_id": "SH00000001", "device_id": "DEVICE-1"},
            tenant_id="SH00000001",
        )
        await repo.update_report(
            "report-original",
            status=ReportStatus.completed,
            progress=100,
            result_json={"summary": {"total_kwh": 10.2}},
            s3_key="reports/SH00000001/report-original.pdf",
            completed_at=datetime.utcnow(),
        )
        revision = await repo.create_revision_report(
            new_report_id="report-revision",
            supersedes_report_id="report-original",
            revision_reason="corrected_after_reconciliation_apply",
            tenant_id="SH00000001",
            generated_from_reconciliation_run_id="run-456",
            tariff_version_id=7,
        )
        await repo.update_report(
            "report-revision",
            status=ReportStatus.completed,
            progress=100,
            result_json={"summary": {"total_kwh": 1.3992}},
            s3_key="reports/SH00000001/report-revision.pdf",
            completed_at=datetime.utcnow(),
        )
        changed = await repo.finalize_revision_report("report-revision", tenant_id="SH00000001")
        original = await repo.get_report("report-original", tenant_id="SH00000001")
        refreshed_revision = await repo.get_report(revision.report_id, tenant_id="SH00000001")

    assert changed is True
    assert original is not None
    assert original.is_authoritative is False
    assert original.superseded_by_report_id == "report-revision"
    assert original.s3_key == "reports/SH00000001/report-original.pdf"
    assert original.result_json == {"summary": {"total_kwh": 10.2}}
    assert refreshed_revision is not None
    assert refreshed_revision.is_authoritative is True
    assert refreshed_revision.s3_key == "reports/SH00000001/report-revision.pdf"
