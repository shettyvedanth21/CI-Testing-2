from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace

import pytest
import types
from sqlalchemy.dialects import mysql

from tests._bootstrap import bootstrap_test_imports

bootstrap_test_imports()

from src.infrastructure.mysql_repository import MySQLResultRepository
from src.utils.exceptions import JobNotFoundError


class _FakeScalarResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return list(self._rows)


class _FakeResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)

    def scalars(self):
        return _FakeScalarResult(self._rows)


class _FakeSession:
    def __init__(self, rows):
        self._rows = list(rows)

    async def execute(self, query):
        offset_clause = getattr(query, "_offset_clause", None)
        limit_clause = getattr(query, "_limit_clause", None)
        offset = int(offset_clause.value) if offset_clause is not None else 0
        limit = int(limit_clause.value) if limit_clause is not None else None
        rows = self._rows[offset:]
        if limit is not None:
            rows = rows[:limit]
        return _FakeResult(rows)


def _job(job_id: str, device_id: str, *, tenant_id: str, device_ids: list[str] | None = None):
    parameters = {"tenant_id": tenant_id}
    if device_ids is not None:
        parameters["device_ids"] = device_ids
    return SimpleNamespace(
        job_id=job_id,
        device_id=device_id,
        parameters=parameters,
        created_at=job_id,
    )


@pytest.mark.asyncio
async def test_get_job_scoped_hides_out_of_scope_single_device_job():
    repo = MySQLResultRepository(_FakeSession([]))
    repo.get_job = lambda job_id: _async_return(_job("job-1", "dev-b", tenant_id="tenant-1"))  # type: ignore[method-assign]

    with pytest.raises(JobNotFoundError):
        await repo.get_job_scoped("job-1", tenant_id="tenant-1", accessible_device_ids=["dev-a"])


@pytest.mark.asyncio
async def test_get_job_scoped_allows_scoped_parent_fleet_job():
    repo = MySQLResultRepository(_FakeSession([]))
    parent_job = _job("job-1", "ALL", tenant_id="tenant-1", device_ids=["dev-a", "dev-b"])
    repo.get_job = lambda job_id: _async_return(parent_job)  # type: ignore[method-assign]

    job = await repo.get_job_scoped(
        "job-1",
        tenant_id="tenant-1",
        accessible_device_ids=["dev-a", "dev-b", "dev-c"],
    )
    assert job is parent_job


@pytest.mark.asyncio
async def test_list_jobs_filters_before_pagination_by_accessible_devices():
    jobs = [
        _job("job-3", "dev-c", tenant_id="tenant-1"),
        _job("job-2", "dev-b", tenant_id="tenant-1"),
        _job("job-1", "dev-a", tenant_id="tenant-1"),
    ]
    repo = MySQLResultRepository(_FakeSession(jobs))

    visible = await repo.list_jobs(
        tenant_id="tenant-1",
        accessible_device_ids=["dev-a", "dev-c"],
        limit=10,
        offset=0,
    )

    assert [job.job_id for job in visible] == ["job-3", "job-1"]


@pytest.mark.asyncio
async def test_list_jobs_applies_database_pagination_for_unscoped_history_queries():
    jobs = [
        _job("job-4", "dev-d", tenant_id="tenant-1"),
        _job("job-3", "dev-c", tenant_id="tenant-1"),
        _job("job-2", "dev-b", tenant_id="tenant-1"),
        _job("job-1", "dev-a", tenant_id="tenant-1"),
    ]
    repo = MySQLResultRepository(_FakeSession(jobs))

    visible = await repo.list_jobs(
        tenant_id="tenant-1",
        accessible_device_ids=None,
        limit=2,
        offset=1,
    )

    assert [job.job_id for job in visible] == ["job-3", "job-2"]


def test_tenant_match_clause_uses_first_class_tenant_column_only():
    clause = MySQLResultRepository._tenant_match_clause("tenant-1")
    compiled = str(
        clause.compile(
            dialect=mysql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "analytics_jobs.tenant_id = 'tenant-1'" in compiled
    assert "json_extract" not in compiled.lower()
    assert " or " not in compiled.lower()


def test_list_query_does_not_load_results_blob_by_default():
    repo = MySQLResultRepository(_FakeSession([]))
    query = repo._list_jobs_base_query(tenant_id="tenant-1")
    compiled = str(
        query.compile(
            dialect=mysql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "analytics_jobs.results" not in compiled
    assert "analytics_jobs.tenant_id = 'tenant-1'" in compiled


@pytest.mark.asyncio
async def test_list_tenant_job_counts_uses_first_class_tenant_column_only():
    class _CaptureSession:
        def __init__(self):
            self.query = None

        async def execute(self, query):
            self.query = query
            return _FakeResult([])

    session = _CaptureSession()
    repo = MySQLResultRepository(session)

    await repo.list_tenant_job_counts(limit=5)

    compiled = str(
        session.query.compile(
            dialect=mysql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "analytics_jobs.tenant_id" in compiled
    assert "json_extract" not in compiled.lower()
    assert "coalesce" not in compiled.lower()


def _async_return(value):
    async def _inner(*args, **kwargs):
        return value

    return _inner()
