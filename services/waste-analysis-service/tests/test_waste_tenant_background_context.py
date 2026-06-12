from __future__ import annotations

import os
import sys
from datetime import date
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


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

SERVICE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
REPO_ROOT = os.path.abspath(os.path.join(SERVICE_ROOT, "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
SERVICES_ROOT = os.path.join(REPO_ROOT, "services")
if SERVICES_ROOT not in sys.path:
    sys.path.insert(0, SERVICES_ROOT)

from src.models import Base, WasteAnalysisJob, WasteDeviceSummary, WasteGranularity, WasteScope, WasteStatus
from src.repositories.waste_repository import WasteRepository
from src.tasks import waste_task
from services.shared.tenant_context import TenantContext


class _DummySessionCtx:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_run_waste_analysis_builds_tenant_scoped_repository(monkeypatch):
    contexts: list[TenantContext] = []

    class FakeRepo:
        def __init__(self, db, ctx=None):
            self._calls = 0
            contexts.append(ctx)

        async def update_job(self, *_args, **_kwargs):
            self._calls += 1
            if self._calls == 1:
                raise RuntimeError("stop_after_context_assertion")

    monkeypatch.setattr(waste_task, "AsyncSessionLocal", lambda: _DummySessionCtx())
    monkeypatch.setattr(waste_task, "WasteRepository", FakeRepo)

    await waste_task.run_waste_analysis(
        "waste-job-1",
        {
            "tenant_id": "SH00000001",
            "start_date": "2026-04-01",
            "end_date": "2026-04-01",
            "scope": "all",
        },
    )

    assert contexts
    assert contexts[0].tenant_id == "SH00000001"
    assert contexts[0].is_super_admin is False


@pytest.mark.asyncio
async def test_run_waste_analysis_rejects_missing_tenant_scope(monkeypatch):
    monkeypatch.setattr(waste_task, "AsyncSessionLocal", lambda: _DummySessionCtx())

    with pytest.raises(HTTPException) as excinfo:
        await waste_task.run_waste_analysis(
            "waste-job-no-tenant",
            {
                "start_date": "2026-04-01",
                "end_date": "2026-04-01",
                "scope": "all",
            },
        )

    assert excinfo.value.status_code == 403
    assert excinfo.value.detail["code"] == "TENANT_SCOPE_REQUIRED"


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


@pytest.mark.asyncio
async def test_replace_device_summaries_chunked_persists_tenant_id(session_factory):
    async with session_factory() as session:
        repo = WasteRepository(
            session,
            TenantContext(
                tenant_id="SH00000001",
                user_id="svc:waste-analysis-service",
                role="internal_service",
                plant_ids=[],
                is_super_admin=False,
            ),
        )
        await repo.replace_device_summaries_chunked(
            "waste-job-tenant-summary",
            summaries=[
                {
                    "device_id": "AD00000001",
                    "device_name": "Device 1",
                    "data_source_type": "metered",
                }
            ],
            batch_size=1,
        )
        rows = (await session.execute(select(WasteDeviceSummary))).scalars().all()

    assert len(rows) == 1
    assert rows[0].tenant_id == "SH00000001"
    assert rows[0].job_id == "waste-job-tenant-summary"


@pytest.mark.asyncio
async def test_replace_device_summaries_chunked_requires_tenant_scope(session_factory):
    async with session_factory() as session:
        repo = WasteRepository(session)
        with pytest.raises(HTTPException) as excinfo:
            await repo.replace_device_summaries_chunked(
                "waste-job-system-context",
                summaries=[{"device_id": "AD00000001"}],
                batch_size=1,
            )

    assert excinfo.value.status_code == 403
    assert excinfo.value.detail["code"] == "TENANT_SCOPE_REQUIRED"


@pytest.mark.asyncio
async def test_run_waste_analysis_keeps_result_when_pdf_upload_fails(session_factory, monkeypatch):
    factory = session_factory

    monkeypatch.setattr(waste_task, "AsyncSessionLocal", factory)

    async def _fake_devices(_scope, _requested_ids, _tenant_id):
        return [{"device_id": "AD00000001", "device_name": "Device 1", "data_source_type": "metered"}]

    monkeypatch.setattr(waste_task, "_resolve_devices", _fake_devices)

    async def _fake_tariff(_tenant_id):
        return SimpleNamespace(rate=8.0, currency="INR", stale=False)

    async def _fake_rows(**_kwargs):
        return []

    async def _fake_idle_config(_device_id, _tenant_id):
        return {
            "derived_idle_threshold_a": 12.5,
            "full_load_current_a": 50.0,
        }

    async def _fake_shift_config(_device_id, _tenant_id):
        return []

    async def _fake_waste_config(_device_id, _tenant_id):
        return {
            "derived_overconsumption_threshold_a": 48.0,
        }

    async def _fake_range(**_kwargs):
        return None

    monkeypatch.setattr(waste_task.tariff_cache, "get", _fake_tariff)
    monkeypatch.setattr(waste_task.influx_reader, "query_telemetry", _fake_rows)
    monkeypatch.setattr(waste_task.device_client, "get_idle_config", _fake_idle_config)
    monkeypatch.setattr(waste_task.device_client, "get_shift_config", _fake_shift_config)
    monkeypatch.setattr(waste_task.device_client, "get_waste_config", _fake_waste_config)
    monkeypatch.setattr(waste_task.energy_client, "get_device_range", _fake_range)
    monkeypatch.setattr(waste_task, "_find_reporting_reference_kwh", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(waste_task, "summarize_insights", lambda *_args, **_kwargs: ["Insight"])

    monkeypatch.setattr(
        waste_task,
        "compute_device_waste",
        lambda **kwargs: SimpleNamespace(
            device_id=kwargs["device_id"],
            device_name=kwargs["device_name"],
            data_source_type=kwargs["data_source_type"],
            idle_duration_sec=120,
            idle_energy_kwh=0.5,
            idle_cost=4.0,
            standby_power_kw=None,
            standby_energy_kwh=None,
            standby_cost=None,
            total_energy_kwh=2.0,
            total_cost=16.0,
            offhours_energy_kwh=0.0,
            offhours_cost=0.0,
            offhours_duration_sec=0,
            offhours_skipped_reason=None,
            offhours_pf_estimated=False,
            overconsumption_duration_sec=0,
            overconsumption_energy_kwh=0.0,
            overconsumption_cost=0.0,
            overconsumption_skipped_reason=None,
            overconsumption_pf_estimated=False,
            overconsumption_config_source=None,
            overconsumption_config_used={},
            unoccupied_duration_sec=None,
            unoccupied_energy_kwh=None,
            unoccupied_cost=None,
            unoccupied_skipped_reason=None,
            unoccupied_pf_estimated=False,
            unoccupied_config_source=None,
            unoccupied_config_used=None,
            idle_status="configured",
            power_unit_input="kW",
            power_unit_normalized_to="kW",
            normalization_applied=False,
            pf_estimated=False,
            data_quality="high",
            energy_quality="high",
            idle_quality="high",
            standby_quality="high",
            overall_quality="high",
            warnings=[],
            calculation_method="interval_power",
        ),
    )

    from src.pdf import builder as pdf_builder

    monkeypatch.setattr(pdf_builder, "generate_waste_pdf", lambda payload: b"pdf-bytes")

    async def _broken_async_upload(_pdf_bytes, _s3_key):
        raise RuntimeError("InvalidAccessKeyId")

    monkeypatch.setattr(waste_task.minio_client, "async_upload_pdf", _broken_async_upload)

    async with factory() as session:
        session.add(
            WasteAnalysisJob(
                id="waste-artifact-failure",
                tenant_id="SH00000001",
                job_name="Artifact failure",
                scope=WasteScope.selected,
                device_ids=["AD00000001"],
                start_date=date(2026, 5, 1),
                end_date=date(2026, 5, 7),
                granularity=WasteGranularity.daily,
                status=WasteStatus.pending,
                progress_pct=0,
                stage="Queued",
                result_json={"tenant_id": "SH00000001"},
            )
        )
        await session.commit()

    await waste_task.run_waste_analysis(
        "waste-artifact-failure",
        {
            "tenant_id": "SH00000001",
            "start_date": "2026-05-01",
            "end_date": "2026-05-07",
            "scope": "selected",
            "device_ids": ["AD00000001"],
            "granularity": "daily",
        },
    )

    async with factory() as session:
        job = await session.get(WasteAnalysisJob, "waste-artifact-failure")

    assert job is not None
    assert job.status == WasteStatus.completed
    assert job.error_code == "ARTIFACT_UPLOAD_FAILED"
    assert job.s3_key is None
    assert job.download_url is None
    assert job.result_json["device_summaries"][0]["device_id"] == "AD00000001"
