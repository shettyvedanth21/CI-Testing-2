from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

BASE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
SERVICES_DIR = REPO_ROOT / "services"
for path in (REPO_ROOT, SERVICES_DIR, BASE_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.environ.setdefault("DEVICE_SERVICE_URL", "http://device-service:8001")
os.environ.setdefault("INFLUXDB_URL", "http://localhost:8086")
os.environ.setdefault("INFLUXDB_TOKEN", "test-token")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from src.models.energy_reports import Base as EnergyBase, EnergyReport, ReportStatus, ReportType
from src.models.scheduled_reports import Base as ScheduledBase, ScheduledFrequency, ScheduledReport, ScheduledReportType
from src.repositories.report_repository import ReportRepository
from src.repositories.scheduled_repository import ScheduledRepository
from src.services.report_scope import normalize_schedule_params_template
from services.shared.tenant_context import TenantContext


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(EnergyBase.metadata.create_all)
        await conn.run_sync(ScheduledBase.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        yield factory
    finally:
        await engine.dispose()


def _ctx() -> TenantContext:
    return TenantContext(
        tenant_id="TENANT-A",
        user_id="pm-1",
        role="plant_manager",
        plant_ids=["PLANT-1"],
        is_super_admin=False,
    )


@pytest.mark.asyncio
async def test_report_repository_filters_history_to_accessible_devices(session_factory):
    async with session_factory() as session:
        session.add_all(
            [
                EnergyReport(
                    report_id="report-p1",
                    tenant_id="TENANT-A",
                    report_type=ReportType.consumption,
                    status=ReportStatus.completed,
                    params={"resolved_device_ids": ["P1"]},
                    created_at=datetime.utcnow(),
                ),
                EnergyReport(
                    report_id="report-p2",
                    tenant_id="TENANT-A",
                    report_type=ReportType.consumption,
                    status=ReportStatus.completed,
                    params={"resolved_device_ids": ["P2"]},
                    created_at=datetime.utcnow(),
                ),
                EnergyReport(
                    report_id="report-all-legacy",
                    tenant_id="TENANT-A",
                    report_type=ReportType.consumption,
                    status=ReportStatus.completed,
                    params={"device_id": "ALL"},
                    created_at=datetime.utcnow(),
                ),
                EnergyReport(
                    report_id="report-compare",
                    tenant_id="TENANT-A",
                    report_type=ReportType.comparison,
                    status=ReportStatus.completed,
                    params={"machine_a_id": "P1", "machine_b_id": "P3"},
                    created_at=datetime.utcnow(),
                ),
            ]
        )
        await session.commit()

        repo = ReportRepository(session, ctx=_ctx())
        reports = await repo.list_reports(accessible_device_ids=["P1", "P3"], limit=10, offset=0)
        visible = await repo.get_report("report-compare", accessible_device_ids=["P1", "P3"])
        hidden = await repo.get_report("report-p2", accessible_device_ids=["P1", "P3"])
        legacy_hidden = await repo.get_report("report-all-legacy", accessible_device_ids=["P1", "P3"])

    assert [report.report_id for report in reports] == ["report-compare", "report-p1"]
    assert visible is not None
    assert hidden is None
    assert legacy_hidden is None


@pytest.mark.asyncio
async def test_report_repository_applies_sql_limit_when_scope_filter_is_not_needed(session_factory):
    async with session_factory() as session:
        session.add_all(
            [
                EnergyReport(
                    report_id="report-older",
                    tenant_id="TENANT-A",
                    report_type=ReportType.consumption,
                    status=ReportStatus.completed,
                    params={"resolved_device_ids": ["P1"]},
                    created_at=datetime(2026, 4, 20, 10, 0, 0),
                ),
                EnergyReport(
                    report_id="report-middle",
                    tenant_id="TENANT-A",
                    report_type=ReportType.consumption,
                    status=ReportStatus.completed,
                    params={"resolved_device_ids": ["P1"]},
                    created_at=datetime(2026, 4, 21, 10, 0, 0),
                ),
                EnergyReport(
                    report_id="report-newest",
                    tenant_id="TENANT-A",
                    report_type=ReportType.consumption,
                    status=ReportStatus.completed,
                    params={"resolved_device_ids": ["P1"]},
                    created_at=datetime(2026, 4, 22, 10, 0, 0),
                ),
            ]
        )
        await session.commit()

        repo = ReportRepository(session, ctx=_ctx())
        first_page = await repo.list_reports(limit=2, offset=0, accessible_device_ids=None)
        second_page = await repo.list_reports(limit=1, offset=1, accessible_device_ids=None)

    assert [report.report_id for report in first_page] == ["report-newest", "report-middle"]
    assert [report.report_id for report in second_page] == ["report-middle"]


@pytest.mark.asyncio
async def test_scheduled_repository_filters_to_accessible_devices(session_factory):
    async with session_factory() as session:
        session.add_all(
            [
                ScheduledReport(
                    schedule_id="sched-p1",
                    tenant_id="TENANT-A",
                    report_type=ScheduledReportType.consumption,
                    frequency=ScheduledFrequency.daily,
                    params_template={"device_ids": ["P1"]},
                    is_active=True,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                ),
                ScheduledReport(
                    schedule_id="sched-p2",
                    tenant_id="TENANT-A",
                    report_type=ScheduledReportType.consumption,
                    frequency=ScheduledFrequency.daily,
                    params_template={"device_ids": ["P2"]},
                    is_active=True,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                ),
            ]
        )
        await session.commit()

        repo = ScheduledRepository(session, ctx=_ctx())
        schedules = await repo.list_schedules(accessible_device_ids=["P1"])
        visible = await repo.get_schedule("sched-p1", accessible_device_ids=["P1"])
        hidden = await repo.get_schedule("sched-p2", accessible_device_ids=["P1"])

    assert [schedule.schedule_id for schedule in schedules] == ["sched-p1"]
    assert visible is not None
    assert hidden is None


def test_normalize_schedule_params_template_rejects_out_of_scope_devices():
    with pytest.raises(PermissionError, match="only devices from your assigned plants"):
        normalize_schedule_params_template({"device_ids": ["P1", "P2"]}, ["P1"])


def test_normalize_schedule_params_template_deduplicates_and_preserves_scope():
    normalized = normalize_schedule_params_template({"device_ids": ["P1", "P1", "P3"]}, ["P1", "P3"])

    assert normalized["device_ids"] == ["P1", "P3"]


@pytest.mark.asyncio
async def test_update_report_uses_unique_report_id_without_tenant_secondary_lock(session_factory):
    async with session_factory() as session:
        session.add(
            EnergyReport(
                report_id="report-update-target",
                tenant_id="TENANT-A",
                report_type=ReportType.consumption,
                status=ReportStatus.pending,
                params={"resolved_device_ids": ["P1"]},
                created_at=datetime.utcnow(),
            )
        )
        await session.commit()

        captured = {}
        original_execute = session.execute

        async def _capture(statement, *args, **kwargs):
            captured["sql"] = str(
                statement.compile(
                    dialect=session.bind.dialect,
                    compile_kwargs={"literal_binds": True},
                )
            )
            return await original_execute(statement, *args, **kwargs)

        session.execute = _capture  # type: ignore[method-assign]

        repo = ReportRepository(session, ctx=_ctx())
        await repo.update_report("report-update-target", progress=42)

    sql = captured["sql"]
    assert "WHERE energy_reports.report_id = 'report-update-target'" in sql
    assert "tenant_id" not in sql


@pytest.mark.asyncio
async def test_update_schedule_is_scoped_to_current_tenant(session_factory):
    async with session_factory() as session:
        session.add(
            ScheduledReport(
                schedule_id="sched-foreign",
                tenant_id="TENANT-B",
                report_type=ScheduledReportType.consumption,
                frequency=ScheduledFrequency.daily,
                params_template={"device_ids": ["P9"]},
                is_active=True,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
        )
        await session.commit()

        repo = ScheduledRepository(session, ctx=_ctx())
        await repo.update_schedule("sched-foreign", is_active=False, params_template={"device_ids": ["P1"]})

        visible = await repo.get_schedule("sched-foreign")

    assert visible is None

    async with session_factory() as session:
        foreign_repo = ScheduledRepository(
            session,
            ctx=TenantContext(
                tenant_id="TENANT-B",
                user_id="pm-2",
                role="plant_manager",
                plant_ids=["PLANT-9"],
                is_super_admin=False,
            ),
        )
        foreign_schedule = await foreign_repo.get_schedule("sched-foreign", accessible_device_ids=["P9"])

    assert foreign_schedule is not None
    assert foreign_schedule.is_active is True
    assert foreign_schedule.params_template == {"device_ids": ["P9"]}
