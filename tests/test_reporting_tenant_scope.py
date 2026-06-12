from __future__ import annotations

import importlib.util
import os
import sys
from datetime import date, datetime, timedelta
from itertools import count
from pathlib import Path
from types import SimpleNamespace
import types

import pytest
from starlette.requests import Request

REPORTING_SERVICE_ROOT = Path(__file__).resolve().parents[1] / "services" / "reporting-service"
if str(REPORTING_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(REPORTING_SERVICE_ROOT))

src_handlers_pkg = types.ModuleType("src.handlers")
src_handlers_pkg.__path__ = [str(REPORTING_SERVICE_ROOT / "src" / "handlers")]
sys.modules.setdefault("src.handlers", src_handlers_pkg)

src_tasks_pkg = types.ModuleType("src.tasks")
src_tasks_pkg.__path__ = [str(REPORTING_SERVICE_ROOT / "src" / "tasks")]
sys.modules.setdefault("src.tasks", src_tasks_pkg)

fake_report_task = types.ModuleType("src.tasks.report_task")


async def _noop(*args, **kwargs):  # noqa: ANN001
    return None


async def _admit(*args, **kwargs):  # noqa: ANN001
    return 0


fake_report_task.run_consumption_report = _noop
fake_report_task.run_comparison_report = _noop
sys.modules.setdefault("src.tasks.report_task", fake_report_task)

os.environ.setdefault("REDIS_URL", "memory://")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///dummy.db")
os.environ.setdefault("INFLUXDB_URL", "http://localhost:8086")
os.environ.setdefault("INFLUXDB_TOKEN", "dummy-token")
os.environ.setdefault("INFLUXDB_ORG", "dummy-org")

src_services_pkg = types.ModuleType("src.services")
src_services_pkg.__path__ = [str(REPORTING_SERVICE_ROOT / "src" / "services")]
sys.modules["src.services"] = src_services_pkg

fake_influx_reader = types.ModuleType("src.services.influx_reader")


class _FakeInfluxReader:
    async def query_telemetry(self, *args, **kwargs):
        return []


fake_influx_reader.InfluxReader = _FakeInfluxReader
fake_influx_reader.influx_reader = _FakeInfluxReader()
sys.modules["src.services.influx_reader"] = fake_influx_reader

src_database_stub = types.ModuleType("src.database")


async def _get_db():
    yield None


src_database_stub.get_db = _get_db
src_database_stub.engine = None
src_database_stub.AsyncSessionLocal = None
sys.modules["src.database"] = src_database_stub


def load_reporting_module(module_name: str, filename: str):
    spec = importlib.util.spec_from_file_location(module_name, REPORTING_SERVICE_ROOT / "src" / "handlers" / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


report_common = load_reporting_module("src.handlers.report_common", "report_common.py")
energy_reports = load_reporting_module("src.handlers.energy_reports", "energy_reports.py")
comparison_reports = load_reporting_module("src.handlers.comparison_reports", "comparison_reports.py")

for _mod in (report_common, energy_reports, comparison_reports):
    _limiter = getattr(_mod, "limiter", None)
    if _limiter is not None:
        _limiter.enabled = False

from fastapi import HTTPException
from src.handlers.report_common import ScheduleCreateRequest
from src.repositories.report_repository import ReportRepository
from src.repositories.scheduled_repository import ScheduledRepository
from src.repositories.tariff_repository import TariffRepository
from src.schemas.requests import ComparisonReportRequest, ConsumptionReportRequest
from services.shared.tenant_context import TenantContext

_CLIENT_COUNTER = count(1)


class _Result:
    def __init__(self, rows=None):
        self._rows = rows or []

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _Session:
    def __init__(self, results=None):
        self.results = list(results or [])
        self.executed = []
        self.added = []
        self.refreshed = []
        self.commits = 0

    async def execute(self, statement):
        self.executed.append(statement)
        if self.results:
            return self.results.pop(0)
        return _Result()

    def add(self, instance):
        self.added.append(instance)

    async def flush(self):
        return None

    async def refresh(self, instance):
        self.refreshed.append(instance)

    async def commit(self):
        self.commits += 1


def _request(tenant_id: str | None = None):
    client_id = next(_CLIENT_COUNTER)
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/reports",
        "headers": [],
        "query_string": b"",
        "client": (f"127.0.1.{client_id}", 6000 + client_id),
    }
    request = Request(scope)
    if tenant_id is not None:
        request.state.tenant_context = TenantContext(
            tenant_id=tenant_id,
            user_id="user-1",
            role="plant_manager",
            plant_ids=[],
            is_super_admin=False,
        )
    return request


def _request_with_query(tenant_id: str | None = None, query_params: dict[str, str] | None = None):
    request = _request(tenant_id)
    request.scope["query_string"] = "&".join(
        f"{key}={value}" for key, value in (query_params or {}).items()
    ).encode("utf-8")
    return request


@pytest.mark.asyncio
async def test_report_repository_create_report_requires_tenant_and_sets_tenant_id():
    session = _Session()
    repo = ReportRepository(session)

    report = await repo.create_report(
        report_id="REP-1",
        report_type="consumption",
        params={"tenant_id": "TENANT-1"},
        tenant_id="TENANT-1",
    )

    assert report.tenant_id == "TENANT-1"
    assert session.added[0].tenant_id == "TENANT-1"
    assert session.commits == 1

    with pytest.raises(ValueError, match="Tenant scope is required"):
        await repo.create_report(
            report_id="REP-2",
            report_type="consumption",
            params={},
        )


@pytest.mark.asyncio
async def test_report_repository_reads_are_scoped_by_explicit_tenant():
    session = _Session()
    repo = ReportRepository(session)

    await repo.list_reports("TENANT-1", 20, 0, "consumption")
    await repo.get_report("REP-1", "TENANT-1")
    await repo.find_active_duplicate("consumption", "sig-1", tenant_id="TENANT-1")

    compiled = [str(stmt.compile(compile_kwargs={"literal_binds": True})) for stmt in session.executed]
    assert compiled
    assert all("tenant_id = 'TENANT-1'" in sql for sql in compiled)


@pytest.mark.asyncio
async def test_schedule_and_tariff_repositories_persist_tenant_id():
    session = _Session()

    schedule_repo = ScheduledRepository(session)
    schedule = await schedule_repo.create_schedule(
        {
            "tenant_id": "TENANT-1",
            "report_type": "consumption",
            "frequency": "daily",
            "params_template": {},
        }
    )
    assert schedule.tenant_id == "TENANT-1"
    assert session.added[0].tenant_id == "TENANT-1"

    tariff_repo = TariffRepository(session)
    tariff = await tariff_repo.upsert_tariff(
        {
            "tenant_id": "TENANT-1",
            "energy_rate_per_kwh": 8.5,
            "currency": "INR",
        }
    )
    assert tariff.tenant_id == "TENANT-1"
    assert session.added[-1].tenant_id == "TENANT-1"


@pytest.mark.asyncio
async def test_consumption_report_submission_uses_auth_tenant_and_processes():
    captured = {}
    queued_jobs = []

    class FakeReportRepository:
        def __init__(self, db, ctx=None, allow_cross_tenant=False):
            self.db = db
            self.ctx = ctx
            self.allow_cross_tenant = allow_cross_tenant

        async def find_active_duplicate(self, *args, **kwargs):
            return None

        async def create_report(self, **kwargs):
            captured["create"] = kwargs
            return SimpleNamespace(report_id=kwargs["report_id"], status="pending", created_at=datetime.utcnow())

        async def update_report(self, *args, **kwargs):
            captured.setdefault("updates", []).append((args, kwargs))

    async def fake_validate_device_for_reporting(device_id, ctx):  # noqa: ANN001
        captured["validated"] = (device_id, ctx.require_tenant())
        return {"device_id": device_id}

    class FakeQueue:
        async def enqueue(self, job):  # noqa: ANN001
            queued_jobs.append(job)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(energy_reports, "ReportRepository", FakeReportRepository)
    monkeypatch.setattr(energy_reports, "validate_device_for_reporting", fake_validate_device_for_reporting)
    monkeypatch.setattr(energy_reports, "get_report_queue", lambda: FakeQueue())
    async def _admit(*args, **kwargs):  # noqa: ANN001
        return 0

    monkeypatch.setattr(energy_reports, "enforce_report_admission", _admit)
    try:
        request = ConsumptionReportRequest(
            device_id="DEV-1",
            start_date=date.today() - timedelta(days=1),
            end_date=date.today(),
            report_name="Tenant-safe report",
            tenant_id=None,
        )
        app_request = _request("TENANT-AUTH")
        response = await energy_reports.create_energy_consumption_report(
            body=request,
            request=app_request,
            db=object(),
        )
    finally:
        monkeypatch.undo()

    assert response.status == "pending"
    assert captured["create"]["tenant_id"] == "TENANT-AUTH"
    assert captured["validated"] == ("DEV-1", "TENANT-AUTH")
    assert len(queued_jobs) == 1
    assert queued_jobs[0].tenant_id == "TENANT-AUTH"
    assert queued_jobs[0].report_type == "consumption"


def test_resolve_submission_tenant_id_accepts_tenant_id_query():
    app_request = _request_with_query(query_params={"tenant_id": "TENANT-ORG"})

    assert energy_reports.resolve_submission_tenant_id(app_request, None) == "TENANT-ORG"


def test_resolve_submission_tenant_id_rejects_conflicting_body_and_request_tenants():
    app_request = _request_with_query(tenant_id="TENANT-AUTH")

    with pytest.raises(HTTPException) as exc_info:
        energy_reports.resolve_submission_tenant_id(app_request, "TENANT-BODY")

    assert exc_info.value.detail["code"] == "TENANT_SCOPE_MISMATCH"


@pytest.mark.asyncio
async def test_consumption_report_submission_requires_tenant_scope():
    request = ConsumptionReportRequest(
        device_id="DEV-1",
        start_date=date.today() - timedelta(days=1),
        end_date=date.today(),
        report_name="Tenant-safe report",
        tenant_id=None,
    )
    app_request = _request(None)

    with pytest.raises(HTTPException) as exc:
        await energy_reports.create_energy_consumption_report(
            body=request,
            request=app_request,
            db=object(),
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_comparison_report_submission_uses_auth_tenant():
    captured = {}
    queued_jobs = []

    class FakeReportRepository:
        def __init__(self, db, ctx=None, allow_cross_tenant=False):
            self.db = db
            self.ctx = ctx
            self.allow_cross_tenant = allow_cross_tenant

        async def create_report(self, **kwargs):
            captured["create"] = kwargs
            return SimpleNamespace(report_id=kwargs["report_id"], status="pending", created_at=datetime.utcnow())

        async def update_report(self, *args, **kwargs):
            captured.setdefault("updates", []).append((args, kwargs))

        async def find_active_duplicate(self, *args, **kwargs):  # noqa: ANN001
            return None

    async def fake_validate_device(device_id, ctx):  # noqa: ANN001
        captured.setdefault("validated", []).append((device_id, ctx.require_tenant()))
        return {"device_id": device_id}

    class FakeQueue:
        async def enqueue(self, job):  # noqa: ANN001
            queued_jobs.append(job)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(comparison_reports, "ReportRepository", FakeReportRepository)
    monkeypatch.setattr(comparison_reports, "validate_device_for_reporting", fake_validate_device)
    monkeypatch.setattr(comparison_reports, "get_report_queue", lambda: FakeQueue())
    monkeypatch.setattr(energy_reports, "get_report_queue", lambda: FakeQueue())
    monkeypatch.setattr(comparison_reports, "enforce_report_admission", _admit)
    try:
        request = ComparisonReportRequest(
            comparison_type="machine_vs_machine",
            tenant_id="TENANT-AUTH",
            machine_a_id="DEV-A",
            machine_b_id="DEV-B",
            start_date=date.today() - timedelta(days=2),
            end_date=date.today() - timedelta(days=1),
        )
        app_request = _request("TENANT-AUTH")
        response = await comparison_reports.create_comparison_report(
            body=request,
            request=app_request,
            db=object(),
        )
    finally:
        monkeypatch.undo()

    assert response.status == "pending"
    assert captured["create"]["tenant_id"] == "TENANT-AUTH"
    assert captured["validated"] == [("DEV-A", "TENANT-AUTH"), ("DEV-B", "TENANT-AUTH")]
    assert len(queued_jobs) == 1
    assert queued_jobs[0].tenant_id == "TENANT-AUTH"
    assert queued_jobs[0].report_type == "comparison"


@pytest.mark.asyncio
async def test_schedule_creation_uses_tenant_scope_from_request():
    captured = {}

    class FakeScheduledRepository:
        def __init__(self, db, ctx=None, allow_cross_tenant=False):
            self.db = db
            self.ctx = ctx

        async def create_schedule(self, payload):
            captured["payload"] = payload
            return SimpleNamespace(
                schedule_id="SCH-1",
                tenant_id=payload["tenant_id"],
                report_type=SimpleNamespace(value=payload["report_type"]),
                frequency=SimpleNamespace(value=payload["frequency"]),
                is_active=True,
                next_run_at=datetime.utcnow(),
                created_at=datetime.utcnow(),
            )

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(report_common, "ScheduledRepository", FakeScheduledRepository)
    monkeypatch.setattr(report_common, "_resolve_accessible_device_ids", _noop)
    try:
        response = await report_common.create_schedule(
            data=ScheduleCreateRequest(
                report_type="consumption",
                frequency="daily",
                params_template={"device_ids": ["DEV-1"]},
            ),
            request=_request("TENANT-AUTH"),
            db=object(),
        )
    finally:
        monkeypatch.undo()

    assert response["tenant_id"] == "TENANT-AUTH"
    assert captured["payload"]["tenant_id"] == "TENANT-AUTH"


@pytest.mark.asyncio
async def test_report_status_uses_request_tenant_context():
    captured = {}

    async def _fake_estimate(_report):
        return SimpleNamespace(
            queue_position=None,
            estimated_wait_seconds=None,
            estimated_completion_seconds=None,
            estimate_quality="unknown",
        )

    class FakeReportRepository:
        def __init__(self, db, ctx=None, allow_cross_tenant=False):
            captured["ctx"] = ctx

        async def get_report(self, report_id, tenant_id=None, *args, **kwargs):  # noqa: ANN001
            captured["tenant_id"] = tenant_id
            return SimpleNamespace(
                report_id=report_id,
                report_type=SimpleNamespace(value="consumption"),
                status=SimpleNamespace(value="processing"),
                progress=42,
                error_code=None,
                error_message=None,
                created_at=None,
                processing_started_at=None,
                completed_at=None,
                s3_key=None,
                result_json=None,
            )

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(report_common, "ReportRepository", FakeReportRepository)
    monkeypatch.setattr(report_common, "_resolve_accessible_device_ids", _noop)
    monkeypatch.setattr(
        report_common,
        "ReportJobStatusEstimator",
        lambda _db: SimpleNamespace(
            estimate=_fake_estimate,
        ),
    )
    try:
        response = await report_common.get_report_status(
            report_id="REP-1",
            request=_request("TENANT-AUTH"),
            db=object(),
        )
    finally:
        monkeypatch.undo()

    assert response["report_id"] == "REP-1"
    assert response["status"] == "running"
    assert response["progress"] == 42
    assert captured["tenant_id"] == "TENANT-AUTH"
    assert getattr(captured["ctx"], "tenant_id", None) == "TENANT-AUTH"
