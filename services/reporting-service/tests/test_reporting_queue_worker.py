from __future__ import annotations

import os
import sys
import asyncio
from datetime import date, datetime, timedelta
from itertools import count
from types import ModuleType

import pytest
import pytest_asyncio
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
from src.models.energy_reports import Base as EnergyBase, EnergyReport, ReportStatus, ReportType
from src.queue.report_queue import InMemoryReportQueue, RedisReportQueue, ReportJob
from src.repositories.report_repository import ReportRepository
from src.schemas.requests import ConsumptionReportRequest
from src.workers import report_worker as report_worker_module
from src.workers.report_worker import ReportWorker
from services.shared.tenant_context import TenantContext

_CLIENT_COUNTER = count(1)


class _RedisQueueReadTimeout:
    async def xautoclaim(self, *args, **kwargs):
        return [None, []]

    async def xreadgroup(self, *args, **kwargs):
        from redis.exceptions import TimeoutError as RedisTimeoutError

        raise RedisTimeoutError("Timeout reading from redis:6379")


class _RedisQueuePendingTimeout:
    async def xautoclaim(self, *args, **kwargs):
        from redis.exceptions import TimeoutError as RedisTimeoutError

        raise RedisTimeoutError("Timeout reading from redis:6379")


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


def _request(tenant_id: str = "SH00000001") -> Request:
    client_id = next(_CLIENT_COUNTER)
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/reports/energy/consumption",
        "headers": [(b"x-tenant-id", tenant_id.encode("utf-8"))],
        "query_string": b"",
        "client": (f"127.0.0.{client_id}", 5000 + client_id),
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


def _redis_report_queue_with(fake_redis) -> RedisReportQueue:
    queue = RedisReportQueue.__new__(RedisReportQueue)
    queue._redis = fake_redis
    queue._stream = "reports:queue"
    queue._dead_stream = "reports:dead"
    queue._group = "report-workers"
    queue._consumer = "test-consumer"
    queue._group_ready = True
    return queue


@pytest.mark.asyncio
async def test_redis_report_queue_timeout_returns_no_job():
    queue = _redis_report_queue_with(_RedisQueueReadTimeout())

    assert await queue.get_job() is None


@pytest.mark.asyncio
async def test_redis_report_queue_pending_timeout_returns_no_job():
    queue = _redis_report_queue_with(_RedisQueuePendingTimeout())

    assert await queue.get_job() is None


@pytest.mark.asyncio
async def test_submit_api_enqueues_without_inline_execution(session_factory, monkeypatch):
    queue = InMemoryReportQueue()

    async def _fake_validate(device_id: str, ctx):
        return {"device_id": device_id}

    async def _active_workers(_db):
        return 1

    monkeypatch.setattr(energy_reports_module, "get_report_queue", lambda: queue)
    monkeypatch.setattr(energy_reports_module, "validate_device_for_reporting", _fake_validate)
    monkeypatch.setattr(energy_reports_module, "resolve_all_devices", lambda ctx: ["DEVICE-1"])
    monkeypatch.setattr(energy_reports_module, "count_active_workers", _active_workers)

    async with session_factory() as session:
        response = await energy_reports_module.create_energy_consumption_report(
            body=ConsumptionReportRequest(
                start_date=date(2026, 4, 1),
                end_date=date(2026, 4, 2),
                device_id="DEVICE-1",
                tenant_id="SH00000001",
            ),
            request=_request(),
            db=session,
        )
        repo = ReportRepository(session)
        report = await repo.load_report_for_worker(response.report_id, tenant_id="SH00000001")

    job = await queue.get_job()

    assert response.status == "pending"
    assert job is not None
    assert job.report_id == response.report_id
    assert report is not None
    assert report.status == ReportStatus.pending


@pytest.mark.asyncio
async def test_duplicate_submit_reuses_existing_active_report(session_factory, monkeypatch):
    queue = InMemoryReportQueue()

    async def _fake_validate(device_id: str, ctx):
        return {"device_id": device_id}

    async def _active_workers(_db):
        return 1

    monkeypatch.setattr(energy_reports_module, "get_report_queue", lambda: queue)
    monkeypatch.setattr(energy_reports_module, "validate_device_for_reporting", _fake_validate)
    monkeypatch.setattr(energy_reports_module, "count_active_workers", _active_workers)

    async with session_factory() as session:
        request = ConsumptionReportRequest(
            start_date=date(2026, 4, 1),
            end_date=date(2026, 4, 2),
            device_id="DEVICE-1",
            tenant_id="SH00000001",
        )
        first = await energy_reports_module.create_energy_consumption_report(body=request, request=_request(), db=session)
        second = await energy_reports_module.create_energy_consumption_report(body=request, request=_request(), db=session)
        repo = ReportRepository(session)
        reports = await repo.list_reports(tenant_id="SH00000001", limit=10, offset=0)

    assert first.report_id == second.report_id
    assert len(reports) == 1


@pytest.mark.asyncio
async def test_worker_processes_queued_job_and_marks_completed(session_factory, monkeypatch):
    queue = InMemoryReportQueue()
    worker = ReportWorker(queue=queue, concurrency=1)

    async def _fake_execute(report_id: str, report_type: str, params: dict) -> None:
        async with report_worker_module.AsyncSessionLocal() as session:
            repo = ReportRepository(session)
            await repo.update_report(
                report_id,
                status="completed",
                progress=100,
                result_json={"ok": True},
                completed_at=datetime.utcnow(),
            )

    monkeypatch.setattr(report_worker_module, "execute_report", _fake_execute)

    async with session_factory() as session:
        report = EnergyReport(
            report_id="report-queued",
            tenant_id="SH00000001",
            report_type=ReportType.consumption,
            status=ReportStatus.pending,
            params={"tenant_id": "SH00000001"},
            created_at=datetime.utcnow(),
            enqueued_at=datetime.utcnow(),
        )
        session.add(report)
        await session.commit()

    monkeypatch.setattr(report_worker_module, "AsyncSessionLocal", session_factory)
    await queue.enqueue(ReportJob(report_id="report-queued", tenant_id="SH00000001", report_type="consumption"))
    job = await queue.get_job()
    assert job is not None

    await worker._process_job(job, slot=0)

    async with session_factory() as session:
        repo = ReportRepository(session)
        refreshed = await repo.load_report_for_worker("report-queued", tenant_id="SH00000001")

    assert refreshed is not None
    assert refreshed.status == ReportStatus.completed
    assert refreshed.worker_id is None
    assert refreshed.processing_started_at is None


@pytest.mark.asyncio
async def test_worker_timeout_marks_failed_after_retry_budget_exhausted(session_factory, monkeypatch):
    queue = InMemoryReportQueue()
    worker = ReportWorker(queue=queue, concurrency=1)

    async def _slow_execute(report_id: str, report_type: str, params: dict) -> None:
        await asyncio.sleep(1.1)

    monkeypatch.setattr(report_worker_module, "execute_report", _slow_execute)
    monkeypatch.setattr(report_worker_module, "AsyncSessionLocal", session_factory)
    monkeypatch.setattr(report_worker_module.settings, "REPORT_JOB_TIMEOUT_SECONDS", 1)
    monkeypatch.setattr(report_worker_module.settings, "REPORT_JOB_MAX_RETRIES", 1)

    async with session_factory() as session:
        session.add(
            EnergyReport(
                report_id="report-timeout",
                tenant_id="SH00000001",
                report_type=ReportType.consumption,
                status=ReportStatus.pending,
                params={"tenant_id": "SH00000001"},
                created_at=datetime.utcnow(),
                enqueued_at=datetime.utcnow(),
            )
        )
        await session.commit()

    await queue.enqueue(ReportJob(report_id="report-timeout", tenant_id="SH00000001", report_type="consumption"))
    job = await queue.get_job()
    assert job is not None

    await worker._process_job(job, slot=0)

    async with session_factory() as session:
        repo = ReportRepository(session)
        refreshed = await repo.load_report_for_worker("report-timeout", tenant_id="SH00000001")

    assert refreshed is not None
    assert refreshed.status == ReportStatus.failed
    assert refreshed.error_code == "JOB_TIMEOUT"
    assert refreshed.timeout_count == 1


@pytest.mark.asyncio
async def test_stale_processing_report_can_be_reclaimed_by_worker(session_factory, monkeypatch):
    queue = InMemoryReportQueue()
    worker = ReportWorker(queue=queue, concurrency=1)

    async def _fake_execute(report_id: str, report_type: str, params: dict) -> None:
        async with report_worker_module.AsyncSessionLocal() as session:
            repo = ReportRepository(session)
            await repo.update_report(
                report_id,
                status="completed",
                progress=100,
                completed_at=datetime.utcnow(),
            )

    monkeypatch.setattr(report_worker_module, "execute_report", _fake_execute)
    monkeypatch.setattr(report_worker_module, "AsyncSessionLocal", session_factory)
    monkeypatch.setattr(report_worker_module.settings, "REPORT_JOB_TIMEOUT_SECONDS", 5)

    async with session_factory() as session:
        session.add(
            EnergyReport(
                report_id="report-stale",
                tenant_id="SH00000001",
                report_type=ReportType.consumption,
                status=ReportStatus.processing,
                params={"tenant_id": "SH00000001"},
                created_at=datetime.utcnow() - timedelta(minutes=10),
                enqueued_at=datetime.utcnow() - timedelta(minutes=10),
                processing_started_at=datetime.utcnow() - timedelta(minutes=10),
                worker_id="dead-worker",
            )
        )
        await session.commit()

    await worker._process_job(
        ReportJob(report_id="report-stale", tenant_id="SH00000001", report_type="consumption"),
        slot=0,
    )

    async with session_factory() as session:
        repo = ReportRepository(session)
        refreshed = await repo.load_report_for_worker("report-stale", tenant_id="SH00000001")

    assert refreshed is not None
    assert refreshed.status == ReportStatus.completed


@pytest.mark.asyncio
async def test_tenant_active_cap_rejects_when_saturated(session_factory, monkeypatch):
    queue = InMemoryReportQueue()

    async def _fake_validate(device_id: str, ctx):
        return {"device_id": device_id}

    async def _active_workers(_db):
        return 1

    monkeypatch.setattr(energy_reports_module, "get_report_queue", lambda: queue)
    monkeypatch.setattr(energy_reports_module, "validate_device_for_reporting", _fake_validate)
    monkeypatch.setattr(energy_reports_module, "count_active_workers", _active_workers)
    monkeypatch.setattr(energy_reports_module.settings, "REPORT_TENANT_MAX_ACTIVE_JOBS", 2)
    monkeypatch.setattr(energy_reports_module.settings, "REPORT_TENANT_MAX_PENDING_JOBS", 25)
    monkeypatch.setattr(energy_reports_module.settings, "REPORT_QUEUE_REJECT_THRESHOLD", 5000)

    async with session_factory() as session:
        ctx = TenantContext(tenant_id="SH00000001", user_id="u1", role="org_admin", plant_ids=[], is_super_admin=False)
        repo = ReportRepository(session, ctx=ctx)
        for i in range(2):
            await repo.create_report(
                report_id=f"active-report-{i}",
                tenant_id="SH00000001",
                report_type="consumption",
                params={"tenant_id": "SH00000001", "device_id": f"DEV-{i}", "start_date": "2026-04-01", "end_date": "2026-04-02"},
            )
            await repo.update_report(
                f"active-report-{i}",
                tenant_id="SH00000001",
                status="processing",
                processing_started_at=datetime.utcnow(),
                worker_id="worker-slot-0",
            )

    async with session_factory() as session:
        with pytest.raises(Exception) as excinfo:
            await energy_reports_module.create_energy_consumption_report(
                body=ConsumptionReportRequest(
                    start_date=date(2026, 4, 10),
                    end_date=date(2026, 4, 11),
                    device_id="DEVICE-NEW",
                    tenant_id="SH00000001",
                ),
                request=_request(),
                db=session,
            )
        assert excinfo.value.status_code == 429
        assert excinfo.value.detail["error"] == "TENANT_ACTIVE_CAP_EXCEEDED"


@pytest.mark.asyncio
async def test_tenant_active_cap_allows_other_tenants(session_factory, monkeypatch):
    queue = InMemoryReportQueue()

    async def _fake_validate(device_id: str, ctx):
        return {"device_id": device_id}

    async def _active_workers(_db):
        return 1

    monkeypatch.setattr(energy_reports_module, "get_report_queue", lambda: queue)
    monkeypatch.setattr(energy_reports_module, "validate_device_for_reporting", _fake_validate)
    monkeypatch.setattr(energy_reports_module, "count_active_workers", _active_workers)
    monkeypatch.setattr(energy_reports_module.settings, "REPORT_TENANT_MAX_ACTIVE_JOBS", 1)
    monkeypatch.setattr(energy_reports_module.settings, "REPORT_TENANT_MAX_PENDING_JOBS", 25)
    monkeypatch.setattr(energy_reports_module.settings, "REPORT_QUEUE_REJECT_THRESHOLD", 5000)

    async with session_factory() as session:
        ctx_a = TenantContext(tenant_id="SH00000001", user_id="u1", role="org_admin", plant_ids=[], is_super_admin=False)
        repo_a = ReportRepository(session, ctx=ctx_a)
        await repo_a.create_report(
            report_id="active-tenant-a",
            tenant_id="SH00000001",
            report_type="consumption",
            params={"tenant_id": "SH00000001", "device_id": "DEV-A", "start_date": "2026-04-01", "end_date": "2026-04-02"},
        )
        await repo_a.update_report(
            "active-tenant-a",
            tenant_id="SH00000001",
            status="processing",
            processing_started_at=datetime.utcnow(),
            worker_id="worker-slot-0",
        )

    async with session_factory() as session:
        response = await energy_reports_module.create_energy_consumption_report(
            body=ConsumptionReportRequest(
                start_date=date(2026, 4, 10),
                end_date=date(2026, 4, 11),
                device_id="DEVICE-B",
                tenant_id="SH00000002",
            ),
            request=_request(tenant_id="SH00000002"),
            db=session,
        )
    assert response.status == "pending"


@pytest.mark.asyncio
async def test_retry_enqueues_before_ack(session_factory, monkeypatch):
    queue = InMemoryReportQueue()
    worker = ReportWorker(queue=queue, concurrency=1)

    async def _fail_execute(report_id: str, report_type: str, params: dict) -> None:
        raise RuntimeError("Transient failure")

    monkeypatch.setattr(report_worker_module, "execute_report", _fail_execute)
    monkeypatch.setattr(report_worker_module, "AsyncSessionLocal", session_factory)
    monkeypatch.setattr(report_worker_module.settings, "REPORT_JOB_MAX_RETRIES", 3)
    monkeypatch.setattr(report_worker_module.settings, "REPORT_JOB_TIMEOUT_SECONDS", 600)

    async with session_factory() as session:
        session.add(
            EnergyReport(
                report_id="report-retry-order",
                tenant_id="SH00000001",
                report_type=ReportType.consumption,
                status=ReportStatus.pending,
                params={"tenant_id": "SH00000001"},
                created_at=datetime.utcnow(),
                enqueued_at=datetime.utcnow(),
            )
        )
        await session.commit()

    await queue.enqueue(ReportJob(report_id="report-retry-order", tenant_id="SH00000001", report_type="consumption"))
    job = await queue.get_job()
    assert job is not None

    await worker._process_job(job, slot=0)

    metrics = await queue.metrics()
    assert metrics["queue_depth"] == 1

    dequeued = await queue.get_job()
    assert dequeued is not None
    assert dequeued.report_id == "report-retry-order"
    assert dequeued.attempt == 2

    async with session_factory() as session:
        repo = ReportRepository(session)
        refreshed = await repo.load_report_for_worker("report-retry-order", tenant_id="SH00000001")
        assert refreshed is not None
        assert refreshed.status == ReportStatus.pending
        assert refreshed.retry_count == 1


@pytest.mark.asyncio
async def test_enqueue_failure_marks_report_enqueue_failed(session_factory, monkeypatch):
    class FailingQueue:
        async def enqueue(self, job):
            raise RuntimeError("Redis connection refused")

        async def metrics(self):
            return {"queue_depth": 0, "pending_messages": 0, "dead_letter_count": 0}

    failing_queue = FailingQueue()

    async def _fake_validate(device_id: str, ctx):
        return {"device_id": device_id}

    async def _active_workers(_db):
        return 1

    monkeypatch.setattr(energy_reports_module, "get_report_queue", lambda: failing_queue)
    monkeypatch.setattr(energy_reports_module, "validate_device_for_reporting", _fake_validate)
    monkeypatch.setattr(energy_reports_module, "count_active_workers", _active_workers)
    monkeypatch.setattr(energy_reports_module.settings, "REPORT_TENANT_MAX_ACTIVE_JOBS", 4)
    monkeypatch.setattr(energy_reports_module.settings, "REPORT_TENANT_MAX_PENDING_JOBS", 25)
    monkeypatch.setattr(energy_reports_module.settings, "REPORT_QUEUE_REJECT_THRESHOLD", 5000)

    async with session_factory() as session:
        with pytest.raises(Exception) as excinfo:
            await energy_reports_module.create_energy_consumption_report(
                body=ConsumptionReportRequest(
                    start_date=date(2026, 4, 1),
                    end_date=date(2026, 4, 2),
                    device_id="DEVICE-1",
                    tenant_id="SH00000001",
                ),
                request=_request(),
                db=session,
            )
        assert excinfo.value.status_code == 503
        assert excinfo.value.detail["code"] == "ENQUEUE_FAILED"

    async with session_factory() as session:
        from sqlalchemy import select
        stmt = select(EnergyReport).where(EnergyReport.status == ReportStatus.enqueue_failed)
        result = await session.execute(stmt)
        failed_report = result.scalar_one_or_none()
        assert failed_report is not None
        assert failed_report.error_code == "ENQUEUE_FAILED"


@pytest.mark.asyncio
async def test_enqueue_failed_report_recovered_on_startup():
    from src.queue import get_report_queue
    import src.queue as queue_mod

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(EnergyBase.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    fake_queue = InMemoryReportQueue()

    async with factory() as session:
        session.add(
            EnergyReport(
                report_id="report-enqueue-failed-1",
                tenant_id="SH00000001",
                report_type=ReportType.consumption,
                status=ReportStatus.enqueue_failed,
                params={"tenant_id": "SH00000001"},
                error_code="ENQUEUE_FAILED",
                error_message="Enqueue failed earlier",
                created_at=datetime.utcnow() - timedelta(minutes=15),
            ),
        )
        await session.commit()

    async def requeue_enqueue_failed(engine, queue):
        from sqlalchemy import text
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT report_id, tenant_id, report_type FROM energy_reports WHERE status = 'enqueue_failed'")
            )
            rows = result.fetchall()
        if not rows:
            return
        for row in rows:
            report_id, tenant_id, report_type = row
            now = datetime.utcnow()
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "UPDATE energy_reports SET status = 'pending', progress = 0, phase = 'queued', "
                        "phase_label = 'Queued', enqueued_at = :now, "
                        "error_code = 'ENQUEUE_RETRY', error_message = 'Requeued after enqueue failure on startup' "
                        "WHERE report_id = :report_id"
                    ),
                    {"now": now, "report_id": report_id},
                )
            await queue.enqueue(
                ReportJob(report_id=report_id, tenant_id=tenant_id, report_type=report_type),
            )

    await requeue_enqueue_failed(engine, fake_queue)

    async with factory() as session:
        from sqlalchemy import select
        stmt = select(EnergyReport).where(EnergyReport.report_id == "report-enqueue-failed-1")
        result = await session.execute(stmt)
        refreshed = result.scalar_one_or_none()
        assert refreshed is not None
        assert refreshed.status == ReportStatus.pending
        assert refreshed.error_code == "ENQUEUE_RETRY"

    metrics = await fake_queue.metrics()
    assert metrics["queue_depth"] == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_enqueue_failed_report_recovery_limits_startup_batch():
    apscheduler_module = ModuleType("apscheduler")
    apscheduler_schedulers_module = ModuleType("apscheduler.schedulers")
    apscheduler_asyncio_module = ModuleType("apscheduler.schedulers.asyncio")
    apscheduler_triggers_module = ModuleType("apscheduler.triggers")
    apscheduler_interval_module = ModuleType("apscheduler.triggers.interval")

    class _DummyScheduler:
        def __init__(self, *args, **kwargs):
            pass

        def add_job(self, *args, **kwargs):
            return None

        def start(self):
            return None

        def shutdown(self, *args, **kwargs):
            return None

    apscheduler_asyncio_module.AsyncIOScheduler = _DummyScheduler
    apscheduler_interval_module.IntervalTrigger = object
    sys.modules.setdefault("apscheduler", apscheduler_module)
    sys.modules.setdefault("apscheduler.schedulers", apscheduler_schedulers_module)
    sys.modules["apscheduler.schedulers.asyncio"] = apscheduler_asyncio_module
    sys.modules.setdefault("apscheduler.triggers", apscheduler_triggers_module)
    sys.modules["apscheduler.triggers.interval"] = apscheduler_interval_module

    from src.main import requeue_enqueue_failed_reports
    import src.main as main_module
    import src.queue as queue_module

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(EnergyBase.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    fake_queue = InMemoryReportQueue()

    async with factory() as session:
        for index in range(60):
            session.add(
                EnergyReport(
                    report_id=f"report-enqueue-failed-{index}",
                    tenant_id="SH00000001",
                    report_type=ReportType.consumption,
                    status=ReportStatus.enqueue_failed,
                    params={"tenant_id": "SH00000001"},
                    error_code="ENQUEUE_FAILED",
                    error_message="Enqueue failed earlier",
                    created_at=datetime.utcnow() - timedelta(minutes=15),
                ),
            )
        await session.commit()

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(main_module, "engine", engine)
    monkeypatch.setattr(queue_module, "get_report_queue", lambda: fake_queue)
    try:
        await requeue_enqueue_failed_reports()
    finally:
        monkeypatch.undo()

    async with factory() as session:
        from sqlalchemy import select

        pending_count = (
            await session.execute(select(EnergyReport).where(EnergyReport.status == ReportStatus.pending))
        ).scalars().all()
        enqueue_failed_count = (
            await session.execute(select(EnergyReport).where(EnergyReport.status == ReportStatus.enqueue_failed))
        ).scalars().all()

    metrics = await fake_queue.metrics()
    assert len(pending_count) == 50
    assert len(enqueue_failed_count) == 10
    assert metrics["queue_depth"] == 50
    await engine.dispose()


@pytest.mark.asyncio
async def test_claim_accepts_enqueue_failed_rows(session_factory):
    async with session_factory() as session:
        repo = ReportRepository(session)
        session.add(
            EnergyReport(
                report_id="report-enq-failed-claim",
                tenant_id="SH00000001",
                report_type=ReportType.consumption,
                status=ReportStatus.enqueue_failed,
                params={"tenant_id": "SH00000001"},
                error_code="ENQUEUE_FAILED",
                created_at=datetime.utcnow(),
                enqueued_at=datetime.utcnow(),
            ),
        )
        await session.commit()

        claimed = await repo.claim_report_for_processing(
            "report-enq-failed-claim",
            worker_id="worker-slot-0",
            stale_after=timedelta(seconds=600),
            tenant_id="SH00000001",
        )
        assert claimed is True

        refreshed = await repo.load_report_for_worker("report-enq-failed-claim", tenant_id="SH00000001")
        assert refreshed is not None
        assert refreshed.status == ReportStatus.processing
        assert refreshed.worker_id == "worker-slot-0"
