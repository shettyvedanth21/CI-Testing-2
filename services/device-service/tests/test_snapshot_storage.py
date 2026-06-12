from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import importlib.util
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


BASE_DIR = Path(__file__).resolve().parents[1]
SERVICES_DIR = Path(__file__).resolve().parents[2]
PROJECT_ROOT = Path(__file__).resolve().parents[3]
for path in (BASE_DIR, SERVICES_DIR, PROJECT_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

from app.api.v1.devices import get_dashboard_summary
from app.database import Base
from app.config import settings
from app.models.device import DashboardSnapshot
from app.services.dashboard import DashboardService
from app.tasks.migrate_snapshots import migrate_snapshots_to_minio
from services.shared.tenant_context import TenantContext


class FakeMappingsResult:
    def __init__(self, row):
        self._row = row

    def mappings(self):
        return self

    def first(self):
        return self._row


class FakeScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class FakeSnapshotSession:
    def __init__(self, row=None, mapping_row=None):
        self.row = row
        self.mapping_row = mapping_row
        self.added = None
        self.flush = AsyncMock()
        self.commit = AsyncMock()

    async def get(self, model, key):
        if isinstance(key, dict):
            if (
                self.row is not None
                and getattr(self.row, "tenant_id", None) == key.get("tenant_id")
                and getattr(self.row, "snapshot_key", None) == key.get("snapshot_key")
            ):
                return self.row
            return None
        return self.row

    def add(self, row):
        self.row = row
        self.added = row

    async def execute(self, statement):
        return FakeMappingsResult(self.mapping_row)


class FakeMigrationSession:
    def __init__(self, rows):
        self.rows = rows
        self.commit = AsyncMock()

    async def execute(self, statement):
        pending = [row for row in self.rows if row.storage_backend == "mysql" and row.payload_json is not None]
        return FakeScalarResult(pending[:50])


@pytest.fixture(autouse=True)
def reset_snapshot_state():
    DashboardService._cache = {}
    DashboardService._snapshot_minio_client = None
    yield
    DashboardService._cache = {}
    DashboardService._snapshot_minio_client = None


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


class FakeMigrationFactory:
    def __init__(self, rows):
        self.session = FakeMigrationSession(rows)

    def __call__(self):
        return self._ctx()

    @asynccontextmanager
    async def _ctx(self):
        yield self.session


def _tenant_ctx(tenant_id: str = "tenant-a") -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        user_id="tester",
        role="internal_service",
        plant_ids=[],
        is_super_admin=False,
    )


@pytest.mark.asyncio
async def test_new_snapshot_written_to_minio(monkeypatch):
    monkeypatch.setattr(settings, "SNAPSHOT_STORAGE_BACKEND", "minio")
    session = FakeSnapshotSession()
    service = DashboardService(session, _tenant_ctx())
    payload = {"generated_at": "2026-03-24T10:00:00+00:00", "value": 123}

    monkeypatch.setattr(
        DashboardService,
        "_upload_snapshot_to_minio",
        classmethod(
            lambda cls, tenant_id, key, generated_at, payload_json: f"{tenant_id}/{key}/object.json"
        ),
    )

    await service._write_snapshot("dashboard:summary:v1", payload)

    assert session.added is not None
    assert session.row.tenant_id == "tenant-a"
    assert session.row.payload_json is None
    assert session.row.s3_key == "tenant-a/dashboard:summary:v1/object.json"
    assert session.row.storage_backend == "minio"


@pytest.mark.asyncio
async def test_snapshot_read_from_minio(monkeypatch):
    payload = {"generated_at": "2026-03-24T10:00:00+00:00", "value": 456}
    session = FakeSnapshotSession(
        mapping_row={
            "tenant_id": "tenant-a",
            "snapshot_key": "dashboard:summary:v1",
            "s3_key": "tenant-a/dashboard:summary:v1/object.json",
            "storage_backend": "minio",
            "payload_json": None,
            "generated_at": datetime.now(timezone.utc),
            "expires_at": None,
        }
    )
    service = DashboardService(session, _tenant_ctx())
    monkeypatch.setattr(DashboardService, "_download_snapshot_from_minio", classmethod(lambda cls, s3_key: '{"generated_at":"2026-03-24T10:00:00+00:00","value":456}'))

    result, stale = await service._read_snapshot("dashboard:summary:v1")

    assert result == payload
    assert stale is False


@pytest.mark.asyncio
async def test_fallback_to_mysql_on_minio_failure(monkeypatch):
    monkeypatch.setattr(settings, "SNAPSHOT_STORAGE_BACKEND", "minio")
    session = FakeSnapshotSession()
    service = DashboardService(session, _tenant_ctx())
    payload = {"generated_at": "2026-03-24T10:00:00+00:00", "value": 789}

    def _boom(cls, tenant_id, key, generated_at, payload_json):
        raise RuntimeError("minio down")

    monkeypatch.setattr(DashboardService, "_upload_snapshot_to_minio", classmethod(_boom))

    await service._write_snapshot("dashboard:summary:v1", payload)

    assert session.row.payload_json is not None
    assert session.row.s3_key is None
    assert session.row.storage_backend == "mysql"


def test_snapshot_storage_backend_auto_prefers_mysql_without_snapshot_object_store_config(monkeypatch):
    monkeypatch.setattr(settings, "SNAPSHOT_STORAGE_BACKEND", "auto")
    monkeypatch.setattr(settings, "SNAPSHOT_MINIO_ENDPOINT", None)
    monkeypatch.setattr(settings, "SNAPSHOT_MINIO_ACCESS_KEY", None)
    monkeypatch.setattr(settings, "SNAPSHOT_MINIO_SECRET_KEY", None)

    assert DashboardService._snapshot_storage_backend() == "mysql"


def test_snapshot_storage_backend_auto_uses_minio_when_snapshot_object_store_config_exists(monkeypatch):
    monkeypatch.setattr(settings, "SNAPSHOT_STORAGE_BACKEND", "auto")
    monkeypatch.setattr(settings, "SNAPSHOT_MINIO_ENDPOINT", "minio:9000")
    monkeypatch.setattr(settings, "SNAPSHOT_MINIO_ACCESS_KEY", "minio")
    monkeypatch.setattr(settings, "SNAPSHOT_MINIO_SECRET_KEY", "minio123")

    assert DashboardService._snapshot_storage_backend() == "minio"


@pytest.mark.asyncio
async def test_read_fallback_on_minio_failure(monkeypatch):
    session = FakeSnapshotSession(
        mapping_row={
            "tenant_id": "tenant-a",
            "snapshot_key": "dashboard:summary:v1",
            "s3_key": "tenant-a/dashboard:summary:v1/object.json",
            "storage_backend": "minio",
            "payload_json": '{"generated_at":"2026-03-24T10:00:00+00:00","value":999}',
            "generated_at": datetime.now(timezone.utc),
            "expires_at": None,
        }
    )
    service = DashboardService(session, _tenant_ctx())
    monkeypatch.setattr(DashboardService, "_download_snapshot_from_minio", classmethod(lambda cls, s3_key: (_ for _ in ()).throw(RuntimeError("down"))))

    result, stale = await service._read_snapshot("dashboard:summary:v1")

    assert result["value"] == 999
    assert stale is True


@pytest.mark.asyncio
async def test_migration_job_moves_existing_rows(monkeypatch):
    monkeypatch.setattr(settings, "MIGRATE_SNAPSHOTS_TO_MINIO", True)
    rows = [
        SimpleNamespace(
            tenant_id=f"tenant-{idx % 2}",
            snapshot_key=f"dashboard:summary:v1:{idx}",
            payload_json=f'{{"idx":{idx}}}',
            storage_backend="mysql",
            s3_key=None,
            generated_at=datetime.now(timezone.utc),
            expires_at=None,
        )
        for idx in range(10)
    ]
    fake_factory = FakeMigrationFactory(rows)
    put_object_mock = MagicMock()
    fake_client = SimpleNamespace(
        bucket_exists=MagicMock(return_value=True),
        make_bucket=MagicMock(),
        put_object=put_object_mock,
    )
    monkeypatch.setattr(DashboardService, "_get_snapshot_minio_client", classmethod(lambda cls: fake_client))
    monkeypatch.setattr(DashboardService, "_ensure_snapshot_bucket", classmethod(lambda cls: None))

    summary = await migrate_snapshots_to_minio(session_factory=fake_factory)

    assert summary["migrated"] == 10
    assert summary["failed"] == 0
    assert all(row.storage_backend == "minio" for row in rows)
    assert all(row.payload_json is None for row in rows)
    assert all(row.s3_key for row in rows)
    assert len({row.s3_key for row in rows}) == 10
    assert put_object_mock.call_count == 10


@pytest.mark.asyncio
async def test_snapshot_read_is_tenant_scoped(session_factory):
    async with session_factory() as session:
        session.add_all(
            [
                DashboardSnapshot(
                    tenant_id="tenant-a",
                    snapshot_key="dashboard:summary:v1",
                    payload_json='{"value":"tenant-a"}',
                    s3_key=None,
                    storage_backend="mysql",
                    generated_at=datetime.now(timezone.utc),
                    expires_at=None,
                ),
                DashboardSnapshot(
                    tenant_id="tenant-b",
                    snapshot_key="dashboard:summary:v1",
                    payload_json='{"value":"tenant-b"}',
                    s3_key=None,
                    storage_backend="mysql",
                    generated_at=datetime.now(timezone.utc),
                    expires_at=None,
                ),
            ]
        )
        await session.commit()

        tenant_a_service = DashboardService(session, _tenant_ctx("tenant-a"))
        tenant_b_service = DashboardService(session, _tenant_ctx("tenant-b"))

        tenant_a_payload, _ = await tenant_a_service._read_snapshot("dashboard:summary:v1")
        tenant_b_payload, _ = await tenant_b_service._read_snapshot("dashboard:summary:v1")

    assert tenant_a_payload["value"] == "tenant-a"
    assert tenant_b_payload["value"] == "tenant-b"


@pytest.mark.asyncio
async def test_duplicate_snapshot_keys_across_tenants_remain_isolated(session_factory):
    async with session_factory() as session:
        tenant_a_service = DashboardService(session, _tenant_ctx("tenant-a"))
        tenant_b_service = DashboardService(session, _tenant_ctx("tenant-b"))

        await tenant_a_service._write_snapshot(
            "dashboard:summary:v1",
            {"generated_at": "2026-03-24T10:00:00+00:00", "value": "tenant-a"},
        )
        await tenant_b_service._write_snapshot(
            "dashboard:summary:v1",
            {"generated_at": "2026-03-24T10:00:00+00:00", "value": "tenant-b"},
        )
        await session.commit()

        rows = (
            await session.execute(
                select(DashboardSnapshot).order_by(
                    DashboardSnapshot.tenant_id.asc(),
                    DashboardSnapshot.snapshot_key.asc(),
                )
            )
        ).scalars().all()

        tenant_a_payload, _ = await tenant_a_service._read_snapshot("dashboard:summary:v1")
        tenant_b_payload, _ = await tenant_b_service._read_snapshot("dashboard:summary:v1")

    assert len(rows) == 2
    assert {(row.tenant_id, row.snapshot_key) for row in rows} == {
        ("tenant-a", "dashboard:summary:v1"),
        ("tenant-b", "dashboard:summary:v1"),
    }
    assert tenant_a_payload["value"] == "tenant-a"
    assert tenant_b_payload["value"] == "tenant-b"


def test_legacy_snapshot_key_backfill_parser():
    migration_path = (
        PROJECT_ROOT
        / "services"
        / "device-service"
        / "alembic"
        / "versions"
        / "20260403_0001_dashboard_snapshots_tenant_scope.py"
    )
    spec = importlib.util.spec_from_file_location("dashboard_snapshots_tenant_scope_migration", migration_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    tenant_id, snapshot_key = module._split_legacy_snapshot_key("tenant-a:dashboard:summary:v1")

    assert tenant_id == "tenant-a"
    assert snapshot_key == "dashboard:summary:v1"

    with pytest.raises(RuntimeError):
        module._split_legacy_snapshot_key("dashboard:summary:v1")



@pytest.mark.asyncio
async def test_api_response_unchanged(monkeypatch):
    expected_payload = {
        "success": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "stale": False,
        "warnings": [],
        "degraded_services": [],
        "summary": {
            "total_devices": 2,
            "running_devices": 1,
            "stopped_devices": 1,
            "idle_devices": 0,
            "in_load_devices": 1,
            "overconsumption_devices": 0,
            "unknown_devices": 1,
            "status_counts": {
                "unknown": 1,
                "stopped": 0,
                "idle": 0,
                "running": 1,
                "overconsumption": 0,
            },
            "devices_with_health_data": 2,
            "devices_with_health_configured": 2,
            "devices_missing_health_config": 0,
            "devices_with_uptime_configured": 1,
            "devices_missing_uptime_config": 1,
            "system_health": 88.5,
            "average_efficiency": 91.2,
        },
        "alerts": {
            "active_alerts": 0,
            "alerts_triggered": 0,
            "alerts_cleared": 0,
            "rules_created": 0,
        },
        "devices": [
            {
                "device_id": "D-1",
                "device_name": "Pump 1",
                "device_type": "pump",
                "runtime_status": "running",
                "operational_status": "running",
                "location": "Plant A",
                "last_seen_timestamp": datetime.now(timezone.utc),
                "health_score": 95.5,
                "uptime_percentage": 97.0,
                "has_uptime_config": True,
                "data_freshness_ts": datetime.now(timezone.utc),
                "version": 1,
            }
        ],
        "energy_widgets": {"today_energy_kwh": 10.0},
        "cost_data_state": "fresh",
        "cost_data_reasons": [],
        "cost_generated_at": datetime.now(timezone.utc).isoformat(),
    }

    class FakeDashboardService:
        def __init__(self, session, ctx=None):
            self.session = session
            self.ctx = ctx

        async def get_dashboard_summary(self, tenant_id=None, plant_id=None, accessible_plant_ids=None):
            return expected_payload

    monkeypatch.setattr("app.services.live_dashboard.LiveDashboardService", FakeDashboardService)
    response = Response()
    request = SimpleNamespace(
        headers={"X-Tenant-Id": "tenant-a"},
        query_params={},
        state=SimpleNamespace(
            role="internal_service",
            tenant_context=TenantContext(
                tenant_id="tenant-a",
                user_id="tester",
                role="internal_service",
                plant_ids=[],
                is_super_admin=False,
            ),
        ),
    )

    result = await get_dashboard_summary(request=request, response=response, db=AsyncMock())

    assert response.headers["Cache-Control"] == "no-store"
    assert result.model_dump(mode="json")["summary"]["total_devices"] == 2
    assert set(result.model_dump().keys()) == {
        "success",
        "generated_at",
        "stale",
        "warnings",
        "degraded_services",
        "summary",
        "alerts",
        "devices",
        "energy_widgets",
        "cost_data_state",
        "cost_data_reasons",
        "cost_generated_at",
    }


@pytest.mark.asyncio
async def test_materialized_summary_does_not_count_unknown_devices_as_stopped(monkeypatch):
    service = DashboardService(FakeSnapshotSession(), _tenant_ctx())
    now = datetime.now(timezone.utc)
    fleet_payload = {
        "devices": [
            {
                "device_id": "RUN-1",
                "device_name": "Run 1",
                "device_type": "compressor",
                "runtime_status": "running",
                "operational_status": "running",
                "location": "Plant A",
                "last_seen_timestamp": now.isoformat(),
                "health_score": 95.0,
                "uptime_percentage": 98.0,
                "has_uptime_config": True,
            },
            {
                "device_id": "UNK-1",
                "device_name": "Unknown 1",
                "device_type": "compressor",
                "runtime_status": "unknown",
                "operational_status": "unknown",
                "location": "Plant A",
                "last_seen_timestamp": (now).isoformat(),
                "health_score": None,
                "uptime_percentage": None,
                "has_uptime_config": False,
            },
        ],
        "warnings": [],
        "degraded_services": [],
    }
    energy_payload = {
        "month_energy_kwh": 0.0,
        "month_energy_cost_inr": 0.0,
        "today_energy_kwh": 0.0,
        "today_energy_cost_inr": 0.0,
        "today_loss_kwh": 0.0,
        "today_loss_cost_inr": 0.0,
        "generated_at": now.isoformat(),
        "currency": "INR",
        "data_quality": "ok",
        "invariant_checks": {},
        "reconciliation_warning": None,
        "no_nan_inf": True,
        "warnings": [],
    }

    read_snapshot = AsyncMock(side_effect=[(fleet_payload, False), (energy_payload, False)])
    monkeypatch.setattr(service, "_read_snapshot", read_snapshot)
    monkeypatch.setattr(service, "_get_alerts_summary", AsyncMock(return_value=({"active_alerts": 0}, None)))
    monkeypatch.setattr(
        "app.services.health_config.HealthConfigService.get_active_health_configs_by_devices",
        AsyncMock(return_value=["RUN-1"]),
    )

    payload = await service.materialize_dashboard_summary_snapshot()

    assert payload["summary"]["total_devices"] == 2
    assert payload["summary"]["running_devices"] == 1
    assert payload["summary"]["stopped_devices"] == 0
    assert payload["summary"]["unknown_devices"] == 1
    assert payload["summary"]["status_counts"]["stopped"] == 0
    assert payload["summary"]["status_counts"]["unknown"] == 1


@pytest.mark.asyncio
async def test_materialized_summary_returns_zero_counts_for_empty_fleet(monkeypatch):
    service = DashboardService(FakeSnapshotSession(), _tenant_ctx())
    now = datetime.now(timezone.utc)
    fleet_payload = {
        "devices": [],
        "warnings": [],
        "degraded_services": [],
    }
    energy_payload = {
        "month_energy_kwh": 0.0,
        "month_energy_cost_inr": 0.0,
        "today_energy_kwh": 0.0,
        "today_energy_cost_inr": 0.0,
        "today_loss_kwh": 0.0,
        "today_loss_cost_inr": 0.0,
        "generated_at": now.isoformat(),
        "currency": "INR",
        "data_quality": "ok",
        "invariant_checks": {},
        "reconciliation_warning": None,
        "no_nan_inf": True,
        "warnings": [],
    }

    read_snapshot = AsyncMock(side_effect=[(fleet_payload, False), (energy_payload, False)])
    monkeypatch.setattr(service, "_read_snapshot", read_snapshot)
    monkeypatch.setattr(service, "_get_alerts_summary", AsyncMock(return_value=({"active_alerts": 0}, None)))
    monkeypatch.setattr(
        "app.services.health_config.HealthConfigService.get_active_health_configs_by_devices",
        AsyncMock(return_value={}),
    )

    payload = await service.materialize_dashboard_summary_snapshot()

    assert payload["summary"]["total_devices"] == 0
    assert payload["summary"]["running_devices"] == 0
    assert payload["summary"]["stopped_devices"] == 0
    assert payload["summary"]["unknown_devices"] == 0
    assert payload["summary"]["devices_with_health_data"] == 0
    assert payload["summary"]["devices_with_health_configured"] == 0
    assert payload["summary"]["devices_missing_health_config"] == 0
    assert payload["summary"]["devices_with_uptime_configured"] == 0
    assert payload["summary"]["devices_missing_uptime_config"] == 0


@pytest.mark.asyncio
async def test_get_monthly_energy_returns_stale_zero_payload_when_snapshot_and_refresh_are_unavailable(monkeypatch):
    service = DashboardService(FakeSnapshotSession(), _tenant_ctx())
    monkeypatch.setattr(service, "_read_snapshot", AsyncMock(return_value=(None, True)))
    monkeypatch.setattr(service, "materialize_monthly_energy_snapshot", AsyncMock(side_effect=RuntimeError("refresh failed")))

    payload = await service.get_monthly_energy(2026, 5)

    assert payload["success"] is True
    assert payload["stale"] is True
    assert payload["summary"]["total_energy_kwh"] == 0.0
    assert payload["summary"]["total_energy_cost_inr"] == 0.0
    assert payload["days"] == []
    assert payload["cost_data_state"] == "unavailable"
    assert "snapshot_unavailable" in payload["warnings"]
