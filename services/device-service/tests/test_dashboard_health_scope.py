from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

BASE_DIR = Path(__file__).resolve().parents[1]
SERVICES_DIR = Path(__file__).resolve().parents[2]
PROJECT_ROOT = Path(__file__).resolve().parents[3]
for path in (BASE_DIR, SERVICES_DIR, PROJECT_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.environ["DATABASE_URL"] = "mysql+aiomysql://test:test@127.0.0.1:3306/test_db"

from app.database import Base
from app.models.device import Device, DeviceRecentTelemetrySample, ParameterHealthConfig
from app.services.health_config import HealthConfigService
from app.services.dashboard import DashboardService
from services.shared.tenant_context import TenantContext


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


def _tenant_ctx(tenant_id: str) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        user_id="tester",
        role="org_admin",
        plant_ids=[],
        is_super_admin=False,
    )


@pytest.mark.asyncio
async def test_dashboard_bootstrap_uses_tenant_scoped_health_score(monkeypatch: pytest.MonkeyPatch, session_factory):
    async with session_factory() as session:
        session.add_all(
            [
                Device(
                    device_id="TENANT-A-DEVICE",
                    tenant_id="TENANT-A",
                    plant_id="PLANT-1",
                    device_name="Shared A",
                    device_type="compressor",
                    data_source_type="metered",
                ),
                Device(
                    device_id="TENANT-B-DEVICE",
                    tenant_id="TENANT-B",
                    plant_id="PLANT-1",
                    device_name="Shared B",
                    device_type="compressor",
                    data_source_type="metered",
                ),
                ParameterHealthConfig(
                    device_id="TENANT-A-DEVICE",
                    tenant_id="TENANT-A",
                    parameter_name="current",
                    normal_min=8.0,
                    normal_max=18.0,
                    weight=100.0,
                    ignore_zero_value=False,
                    is_active=True,
                ),
                ParameterHealthConfig(
                    device_id="TENANT-B-DEVICE",
                    tenant_id="TENANT-B",
                    parameter_name="current",
                    normal_min=50.0,
                    normal_max=60.0,
                    weight=100.0,
                    ignore_zero_value=False,
                    is_active=True,
                ),
                DeviceRecentTelemetrySample(
                    device_id="TENANT-A-DEVICE",
                    tenant_id="TENANT-A",
                    sample_ts=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
                    projection_version=1,
                    runtime_status="running",
                    load_state="idle",
                    telemetry_json=json.dumps(
                        {
                            "timestamp": "2026-04-04T12:00:00+00:00",
                            "device_id": "TENANT-A-DEVICE",
                            "current": 12.0,
                        }
                    ),
                ),
                DeviceRecentTelemetrySample(
                    device_id="TENANT-B-DEVICE",
                    tenant_id="TENANT-B",
                    sample_ts=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
                    projection_version=1,
                    runtime_status="running",
                    load_state="idle",
                    telemetry_json=json.dumps(
                        {
                            "timestamp": "2026-04-04T12:00:00+00:00",
                            "device_id": "TENANT-B-DEVICE",
                            "current": 12.0,
                        }
                    ),
                ),
            ]
        )
        await session.commit()
        monkeypatch.setattr(
            "app.services.shift.ShiftService.get_shifts_by_device",
            AsyncMock(return_value=[]),
        )
        monkeypatch.setattr(
            "app.services.shift.ShiftService.calculate_uptime",
            AsyncMock(return_value={"uptime_percentage": 91.0}),
        )
        monkeypatch.setattr(
            "app.services.device_property.DevicePropertyService.get_dashboard_widget_config",
            AsyncMock(return_value={"selected_fields": []}),
        )
        monkeypatch.setattr(
            "app.services.idle_running.IdleRunningService.get_idle_config",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(
            "app.services.idle_running.IdleRunningService.get_current_state",
            AsyncMock(return_value={"state": "idle"}),
        )
        monkeypatch.setattr(
            "app.services.idle_running.IdleRunningService.get_idle_stats",
            AsyncMock(return_value={"today": None, "month": None}),
        )
        monkeypatch.setattr(
            "app.services.idle_running.IdleRunningService.get_waste_config",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(
            "app.services.dashboard.DashboardService.get_device_loss_stats_from_device",
            AsyncMock(return_value={"today": {"overconsumption_kwh": 1.2, "total_loss_kwh": 2.4}}),
        )

        payload_a = await DashboardService(session, _tenant_ctx("TENANT-A")).get_dashboard_bootstrap(
            "TENANT-A-DEVICE",
            "TENANT-A",
        )
        payload_b = await DashboardService(session, _tenant_ctx("TENANT-B")).get_dashboard_bootstrap(
            "TENANT-B-DEVICE",
            "TENANT-B",
        )

    assert payload_a["health_score"] is not None
    assert payload_b["health_score"] is not None
    assert payload_a["health_score"]["health_score"] is not None
    assert payload_b["health_score"]["health_score"] is not None
    assert payload_a["health_score"]["health_score"] != payload_b["health_score"]["health_score"]
    assert payload_a["loss_stats"]["today"]["overconsumption_kwh"] == 1.2
    assert payload_b["loss_stats"]["today"]["total_loss_kwh"] == 2.4


@pytest.mark.asyncio
async def test_dashboard_bootstrap_uses_generic_health_scoring_contract(monkeypatch: pytest.MonkeyPatch, session_factory):
    async with session_factory() as session:
        session.add_all(
            [
                Device(
                    device_id="GENERIC-DEVICE",
                    tenant_id="TENANT-A",
                    plant_id="PLANT-1",
                    device_name="Generic",
                    device_type="compressor",
                    data_source_type="metered",
                ),
                ParameterHealthConfig(
                    device_id="GENERIC-DEVICE",
                    tenant_id="TENANT-A",
                    parameter_name="temperature",
                    normal_min=30.0,
                    normal_max=60.0,
                    weight=70.0,
                    ignore_zero_value=False,
                    is_active=True,
                ),
                ParameterHealthConfig(
                    device_id="GENERIC-DEVICE",
                    tenant_id="TENANT-A",
                    parameter_name="vibration",
                    normal_min=0.0,
                    normal_max=3.0,
                    weight=30.0,
                    ignore_zero_value=False,
                    is_active=True,
                ),
                DeviceRecentTelemetrySample(
                    device_id="GENERIC-DEVICE",
                    tenant_id="TENANT-A",
                    sample_ts=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
                    projection_version=1,
                    runtime_status="running",
                    load_state="idle",
                    telemetry_json=json.dumps(
                        {
                            "timestamp": "2026-04-04T12:00:00+00:00",
                            "device_id": "GENERIC-DEVICE",
                            "temperature": 44.0,
                            "vibration": 1.2,
                        }
                    ),
                ),
            ]
        )
        await session.commit()
        monkeypatch.setattr(
            "app.services.shift.ShiftService.get_shifts_by_device",
            AsyncMock(return_value=[]),
        )
        monkeypatch.setattr(
            "app.services.shift.ShiftService.calculate_uptime",
            AsyncMock(return_value={"uptime_percentage": 91.0}),
        )
        monkeypatch.setattr(
            "app.services.device_property.DevicePropertyService.get_dashboard_widget_config",
            AsyncMock(return_value={"selected_fields": []}),
        )
        monkeypatch.setattr(
            "app.services.idle_running.IdleRunningService.get_idle_config",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(
            "app.services.idle_running.IdleRunningService.get_current_state",
            AsyncMock(return_value={"state": "idle"}),
        )
        monkeypatch.setattr(
            "app.services.idle_running.IdleRunningService.get_idle_stats",
            AsyncMock(return_value={"today": None, "month": None}),
        )
        monkeypatch.setattr(
            "app.services.idle_running.IdleRunningService.get_waste_config",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(
            "app.services.dashboard.DashboardService.get_device_loss_stats_from_device",
            AsyncMock(return_value={"today": {"overconsumption_kwh": 0.0, "total_loss_kwh": 0.0}}),
        )

        payload = await DashboardService(session, _tenant_ctx("TENANT-A")).get_dashboard_bootstrap(
            "GENERIC-DEVICE",
            "TENANT-A",
        )
        direct = await HealthConfigService(session).calculate_health_score(
            device_id="GENERIC-DEVICE",
            tenant_id="TENANT-A",
            machine_state="RUNNING",
            telemetry_values={"temperature": 44.0, "vibration": 1.2},
        )

    assert payload["health_score"] is not None
    assert payload["health_score"]["health_score"] == direct["health_score"]
    assert payload["health_score"]["parameter_scores"] == direct["parameter_scores"]
