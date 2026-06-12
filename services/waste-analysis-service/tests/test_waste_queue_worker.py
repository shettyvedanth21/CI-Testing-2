from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select, update
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
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

SERVICE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
REPO_ROOT = os.path.abspath(os.path.join(SERVICE_ROOT, "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
SERVICES_ROOT = os.path.join(REPO_ROOT, "services")
if SERVICES_ROOT not in sys.path:
    sys.path.insert(0, SERVICES_ROOT)

from src.queue import InMemoryWasteQueue, RedisWasteQueue, WasteJob
from src.models import Base, WasteAnalysisJob, WasteGranularity, WasteScope, WasteStatus, WasteWorkerHeartbeat
from src.repositories.waste_repository import WasteRepository
from src.workers.waste_worker import WasteWorker
from src.config import settings
from services.shared.tenant_context import TenantContext


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


def _redis_waste_queue_with(fake_redis) -> RedisWasteQueue:
    queue = RedisWasteQueue.__new__(RedisWasteQueue)
    queue._redis = fake_redis
    queue._stream = "waste:queue"
    queue._dead_stream = "waste:dead"
    queue._group = "waste-workers"
    queue._consumer = "test-consumer"
    queue._group_ready = True
    return queue


def _request(path: str, tenant_id: str = "SH00000001") -> Request:
    scope = {
        "type": "http",
        "method": "POST",
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


def test_in_memory_queue_enqueue_dequeue_ack():
    queue = InMemoryWasteQueue()
    job = WasteJob(job_id="w1", tenant_id="SH00000001", params_json='{"scope":"all"}')
    asyncio.run(queue.enqueue(job))
    result = asyncio.run(queue.get_job())
    assert result is not None
    assert result.job_id == "w1"
    assert result.tenant_id == "SH00000001"
    asyncio.run(queue.ack(result))


@pytest.mark.asyncio
async def test_redis_waste_queue_timeout_returns_no_job():
    queue = _redis_waste_queue_with(_RedisQueueReadTimeout())

    assert await queue.get_job() is None


@pytest.mark.asyncio
async def test_redis_waste_queue_pending_timeout_returns_no_job():
    queue = _redis_waste_queue_with(_RedisQueuePendingTimeout())

    assert await queue.get_job() is None


def test_in_memory_queue_dead_letter():
    queue = InMemoryWasteQueue()
    job = WasteJob(job_id="w2", tenant_id="SH00000001", params_json='{}')
    asyncio.run(queue.enqueue(job))
    result = asyncio.run(queue.get_job())
    asyncio.run(queue.dead_letter(result, "terminal failure"))
    metrics = asyncio.run(queue.metrics())
    assert metrics["dead_letter_count"] == 1


def test_in_memory_queue_metrics():
    queue = InMemoryWasteQueue()
    asyncio.run(queue.enqueue(WasteJob(job_id="w3", tenant_id="SH00000001", params_json='{}')))
    asyncio.run(queue.enqueue(WasteJob(job_id="w4", tenant_id="SH00000001", params_json='{}')))
    metrics = asyncio.run(queue.metrics())
    assert metrics["queue_depth"] == 2


@pytest.mark.asyncio
async def test_admission_rejects_global_backlog(monkeypatch):
    from src.handlers import waste_analysis as handler

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    fake_queue = InMemoryWasteQueue()
    monkeypatch.setattr(handler, "get_waste_queue", lambda: fake_queue)
    monkeypatch.setattr(settings, "WASTE_QUEUE_REJECT_THRESHOLD", 2)
    monkeypatch.setattr(settings, "WASTE_TENANT_MAX_PENDING_JOBS", 25)

    async with factory() as session:
        ctx = TenantContext(tenant_id="SH00000001", user_id="u1", role="org_admin", plant_ids=[], is_super_admin=False)
        repo = WasteRepository(session, ctx)
        for i in range(2):
            await repo.create_job(
                job_id=f"waste-pending-{i}",
                tenant_id="SH00000001",
                job_name=None,
                scope="all",
                device_ids=None,
                start_date=date(2026, 4, 1),
                end_date=date(2026, 4, 2),
                granularity="daily",
            )

    async with factory() as session:
        repo_check = WasteRepository(session, ctx)
        count_before = await repo_check.count_pending_jobs_global()
        assert count_before >= 2, f"Expected >=2 pending jobs, got {count_before}"

        with pytest.raises(HTTPException) as excinfo:
            await handler.run_analysis(
                app_request=_request("/api/v1/waste/analysis/run"),
                request=handler.WasteAnalysisRunRequest(
                    scope="selected",
                    device_ids=["AD00000001"],
                    start_date=date(2026, 4, 10),
                    end_date=date(2026, 4, 11),
                    granularity="daily",
                ),
                db=session,
            )

        assert excinfo.value.status_code == 503
        assert excinfo.value.detail["code"] == "QUEUE_BACKLOG_FULL"
    await engine.dispose()


@pytest.mark.asyncio
async def test_admission_rejects_tenant_cap(monkeypatch):
    from src.handlers import waste_analysis as handler

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    fake_queue = InMemoryWasteQueue()
    monkeypatch.setattr(handler, "get_waste_queue", lambda: fake_queue)
    monkeypatch.setattr(settings, "WASTE_TENANT_MAX_PENDING_JOBS", 1)
    monkeypatch.setattr(settings, "WASTE_QUEUE_REJECT_THRESHOLD", 5000)

    async with factory() as session:
        ctx = TenantContext(tenant_id="SH00000001", user_id="u1", role="org_admin", plant_ids=[], is_super_admin=False)
        repo = WasteRepository(session, ctx)
        await repo.create_job(
            job_id="waste-tenant-pending-1",
            tenant_id="SH00000001",
            job_name=None,
            scope="all",
            device_ids=None,
            start_date=date(2026, 4, 1),
            end_date=date(2026, 4, 2),
            granularity="daily",
        )

    async with factory() as session:
        with pytest.raises(HTTPException) as excinfo:
            await handler.run_analysis(
                app_request=_request("/api/v1/waste/analysis/run"),
                request=handler.WasteAnalysisRunRequest(
                    scope="selected",
                    device_ids=["AD00000001"],
                    start_date=date(2026, 4, 10),
                    end_date=date(2026, 4, 11),
                    granularity="daily",
                ),
                db=session,
            )

        assert excinfo.value.status_code == 429
        assert excinfo.value.detail["code"] == "TENANT_QUEUE_CAPACITY_EXCEEDED"
    await engine.dispose()


@pytest.mark.asyncio
async def test_admission_allows_when_under_limits(monkeypatch):
    from src.handlers import waste_analysis as handler
    from src.schemas import WasteAnalysisRunRequest

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    fake_queue = InMemoryWasteQueue()
    monkeypatch.setattr(handler, "get_waste_queue", lambda: fake_queue)
    monkeypatch.setattr(settings, "WASTE_QUEUE_REJECT_THRESHOLD", 5000)
    monkeypatch.setattr(settings, "WASTE_TENANT_MAX_PENDING_JOBS", 25)

    async with factory() as session:
        response = await handler.run_analysis(
            app_request=_request("/api/v1/waste/analysis/run"),
            request=WasteAnalysisRunRequest(
                scope="all",
                start_date=date(2026, 4, 1),
                end_date=date(2026, 4, 2),
                granularity="daily",
            ),
            db=session,
        )

    assert response.status == "pending"
    assert response.job_id
    await engine.dispose()


@pytest.mark.asyncio
async def test_worker_claim_prevents_double_execution(monkeypatch):
    from src.workers import waste_worker as worker_mod
    from src import database as db_mod

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with factory() as session:
        session.add(
            WasteAnalysisJob(
                id="waste-claim-test",
                tenant_id="SH00000001",
                scope=WasteScope.all,
                start_date=date(2026, 4, 1),
                end_date=date(2026, 4, 2),
                granularity=WasteGranularity.daily,
                status=WasteStatus.pending,
                progress_pct=0,
                stage="Queued",
                retry_count=0,
                result_json={"tenant_id": "SH00000001"},
                created_at=datetime.utcnow(),
            )
        )
        await session.commit()

    monkeypatch.setattr(db_mod, "AsyncSessionLocal", factory)
    monkeypatch.setattr(worker_mod, "AsyncSessionLocal", factory)

    worker = WasteWorker(queue=InMemoryWasteQueue(), concurrency=1)
    claimed = await worker._claim_job("waste-claim-test", "worker-slot-1")
    assert claimed is True

    claimed2 = await worker._claim_job("waste-claim-test", "worker-slot-2")
    assert claimed2 is False
    await engine.dispose()


@pytest.mark.asyncio
async def test_worker_retry_or_fail_requeues_on_recoverable_error(monkeypatch):
    from src.workers import waste_worker as worker_mod
    from src import database as db_mod

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    fake_queue = InMemoryWasteQueue()
    worker = WasteWorker(queue=fake_queue, concurrency=1)

    monkeypatch.setattr(settings, "WASTE_JOB_MAX_RETRIES", 3)

    async def _no_sleep(_):
        pass

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(db_mod, "AsyncSessionLocal", factory)
    monkeypatch.setattr(worker_mod, "AsyncSessionLocal", factory)

    async with factory() as session:
        session.add(
            WasteAnalysisJob(
                id="waste-retry-test",
                tenant_id="SH00000001",
                scope=WasteScope.all,
                start_date=date(2026, 4, 1),
                end_date=date(2026, 4, 2),
                granularity=WasteGranularity.daily,
                status=WasteStatus.running,
                progress_pct=50,
                stage="Running",
                retry_count=0,
                result_json={"tenant_id": "SH00000001"},
                created_at=datetime.utcnow(),
            )
        )
        await session.commit()

    job = WasteJob(job_id="waste-retry-test", tenant_id="SH00000001", params_json='{}', attempt=1)
    await worker._retry_or_fail(job, error_code="WORKER_ERROR", error_message="Transient failure")

    metrics = await fake_queue.metrics()
    assert metrics["queue_depth"] == 1

    async with factory() as session:
        updated = await session.get(WasteAnalysisJob, "waste-retry-test")
        assert updated is not None
        assert updated.status == WasteStatus.pending
        assert updated.retry_count == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_worker_max_retries_dead_letters(monkeypatch):
    from src.workers import waste_worker as worker_mod
    from src import database as db_mod

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    fake_queue = InMemoryWasteQueue()
    worker = WasteWorker(queue=fake_queue, concurrency=1)

    monkeypatch.setattr(settings, "WASTE_JOB_MAX_RETRIES", 2)
    monkeypatch.setattr(db_mod, "AsyncSessionLocal", factory)
    monkeypatch.setattr(worker_mod, "AsyncSessionLocal", factory)

    async with factory() as session:
        session.add(
            WasteAnalysisJob(
                id="waste-max-retry-test",
                tenant_id="SH00000001",
                scope=WasteScope.all,
                start_date=date(2026, 4, 1),
                end_date=date(2026, 4, 2),
                granularity=WasteGranularity.daily,
                status=WasteStatus.running,
                progress_pct=50,
                stage="Running",
                retry_count=1,
                result_json={"tenant_id": "SH00000001"},
                created_at=datetime.utcnow(),
            )
        )
        await session.commit()

    job = WasteJob(job_id="waste-max-retry-test", tenant_id="SH00000001", params_json='{}', attempt=2)
    await worker._retry_or_fail(job, error_code="WORKER_ERROR", error_message="Permanent failure")

    metrics = await fake_queue.metrics()
    assert metrics["dead_letter_count"] == 1

    async with factory() as session:
        updated = await session.get(WasteAnalysisJob, "waste-max-retry-test")
        assert updated is not None
        assert updated.status == WasteStatus.failed
        assert updated.retry_count == 2
    await engine.dispose()


@pytest.mark.asyncio
async def test_worker_stale_job_recovery_requeues(monkeypatch):
    from src.workers import waste_worker as worker_mod
    from src import database as db_mod

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    fake_queue = InMemoryWasteQueue()
    worker = WasteWorker(queue=fake_queue, concurrency=1)
    monkeypatch.setattr(settings, "WASTE_JOB_MAX_RETRIES", 3)
    monkeypatch.setattr(db_mod, "AsyncSessionLocal", factory)
    monkeypatch.setattr(worker_mod, "AsyncSessionLocal", factory)

    async with factory() as session:
        session.add(
            WasteAnalysisJob(
                id="waste-stale-recovery",
                tenant_id="SH00000001",
                scope=WasteScope.all,
                start_date=date(2026, 4, 1),
                end_date=date(2026, 4, 2),
                granularity=WasteGranularity.daily,
                status=WasteStatus.running,
                progress_pct=50,
                stage="Running",
                retry_count=0,
                result_json={"tenant_id": "SH00000001"},
                created_at=datetime.utcnow() - timedelta(minutes=15),
                worker_lease_expires_at=datetime.utcnow() - timedelta(minutes=5),
            )
        )
        await session.commit()

    await worker._recover_stale_running_jobs()

    async with factory() as session:
        updated = await session.get(WasteAnalysisJob, "waste-stale-recovery")
        assert updated is not None
        assert updated.status == WasteStatus.pending
        assert updated.retry_count == 1

    metrics = await fake_queue.metrics()
    assert metrics["queue_depth"] == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_count_pending_jobs_for_tenant():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with factory() as session:
        ctx = TenantContext(tenant_id="SH00000001", user_id="u1", role="org_admin", plant_ids=[], is_super_admin=False)
        repo = WasteRepository(session, ctx)
        await repo.create_job(
            job_id="t1",
            tenant_id="SH00000001",
            job_name=None,
            scope="all",
            device_ids=None,
            start_date=date(2026, 4, 1),
            end_date=date(2026, 4, 2),
            granularity="daily",
        )
        count = await repo.count_pending_jobs_for_tenant("SH00000001")
        assert count == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_count_pending_jobs_global():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with factory() as session:
        ctx1 = TenantContext(tenant_id="SH00000001", user_id="u1", role="org_admin", plant_ids=[], is_super_admin=False)
        repo1 = WasteRepository(session, ctx1)
        await repo1.create_job(
            job_id="g1",
            tenant_id="SH00000001",
            job_name=None,
            scope="all",
            device_ids=None,
            start_date=date(2026, 4, 1),
            end_date=date(2026, 4, 2),
            granularity="daily",
        )
        ctx2 = TenantContext(tenant_id="SH00000002", user_id="u2", role="org_admin", plant_ids=[], is_super_admin=False)
        repo2 = WasteRepository(session, ctx2)
        await repo2.create_job(
            job_id="g2",
            tenant_id="SH00000002",
            job_name=None,
            scope="all",
            device_ids=None,
            start_date=date(2026, 4, 1),
            end_date=date(2026, 4, 2),
            granularity="daily",
        )
        count = await repo2.count_pending_jobs_global()
        assert count >= 2
    await engine.dispose()


@pytest.mark.asyncio
async def test_job_submit_enqueues_to_queue(monkeypatch):
    from src.handlers import waste_analysis as handler
    from src.schemas import WasteAnalysisRunRequest

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    fake_queue = InMemoryWasteQueue()
    monkeypatch.setattr(handler, "get_waste_queue", lambda: fake_queue)
    monkeypatch.setattr(settings, "WASTE_QUEUE_REJECT_THRESHOLD", 5000)
    monkeypatch.setattr(settings, "WASTE_TENANT_MAX_PENDING_JOBS", 25)

    async with factory() as session:
        response = await handler.run_analysis(
            app_request=_request("/api/v1/waste/analysis/run"),
            request=WasteAnalysisRunRequest(
                scope="all",
                start_date=date(2026, 4, 1),
                end_date=date(2026, 4, 2),
                granularity="daily",
            ),
            db=session,
        )

    assert response.status == "pending"
    metrics = await fake_queue.metrics()
    assert metrics["queue_depth"] == 1

    queued_job = await fake_queue.get_job()
    assert queued_job is not None
    assert queued_job.job_id == response.job_id
    assert queued_job.tenant_id == "SH00000001"
    await engine.dispose()


@pytest.mark.asyncio
async def test_claim_job_reclaims_stale_running_row_with_expired_lease(monkeypatch):
    from src.workers import waste_worker as worker_mod
    from src import database as db_mod

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with factory() as session:
        session.add(
            WasteAnalysisJob(
                id="waste-stale-claim",
                tenant_id="SH00000001",
                scope=WasteScope.all,
                start_date=date(2026, 4, 1),
                end_date=date(2026, 4, 2),
                granularity=WasteGranularity.daily,
                status=WasteStatus.running,
                progress_pct=50,
                stage="Running",
                retry_count=0,
                worker_id="old-worker-slot-0",
                worker_lease_expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
                result_json={"tenant_id": "SH00000001"},
                created_at=datetime.utcnow(),
            )
        )
        await session.commit()

    monkeypatch.setattr(db_mod, "AsyncSessionLocal", factory)
    monkeypatch.setattr(worker_mod, "AsyncSessionLocal", factory)

    worker = WasteWorker(queue=InMemoryWasteQueue(), concurrency=1)
    claimed = await worker._claim_job("waste-stale-claim", "new-worker-slot-0")
    assert claimed is True

    async with factory() as session:
        updated = await session.get(WasteAnalysisJob, "waste-stale-claim")
        assert updated is not None
        assert updated.status == WasteStatus.running
        assert updated.worker_id == "new-worker-slot-0"
    await engine.dispose()


@pytest.mark.asyncio
async def test_claim_job_refuses_running_row_with_valid_lease(monkeypatch):
    from src.workers import waste_worker as worker_mod
    from src import database as db_mod

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with factory() as session:
        session.add(
            WasteAnalysisJob(
                id="waste-active-lease",
                tenant_id="SH00000001",
                scope=WasteScope.all,
                start_date=date(2026, 4, 1),
                end_date=date(2026, 4, 2),
                granularity=WasteGranularity.daily,
                status=WasteStatus.running,
                progress_pct=50,
                stage="Running",
                retry_count=0,
                worker_id="active-worker-slot-0",
                worker_lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
                result_json={"tenant_id": "SH00000001"},
                created_at=datetime.utcnow(),
            )
        )
        await session.commit()

    monkeypatch.setattr(db_mod, "AsyncSessionLocal", factory)
    monkeypatch.setattr(worker_mod, "AsyncSessionLocal", factory)

    worker = WasteWorker(queue=InMemoryWasteQueue(), concurrency=1)
    claimed = await worker._claim_job("waste-active-lease", "other-worker-slot-0")
    assert claimed is False

    async with factory() as session:
        updated = await session.get(WasteAnalysisJob, "waste-active-lease")
        assert updated is not None
        assert updated.worker_id == "active-worker-slot-0"
    await engine.dispose()


@pytest.mark.asyncio
async def test_retry_enqueue_before_ack_prevents_silent_loss(monkeypatch):
    from src.workers import waste_worker as worker_mod
    from src import database as db_mod

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    fake_queue = InMemoryWasteQueue()
    worker = WasteWorker(queue=fake_queue, concurrency=1)
    monkeypatch.setattr(settings, "WASTE_JOB_MAX_RETRIES", 3)

    async def _no_sleep(_):
        pass

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(db_mod, "AsyncSessionLocal", factory)
    monkeypatch.setattr(worker_mod, "AsyncSessionLocal", factory)

    async with factory() as session:
        session.add(
            WasteAnalysisJob(
                id="waste-retry-order",
                tenant_id="SH00000001",
                scope=WasteScope.all,
                start_date=date(2026, 4, 1),
                end_date=date(2026, 4, 2),
                granularity=WasteGranularity.daily,
                status=WasteStatus.running,
                progress_pct=50,
                stage="Running",
                retry_count=0,
                result_json={"tenant_id": "SH00000001"},
                created_at=datetime.utcnow(),
            )
        )
        await session.commit()

    job = WasteJob(job_id="waste-retry-order", tenant_id="SH00000001", params_json='{}', attempt=1)
    await worker._retry_or_fail(job, error_code="WORKER_ERROR", error_message="Transient failure")

    metrics = await fake_queue.metrics()
    assert metrics["queue_depth"] == 1

    dequeued = await fake_queue.get_job()
    assert dequeued is not None
    assert dequeued.job_id == "waste-retry-order"
    assert dequeued.attempt == 2

    async with factory() as session:
        updated = await session.get(WasteAnalysisJob, "waste-retry-order")
        assert updated is not None
        assert updated.status == WasteStatus.pending
        assert updated.retry_count == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_enqueue_failure_marks_job_enqueue_failed(monkeypatch):
    from src.handlers import waste_analysis as handler
    from src.schemas import WasteAnalysisRunRequest

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    class FailingQueue:
        async def enqueue(self, job):
            raise RuntimeError("Redis connection refused")

        async def metrics(self):
            return {"queue_depth": 0, "pending_messages": 0, "dead_letter_count": 0}

    failing_queue = FailingQueue()
    monkeypatch.setattr(handler, "get_waste_queue", lambda: failing_queue)
    monkeypatch.setattr(settings, "WASTE_QUEUE_REJECT_THRESHOLD", 5000)
    monkeypatch.setattr(settings, "WASTE_TENANT_MAX_PENDING_JOBS", 25)

    async with factory() as session:
        with pytest.raises(HTTPException) as excinfo:
            await handler.run_analysis(
                app_request=_request("/api/v1/waste/analysis/run"),
                request=WasteAnalysisRunRequest(
                    scope="all",
                    start_date=date(2026, 4, 1),
                    end_date=date(2026, 4, 2),
                    granularity="daily",
                ),
                db=session,
            )
        assert excinfo.value.status_code == 503
        assert excinfo.value.detail["code"] == "ENQUEUE_FAILED"

    async with factory() as session:
        stmt = select(WasteAnalysisJob).where(WasteAnalysisJob.status == WasteStatus.enqueue_failed)
        result = await session.execute(stmt)
        failed_job = result.scalar_one_or_none()
        assert failed_job is not None
        assert failed_job.error_code == "ENQUEUE_FAILED"
    await engine.dispose()


@pytest.mark.asyncio
async def test_enqueue_failed_job_recovered_on_startup(monkeypatch):
    from src.main import requeue_stale_waste_jobs_on_startup
    import src.main as main_mod
    import src.queue as queue_mod

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    fake_queue = InMemoryWasteQueue()

    async with factory() as session:
        session.add(
            WasteAnalysisJob(
                id="waste-enqueue-failed-recovery",
                tenant_id="SH00000001",
                scope=WasteScope.all,
                start_date=date(2026, 4, 1),
                end_date=date(2026, 4, 2),
                granularity=WasteGranularity.daily,
                status=WasteStatus.enqueue_failed,
                progress_pct=0,
                stage="Queued",
                retry_count=0,
                error_code="ENQUEUE_FAILED",
                error_message="Job created but queue enqueue failed",
                result_json={"tenant_id": "SH00000001"},
                created_at=datetime.utcnow() - timedelta(minutes=15),
            )
        )
        await session.commit()

    monkeypatch.setattr(main_mod, "engine", engine)
    monkeypatch.setattr(queue_mod, "get_waste_queue", lambda: fake_queue)
    monkeypatch.setattr(settings, "WASTE_JOB_MAX_RETRIES", 3)

    await requeue_stale_waste_jobs_on_startup()

    async with factory() as session:
        updated = await session.get(WasteAnalysisJob, "waste-enqueue-failed-recovery")
        assert updated is not None
        assert updated.status == WasteStatus.pending
        assert updated.retry_count == 1

    metrics = await fake_queue.metrics()
    assert metrics["queue_depth"] == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_worker_recover_stale_running_jobs_also_recovers_enqueue_failed(monkeypatch):
    from src.workers import waste_worker as worker_mod
    from src import database as db_mod

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    fake_queue = InMemoryWasteQueue()
    worker = WasteWorker(queue=fake_queue, concurrency=1)
    monkeypatch.setattr(settings, "WASTE_JOB_MAX_RETRIES", 3)
    monkeypatch.setattr(db_mod, "AsyncSessionLocal", factory)
    monkeypatch.setattr(worker_mod, "AsyncSessionLocal", factory)

    async with factory() as session:
        session.add(
            WasteAnalysisJob(
                id="waste-enqueue-failed-worker-recovery",
                tenant_id="SH00000001",
                scope=WasteScope.all,
                start_date=date(2026, 4, 1),
                end_date=date(2026, 4, 2),
                granularity=WasteGranularity.daily,
                status=WasteStatus.enqueue_failed,
                progress_pct=0,
                stage="Queued",
                retry_count=0,
                error_code="ENQUEUE_FAILED",
                error_message="Enqueue failed earlier",
                result_json={"tenant_id": "SH00000001"},
                created_at=datetime.utcnow() - timedelta(minutes=15),
            ),
        )
        await session.commit()

    await worker._recover_stale_running_jobs()

    async with factory() as session:
        updated = await session.get(WasteAnalysisJob, "waste-enqueue-failed-worker-recovery")
        assert updated is not None
        assert updated.status == WasteStatus.pending
        assert updated.retry_count == 1

    metrics = await fake_queue.metrics()
    assert metrics["queue_depth"] == 1
    await engine.dispose()
