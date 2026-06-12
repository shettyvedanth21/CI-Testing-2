from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import types

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
os.environ.setdefault("ENERGY_SERVICE_URL", "http://energy-service:8002")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

if "apscheduler.schedulers.asyncio" not in sys.modules:
    apscheduler_module = types.ModuleType("apscheduler")
    schedulers_module = types.ModuleType("apscheduler.schedulers")
    asyncio_module = types.ModuleType("apscheduler.schedulers.asyncio")
    triggers_module = types.ModuleType("apscheduler.triggers")
    interval_module = types.ModuleType("apscheduler.triggers.interval")

    class _DummyScheduler:
        def add_job(self, *args, **kwargs):
            return None

        def shutdown(self):
            return None

    class _DummyIntervalTrigger:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    asyncio_module.AsyncIOScheduler = _DummyScheduler
    interval_module.IntervalTrigger = _DummyIntervalTrigger

    sys.modules["apscheduler"] = apscheduler_module
    sys.modules["apscheduler.schedulers"] = schedulers_module
    sys.modules["apscheduler.schedulers.asyncio"] = asyncio_module
    sys.modules["apscheduler.triggers"] = triggers_module
    sys.modules["apscheduler.triggers.interval"] = interval_module

from src.models.scheduled_reports import Base as ScheduledBase, ScheduledFrequency, ScheduledReport, ScheduledReportType
from src.models.energy_reports import ReportStatus
from src.repositories.scheduled_repository import ScheduledRepository
from src.tasks import scheduler as scheduler_module
from services.shared.tenant_context import TenantContext


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(ScheduledBase.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        yield factory
    finally:
        await engine.dispose()


def _ctx() -> TenantContext:
    return TenantContext(
        tenant_id="SH00000001",
        user_id="svc",
        role="org_admin",
        plant_ids=[],
        is_super_admin=False,
    )


@pytest.mark.asyncio
async def test_claim_due_schedules_prevents_duplicate_claims(session_factory):
    now = datetime.utcnow()
    async with session_factory() as session:
        session.add(
            ScheduledReport(
                schedule_id="sched-1",
                tenant_id="SH00000001",
                report_type=ScheduledReportType.consumption,
                frequency=ScheduledFrequency.daily,
                params_template={"device_ids": ["D1"]},
                is_active=True,
                next_run_at=now - timedelta(minutes=1),
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()

        repo = ScheduledRepository(session, ctx=_ctx())
        first_claim = await repo.claim_due_schedules(now=now, stale_after=timedelta(minutes=15))
        second_claim = await repo.claim_due_schedules(now=now + timedelta(seconds=1), stale_after=timedelta(minutes=15))
        claimed_ids = [schedule.schedule_id for schedule in await repo.list_schedules()]
        claimed_record = await repo.get_schedule("sched-1")

    assert claimed_ids == ["sched-1"]
    assert second_claim == []
    assert claimed_record is not None
    assert claimed_record.last_status == "processing"
    assert claimed_record.processing_started_at == now


@pytest.mark.asyncio
async def test_claim_due_schedules_reclaims_stale_processing_schedule(session_factory):
    now = datetime.utcnow()
    stale_started = now - timedelta(minutes=20)
    async with session_factory() as session:
        session.add(
            ScheduledReport(
                schedule_id="sched-stale",
                tenant_id="SH00000001",
                report_type=ScheduledReportType.consumption,
                frequency=ScheduledFrequency.daily,
                params_template={"device_ids": ["D1"]},
                is_active=True,
                next_run_at=now - timedelta(minutes=2),
                processing_started_at=stale_started,
                last_status="processing",
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()

        repo = ScheduledRepository(session, ctx=_ctx())
        claimed = await repo.claim_due_schedules(now=now, stale_after=timedelta(minutes=15))
        claimed_ids = [schedule.schedule_id for schedule in claimed]
        claimed_started = claimed[0].processing_started_at

    assert claimed_ids == ["sched-stale"]
    assert claimed_started == now


@pytest.mark.asyncio
async def test_process_schedule_failure_increments_retry_once(session_factory, monkeypatch):
    now = datetime.utcnow()
    async with session_factory() as session:
        schedule = ScheduledReport(
            schedule_id="sched-fail",
            tenant_id="SH00000001",
            report_type=ScheduledReportType.consumption,
            frequency=ScheduledFrequency.daily,
            params_template={"device_ids": ["D1"]},
            is_active=True,
            next_run_at=now - timedelta(minutes=1),
            processing_started_at=now,
            last_status="processing",
            retry_count=0,
            created_at=now,
            updated_at=now,
        )
        session.add(schedule)
        await session.commit()

        scheduled_repo = ScheduledRepository(session, ctx=_ctx())
        report_repo = SimpleNamespace(db=session)

        monkeypatch.setattr(scheduler_module, "build_service_tenant_context", lambda tenant_id: _ctx())

        async def _fake_create_report(*args, **kwargs):
            return None

        class _FakeReportRepo:
            def __init__(self, db, ctx=None):
                self.db = db

            async def create_report(self, **kwargs):
                return await _fake_create_report(**kwargs)

            async def get_report(self, report_id, tenant_id=None):
                return None

        class _FakeQueue:
            async def enqueue(self, job):
                raise RuntimeError("synthetic failure")

        monkeypatch.setattr(scheduler_module, "ReportRepository", _FakeReportRepo)
        monkeypatch.setattr(scheduler_module, "get_report_queue", lambda: _FakeQueue())

        await scheduler_module.process_schedule(schedule, scheduled_repo, report_repo)
        refreshed = await scheduled_repo.get_schedule("sched-fail")

    assert refreshed is not None
    assert refreshed.retry_count == 1
    assert refreshed.last_status == "failed"
    assert refreshed.is_active is True
    assert refreshed.processing_started_at is None
    assert refreshed.next_run_at is not None


@pytest.mark.asyncio
async def test_wait_for_report_completion_accepts_enum_status(monkeypatch):
    class _FakeReportRepo:
        def __init__(self, db, ctx=None):
            pass

        async def get_report(self, report_id, tenant_id=None):
            return SimpleNamespace(status=ReportStatus.completed)

    class _FakeSessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(scheduler_module, "AsyncSessionLocal", lambda: _FakeSessionContext())
    monkeypatch.setattr(scheduler_module, "ReportRepository", _FakeReportRepo)
    monkeypatch.setattr(scheduler_module, "build_service_tenant_context", lambda tenant_id: _ctx())

    await scheduler_module.wait_for_report_completion("report-1", "SH00000001", max_wait=2)
