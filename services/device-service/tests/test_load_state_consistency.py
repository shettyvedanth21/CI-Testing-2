from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import os
import sys

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

os.environ.setdefault("DATABASE_URL", "mysql+aiomysql://test:test@127.0.0.1:3306/test_db")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

from app.database import Base
from app.models.device import Device, DeviceLatestTelemetrySnapshot, DeviceLiveState, TELEMETRY_TIMEOUT_SECONDS
from app.services.dashboard import DashboardService
from app.services.idle_running import IdleRunningService
from app.services.live_dashboard import LiveDashboardService
from app.services.live_projection import LiveProjectionService
from services.shared.tenant_context import TenantContext


def _tenant_ctx(tenant_id: str = "ORG-1") -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        user_id="tester",
        role="system",
        plant_ids=[],
        is_super_admin=False,
    )


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


async def _seed_running_idle_device(
    session,
    *,
    device_id: str = "DEVICE-1",
    tenant_id: str = "ORG-1",
    first_telemetry_timestamp: datetime | None = None,
) -> datetime:
    now = datetime.now(timezone.utc)
    session.add(
        Device(
            device_id=device_id,
            tenant_id=tenant_id,
            plant_id="PLANT-1",
            device_name="Machine 1",
            device_type="compressor",
            location="Plant 1",
            full_load_current_a=20.0,
            idle_threshold_pct_of_fla=0.25,
            idle_current_threshold=5.0,
            last_seen_timestamp=now,
            first_telemetry_timestamp=first_telemetry_timestamp or (now - timedelta(minutes=5)),
        )
    )
    session.add(
        DeviceLiveState(
            device_id=device_id,
            tenant_id=tenant_id,
            runtime_status="running",
            load_state="idle",
            last_telemetry_ts=now,
            last_sample_ts=now,
            last_current_a=1.25,
            last_voltage_v=230.0,
            version=7,
        )
    )
    await session.commit()
    return now


async def _seed_latest_snapshot(
    session,
    *,
    device_id: str = "DEVICE-1",
    tenant_id: str = "ORG-1",
    sample_ts: datetime,
    runtime_status: str = "running",
    load_state: str = "idle",
    current_band: str = "idle",
    last_power_kw: float | None = 0.25,
    last_current_a: float | None = 1.25,
    last_voltage_v: float | None = 230.0,
    numeric_fields_json: str = '{"current": 1.25, "power": 250.0, "voltage": 230.0}',
    source_fields_json: str = '{"current_field": "current", "power_field": "power", "voltage_field": "voltage"}',
) -> None:
    session.add(
        DeviceLatestTelemetrySnapshot(
            device_id=device_id,
            tenant_id=tenant_id,
            sample_ts=sample_ts,
            projection_version=7,
            snapshot_version=1,
            runtime_status=runtime_status,
            load_state=load_state,
            current_band=current_band,
            last_power_kw=last_power_kw,
            last_current_a=last_current_a,
            last_voltage_v=last_voltage_v,
            numeric_fields_json=numeric_fields_json,
            source_fields_json=source_fields_json,
            normalization_version="v1",
        )
    )
    await session.commit()


@pytest.mark.asyncio
async def test_current_state_uses_projection_and_snapshot_without_raw_history_read(session_factory, monkeypatch):
    async with session_factory() as session:
        now = await _seed_running_idle_device(session)
        await _seed_latest_snapshot(session, sample_ts=now)

        async def fail_fetch_telemetry(self, device_id: str, **kwargs):
            raise AssertionError("current-state should not read raw telemetry history")

        monkeypatch.setattr(IdleRunningService, "_fetch_telemetry", fail_fetch_telemetry)

        service = IdleRunningService(session, _tenant_ctx())
        state = await service.get_current_state("DEVICE-1", "ORG-1")

        assert state["state"] == "idle"
        assert state["current"] == pytest.approx(1.25)
        assert state["voltage"] == pytest.approx(230.0)
        assert state["current_field"] == "current"
        assert state["voltage_field"] == "voltage"


@pytest.mark.asyncio
async def test_current_state_returns_unknown_when_authoritative_projection_is_stale_without_raw_history_read(session_factory, monkeypatch):
    async with session_factory() as session:
        now = await _seed_running_idle_device(session)
        stale_at = now - timedelta(seconds=TELEMETRY_TIMEOUT_SECONDS + 5)
        live_state = await session.get(DeviceLiveState, {"device_id": "DEVICE-1", "tenant_id": "ORG-1"})
        assert live_state is not None
        live_state.last_telemetry_ts = stale_at
        live_state.last_sample_ts = stale_at
        await session.commit()
        await _seed_latest_snapshot(session, sample_ts=stale_at)

        async def fail_fetch_telemetry(self, device_id: str, **kwargs):
            raise AssertionError("current-state should not read raw telemetry history")

        monkeypatch.setattr(IdleRunningService, "_fetch_telemetry", fail_fetch_telemetry)

        service = IdleRunningService(session, _tenant_ctx())
        state = await service.get_current_state("DEVICE-1", "ORG-1")

        assert state["state"] == "unknown"
        assert state["current"] == pytest.approx(1.25)
        assert state["voltage"] == pytest.approx(230.0)


@pytest.mark.asyncio
async def test_current_state_uses_snapshot_numeric_values_when_live_projection_scalars_are_missing(session_factory, monkeypatch):
    async with session_factory() as session:
        now = await _seed_running_idle_device(session)
        live_state = await session.get(DeviceLiveState, {"device_id": "DEVICE-1", "tenant_id": "ORG-1"})
        assert live_state is not None
        live_state.last_current_a = None
        live_state.last_voltage_v = None
        live_state.load_state = "running"
        await session.commit()
        await _seed_latest_snapshot(
            session,
            sample_ts=now,
            runtime_status="running",
            load_state="running",
            current_band="in_load",
            last_power_kw=4.2,
            last_current_a=12.4,
            last_voltage_v=231.0,
            numeric_fields_json='{"current": 12.4, "power": 4200.0, "voltage": 231.0}',
            source_fields_json='{"current_field": "phase_current", "power_field": "power", "voltage_field": "voltage"}',
        )

        async def fail_fetch_telemetry(self, device_id: str, **kwargs):
            raise AssertionError("current-state should not read raw telemetry history")

        monkeypatch.setattr(IdleRunningService, "_fetch_telemetry", fail_fetch_telemetry)

        service = IdleRunningService(session, _tenant_ctx())
        state = await service.get_current_state("DEVICE-1", "ORG-1")

        assert state["state"] == "running"
        assert state["current_band"] == "in_load"
        assert state["current"] == pytest.approx(12.4)
        assert state["voltage"] == pytest.approx(231.0)
        assert state["current_field"] == "phase_current"
        assert state["voltage_field"] == "voltage"


@pytest.mark.asyncio
async def test_dashboard_bootstrap_uses_authoritative_live_projection_for_current_state(session_factory, monkeypatch):
    async with session_factory() as session:
        now = await _seed_running_idle_device(session)

        async def fake_fetch_telemetry(self, device_id: str, **kwargs):
            return [{"timestamp": now.isoformat(), "power": 250.0}]

        async def fake_http_get_json(self, service_key: str, url: str, params=None, tenant_id=None):
            return {
                "data": {
                    "items": [{"timestamp": now.isoformat(), "power": 250.0}],
                }
            }, None

        async def fake_idle_stats(self, device_id: str, tenant_id: str):
            return {
                "device_id": device_id,
                "today": {"idle_minutes": 12},
                "month": {"idle_minutes": 40},
                "tariff_configured": False,
                "pf_estimated": False,
                "threshold_configured": True,
                "idle_current_threshold": 5.0,
                "data_source_type": "metered",
            }

        monkeypatch.setattr(IdleRunningService, "_fetch_telemetry", fake_fetch_telemetry)
        monkeypatch.setattr(IdleRunningService, "get_idle_stats", fake_idle_stats)
        monkeypatch.setattr(DashboardService, "_http_get_json", fake_http_get_json)

        payload = await DashboardService(session, _tenant_ctx()).get_dashboard_bootstrap("DEVICE-1", "ORG-1")

        assert payload["version"] == 7
        assert payload["current_state"]["state"] == "idle"
        assert payload["current_state"]["current"] == pytest.approx(1.25)
        assert payload["current_state"]["voltage"] == pytest.approx(230.0)
        assert payload["device"].first_telemetry_timestamp is not None


@pytest.mark.asyncio
async def test_materialized_fleet_snapshot_uses_live_projection_load_state(session_factory):
    async with session_factory() as session:
        now = await _seed_running_idle_device(session)

        payload = await DashboardService(session, _tenant_ctx())._build_fleet_state_snapshot()

        assert payload["devices"][0]["device_id"] == "DEVICE-1"
        assert payload["devices"][0]["runtime_status"] == "running"
        assert payload["devices"][0]["load_state"] == "idle"
        assert payload["devices"][0]["operational_status"] == "idle"
        assert payload["devices"][0]["first_telemetry_timestamp"] is not None
        assert payload["devices"][0]["last_seen_timestamp"].startswith(now.replace(tzinfo=None).isoformat())


@pytest.mark.asyncio
async def test_materialized_fleet_snapshot_marks_stale_projection_stopped(session_factory):
    async with session_factory() as session:
        now = await _seed_running_idle_device(session)
        stale_at = now - timedelta(seconds=TELEMETRY_TIMEOUT_SECONDS + 5)
        live_state = await session.get(DeviceLiveState, {"device_id": "DEVICE-1", "tenant_id": "ORG-1"})
        assert live_state is not None
        live_state.last_telemetry_ts = stale_at
        live_state.last_sample_ts = stale_at
        live_state.runtime_status = "running"
        live_state.load_state = "idle"
        await session.commit()

        payload = await DashboardService(session, _tenant_ctx())._build_fleet_state_snapshot()

        assert payload["devices"][0]["runtime_status"] == "stopped"
        assert payload["devices"][0]["load_state"] == "unknown"
        assert payload["devices"][0]["operational_status"] == "stopped"
        assert payload["devices"][0]["first_telemetry_timestamp"] is not None


@pytest.mark.asyncio
async def test_live_dashboard_snapshot_marks_stale_projection_stopped(session_factory):
    async with session_factory() as session:
        now = await _seed_running_idle_device(session)
        stale_at = now - timedelta(seconds=TELEMETRY_TIMEOUT_SECONDS + 5)
        live_state = await session.get(DeviceLiveState, {"device_id": "DEVICE-1", "tenant_id": "ORG-1"})
        assert live_state is not None
        live_state.last_telemetry_ts = stale_at
        live_state.last_sample_ts = stale_at
        live_state.runtime_status = "running"
        live_state.load_state = "idle"
        await session.commit()

        payload = await LiveDashboardService(session, _tenant_ctx()).get_fleet_snapshot(tenant_id="ORG-1")

        assert payload["devices"][0]["runtime_status"] == "stopped"
        assert payload["devices"][0]["load_state"] == "unknown"
        assert payload["devices"][0]["operational_status"] == "stopped"
        assert payload["devices"][0]["first_telemetry_timestamp"] is not None


@pytest.mark.asyncio
async def test_live_projection_snapshot_item_marks_stale_projection_stopped(session_factory):
    async with session_factory() as session:
        now = await _seed_running_idle_device(session)
        stale_at = now - timedelta(seconds=TELEMETRY_TIMEOUT_SECONDS + 5)
        live_state = await session.get(DeviceLiveState, {"device_id": "DEVICE-1", "tenant_id": "ORG-1"})
        assert live_state is not None
        live_state.last_telemetry_ts = stale_at
        live_state.last_sample_ts = stale_at
        live_state.runtime_status = "running"
        live_state.load_state = "idle"
        await session.commit()

        payload = await LiveProjectionService(session, _tenant_ctx()).get_device_snapshot_item("DEVICE-1", "ORG-1")

        assert payload["runtime_status"] == "stopped"
        assert payload["load_state"] == "unknown"
        assert payload["first_telemetry_timestamp"] is not None


@pytest.mark.asyncio
async def test_live_dashboard_snapshot_filters_by_operational_status(session_factory):
    async with session_factory() as session:
        now = datetime.now(timezone.utc)
        session.add_all(
            [
                Device(
                    device_id="OVER-DEVICE",
                    tenant_id="ORG-1",
                    plant_id="PLANT-1",
                    device_name="Over Device",
                    device_type="compressor",
                    location="Plant 1",
                    full_load_current_a=20.0,
                    idle_threshold_pct_of_fla=0.25,
                    last_seen_timestamp=now,
                    first_telemetry_timestamp=now - timedelta(minutes=10),
                ),
                DeviceLiveState(
                    device_id="OVER-DEVICE",
                    tenant_id="ORG-1",
                    runtime_status="running",
                    load_state="overconsumption",
                    last_telemetry_ts=now,
                    last_sample_ts=now,
                    last_current_a=25.0,
                    last_voltage_v=230.0,
                    version=9,
                ),
            ]
        )
        await session.commit()

        payload = await LiveDashboardService(session, _tenant_ctx()).get_fleet_snapshot(
            tenant_id="ORG-1",
            operational_status_filter="overconsumption",
        )

        assert [device["device_id"] for device in payload["devices"]] == ["OVER-DEVICE"]
        assert payload["devices"][0]["operational_status"] == "overconsumption"
