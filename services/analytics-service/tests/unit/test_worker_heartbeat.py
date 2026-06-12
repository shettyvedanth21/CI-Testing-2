import json
import asyncio

import pytest
from sqlalchemy.exc import OperationalError
from unittest.mock import AsyncMock

from tests._bootstrap import bootstrap_test_imports

bootstrap_test_imports()

from services.shared.job_context import BoundJobPayload
from src.models.schemas import AnalyticsType
from src.workers.job_worker import JobWorker
from src.workers.job_queue import Job


class _DummyQueue:
    async def get_job(self):
        return None


class _DummyOrig(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(code, message)
        self.args = (code, message)


class _LoggerProbe:
    def __init__(self):
        self.info_calls = []
        self.warning_calls = []
        self.error_calls = []

    def info(self, event, **kwargs):
        self.info_calls.append((event, kwargs))

    def warning(self, event, **kwargs):
        self.warning_calls.append((event, kwargs))

    def error(self, event, **kwargs):
        self.error_calls.append((event, kwargs))


class _QueueProbe:
    def __init__(self):
        self.dead_letters = []
        self.acked = []
        self.task_done_count = 0

    async def get_job(self):
        return None

    async def submit_job(self, *args, **kwargs):
        raise AssertionError("timeout test should not requeue when max attempts is one")

    async def dead_letter(self, job, reason):
        self.dead_letters.append((job, reason))

    async def ack_job(self, receipt):
        self.acked.append(receipt)

    def task_done(self):
        self.task_done_count += 1

    def size(self):
        return 0

    def empty(self):
        return True


class _FakeSessionCtx:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeRepo:
    status_updates = []
    queue_updates = []

    def __init__(self, *_args, **_kwargs):
        pass

    async def update_job_queue_metadata(self, **kwargs):
        self.__class__.queue_updates.append(kwargs)

    async def update_job_status(self, **kwargs):
        self.__class__.status_updates.append(kwargs)


class _SlowJobRunner:
    def __init__(self, *_args, **_kwargs):
        pass

    async def run_job(self, *_args, **_kwargs):
        import asyncio

        await asyncio.sleep(3600)


def _disconnect_error() -> OperationalError:
    return OperationalError("SELECT 1", {}, _DummyOrig(2013, "Lost connection to MySQL server during query"))


async def test_worker_heartbeat_retries_disconnect(monkeypatch):
    worker = JobWorker(_DummyQueue())
    logger = _LoggerProbe()
    worker._logger = logger
    worker._running = True

    calls = {"count": 0}

    async def fake_write(now):
        calls["count"] += 1
        if calls["count"] == 1:
            raise _disconnect_error()
        worker._running = False

    reset_calls = {"count": 0}

    async def fake_reset():
        reset_calls["count"] += 1

    async def fake_sleep(_seconds):
        worker._running = False

    monkeypatch.setattr(worker, "_write_worker_heartbeat", fake_write)
    monkeypatch.setattr("src.workers.job_worker.reset_db_connections", fake_reset)
    monkeypatch.setattr("src.workers.job_worker.asyncio.sleep", fake_sleep)

    await worker._worker_heartbeat_loop()

    assert calls["count"] == 2
    assert reset_calls["count"] == 1
    assert ("worker_heartbeat_recovered_after_disconnect", {}) in logger.info_calls
    assert logger.warning_calls == []


async def test_worker_heartbeat_logs_failure_when_retry_still_fails(monkeypatch):
    worker = JobWorker(_DummyQueue())
    logger = _LoggerProbe()
    worker._logger = logger
    worker._running = True

    async def fake_write(_now):
        raise _disconnect_error()

    async def fake_reset():
        return None

    async def fake_sleep(_seconds):
        worker._running = False

    monkeypatch.setattr(worker, "_write_worker_heartbeat", fake_write)
    monkeypatch.setattr("src.workers.job_worker.reset_db_connections", fake_reset)
    monkeypatch.setattr("src.workers.job_worker.asyncio.sleep", fake_sleep)

    await worker._worker_heartbeat_loop()

    assert logger.info_calls
    assert logger.info_calls[0][0] == "worker_heartbeat_waiting_for_db_reconnect"
    assert logger.warning_calls == []


async def test_job_heartbeat_retries_disconnect(monkeypatch):
    worker = JobWorker(_DummyQueue())
    logger = _LoggerProbe()
    worker._logger = logger

    calls = {"count": 0}

    async def fake_write(_job_id, _now):
        calls["count"] += 1
        if calls["count"] == 1:
            raise _disconnect_error()
        worker._running = False

    reset_calls = {"count": 0}

    async def fake_reset():
        reset_calls["count"] += 1

    async def fake_sleep(_seconds):
        return None

    monkeypatch.setattr(worker, "_write_job_heartbeat", fake_write)
    monkeypatch.setattr("src.workers.job_worker.reset_db_connections", fake_reset)
    monkeypatch.setattr("src.workers.job_worker.asyncio.sleep", fake_sleep)
    worker._running = True

    await worker._heartbeat_loop("job-1")

    assert calls["count"] == 2
    assert reset_calls["count"] == 1
    assert ("job_heartbeat_recovered_after_disconnect", {"job_id": "job-1"}) in logger.info_calls
    assert logger.warning_calls == []


async def test_job_heartbeat_logs_reconnect_waiting_when_db_still_unavailable(monkeypatch):
    worker = JobWorker(_DummyQueue())
    logger = _LoggerProbe()
    worker._logger = logger

    async def fake_write(_job_id, _now):
        raise _disconnect_error()

    async def fake_reset():
        return None

    async def fake_sleep(_seconds):
        worker._running = False

    monkeypatch.setattr(worker, "_write_job_heartbeat", fake_write)
    monkeypatch.setattr("src.workers.job_worker.reset_db_connections", fake_reset)
    monkeypatch.setattr("src.workers.job_worker.asyncio.sleep", fake_sleep)
    worker._running = True

    await worker._heartbeat_loop("job-2")

    assert logger.info_calls
    assert logger.info_calls[0][0] == "job_heartbeat_waiting_for_db_reconnect"
    assert logger.info_calls[0][1]["job_id"] == "job-2"
    assert logger.warning_calls == []


async def test_worker_loop_waits_for_db_reconnect_instead_of_logging_error(monkeypatch):
    worker = JobWorker(_DummyQueue())
    logger = _LoggerProbe()
    worker._logger = logger
    worker._running = True

    async def fake_get_job():
        raise OperationalError("SELECT 1", {}, _DummyOrig(2003, "Can't connect to MySQL server"))

    async def fake_reset():
        return None

    async def fake_sleep(_seconds):
        worker._running = False

    worker._queue.get_job = fake_get_job
    async def fake_recover():
        return None

    monkeypatch.setattr(worker, "_recover_stale_running_jobs", fake_recover)
    monkeypatch.setattr("src.workers.job_worker.reset_db_connections", fake_reset)
    monkeypatch.setattr("src.workers.job_worker.asyncio.sleep", fake_sleep)

    await worker.start()

    assert logger.info_calls
    assert logger.info_calls[-1][0] == "worker_waiting_for_db_reconnect"
    assert logger.error_calls == []


async def test_worker_startup_recovery_waits_for_db_reconnect(monkeypatch):
    worker = JobWorker(_DummyQueue())
    logger = _LoggerProbe()
    worker._logger = logger
    worker._running = True

    async def fake_recover():
        raise _disconnect_error()

    async def fake_reset():
        return None

    async def fake_get_job():
        worker._running = False
        return None

    async def fake_sleep(_seconds):
        worker._running = False

    monkeypatch.setattr(worker, "_recover_stale_running_jobs", fake_recover)
    monkeypatch.setattr("src.workers.job_worker.reset_db_connections", fake_reset)
    monkeypatch.setattr("src.workers.job_worker.asyncio.sleep", fake_sleep)
    worker._queue.get_job = fake_get_job

    await worker.start()

    assert logger.info_calls
    assert any(event == "worker_startup_waiting_for_db_reconnect" for event, _kwargs in logger.info_calls)


async def test_worker_timeout_marks_job_failed_and_dead_letters(monkeypatch):
    queue = _QueueProbe()
    worker = JobWorker(queue)
    worker._job_timeout_seconds = 1
    worker._heartbeat_seconds = 3600
    worker._max_attempts = 1
    _FakeRepo.status_updates = []
    _FakeRepo.queue_updates = []

    payload = BoundJobPayload(
        job_type="analytics",
        tenant_id="ORG-A",
        device_id="D1",
        initiated_by_user_id="tester",
        initiated_by_role="org_admin",
        payload={
            "device_id": "D1",
            "analysis_type": AnalyticsType.ANOMALY.value,
            "model_name": "anomaly_ensemble",
            "start_time": "2026-04-01T00:00:00Z",
            "end_time": "2026-04-02T00:00:00Z",
        },
    )
    job = Job(job_id="timeout-job", raw_payload=json.dumps(payload.__dict__), attempt=1, receipt="r1")

    monkeypatch.setattr("src.workers.job_worker.async_session_maker", lambda: _FakeSessionCtx())
    monkeypatch.setattr("src.workers.job_worker.MySQLResultRepository", _FakeRepo)
    monkeypatch.setattr("src.workers.job_worker.JobRunner", _SlowJobRunner)
    monkeypatch.setattr(worker, "_try_claim_job_start", AsyncMock(return_value=True))

    await worker._process_job(job)

    assert queue.task_done_count == 1
    assert len(queue.dead_letters) == 1
    assert "exceeded timeout" in queue.dead_letters[0][1]
    final_status = _FakeRepo.status_updates[-1]
    assert final_status["job_id"] == "timeout-job"
    assert final_status["status"].value == "failed"
    assert final_status["message"] == "Analytics job timed out before completion."
    assert _FakeRepo.queue_updates[-1]["error_code"] == "JOB_EXECUTION_TIMEOUT"


async def test_worker_processing_respects_bounded_concurrency(monkeypatch):
    worker = JobWorker(_DummyQueue(), max_concurrent=2)
    worker._semaphore = asyncio.Semaphore(worker._max_concurrent)

    inflight = {"current": 0, "max_seen": 0}

    async def fake_process(_job):
        inflight["current"] += 1
        inflight["max_seen"] = max(inflight["max_seen"], inflight["current"])
        await asyncio.sleep(0.01)
        inflight["current"] -= 1

    monkeypatch.setattr(worker, "_process_job", fake_process)

    jobs = [Job(job_id=f"job-{idx}", raw_payload="{}", attempt=1) for idx in range(5)]
    await asyncio.gather(*(worker._process_job_with_semaphore(job) for job in jobs))

    assert inflight["max_seen"] == 2
