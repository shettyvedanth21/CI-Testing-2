from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests._bootstrap import bootstrap_test_imports

bootstrap_test_imports()

from src.worker_main import _cleanup_interrupted_jobs


class _FakeResult:
    def __init__(self, rowcount: int):
        self.rowcount = rowcount


class _FakeSession:
    def __init__(self, rows: list[SimpleNamespace]):
        self._rows = rows
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, _statement):
        affected = 0
        for row in self._rows:
            if row.status != "running":
                continue
            row.status = "failed"
            row.error_code = "SERVICE_RESTART"
            row.error_message = "Job was interrupted by a service restart. Please resubmit."
            row.message = "Job was interrupted by a service restart. Please resubmit."
            affected += 1
        return _FakeResult(affected)

    async def commit(self):
        self.committed = True


@pytest.mark.asyncio
async def test_cleanup_interrupted_jobs_keeps_pending_children(monkeypatch):
    rows = [
        SimpleNamespace(
            job_id="fleet-parent-running",
            status="running",
            error_code=None,
            error_message=None,
            message=None,
        ),
        SimpleNamespace(
            job_id="fleet-child-pending",
            status="pending",
            error_code=None,
            error_message=None,
            message=None,
        ),
    ]
    session = _FakeSession(rows)

    monkeypatch.setattr("src.worker_main.async_session_maker", lambda: session)

    cleaned = await _cleanup_interrupted_jobs()

    assert cleaned == 1
    assert session.committed is True
    assert rows[0].status == "failed"
    assert rows[0].error_code == "SERVICE_RESTART"
    assert rows[1].status == "pending"
    assert rows[1].error_code is None
