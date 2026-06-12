from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import os
import sys

import pytest
import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from importlib.util import module_from_spec, spec_from_file_location
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

BASE_DIR = Path(__file__).resolve().parents[1]
SERVICES_DIR = Path(__file__).resolve().parents[2]
PROJECT_ROOT = Path(__file__).resolve().parents[3]
for path in (BASE_DIR, SERVICES_DIR, PROJECT_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.environ.setdefault("DATABASE_URL", "mysql+aiomysql://test:test@127.0.0.1:3306/test_db")

from app.database import Base
from app.models.device import Device, DeviceLiveState, DeviceShift
from app.services import live_projection as live_projection_module
from app.services.idle_running import IdleRunningService
from app.services.live_projection import LiveProjectionService
from app.services.load_thresholds import classify_current_band, resolve_device_thresholds
from services.shared.energy_accounting import split_loss_components
from services.shared.tenant_context import TenantContext

_migration_path = BASE_DIR / "alembic" / "versions" / "20260414_0001_fla_threshold_redesign.py"
_migration_spec = spec_from_file_location("device_service_fla_migration", _migration_path)
assert _migration_spec is not None and _migration_spec.loader is not None
migration_module = module_from_spec(_migration_spec)
_migration_spec.loader.exec_module(migration_module)


def _tenant_ctx() -> TenantContext:
    return TenantContext(
        tenant_id="TENANT-A",
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


def test_threshold_derivation_uses_default_pct():
    device = SimpleNamespace(full_load_current_a=20.0, idle_threshold_pct_of_fla=0.25)
    thresholds = resolve_device_thresholds(device)

    assert thresholds.configured is True
    assert thresholds.derived_idle_threshold_a == pytest.approx(5.0)
    assert thresholds.derived_overconsumption_threshold_a == pytest.approx(20.0)


def test_threshold_derivation_uses_custom_pct():
    device = SimpleNamespace(full_load_current_a=20.0, idle_threshold_pct_of_fla=0.4)
    thresholds = resolve_device_thresholds(device)

    assert thresholds.derived_idle_threshold_a == pytest.approx(8.0)
    assert thresholds.derived_overconsumption_threshold_a == pytest.approx(20.0)


def test_current_band_logic_exposes_overconsumption_state_explicitly():
    thresholds = resolve_device_thresholds(SimpleNamespace(full_load_current_a=20.0, idle_threshold_pct_of_fla=0.25))

    assert IdleRunningService.detect_device_state_with_thresholds(0.0, 230.0, thresholds) == "unloaded"
    assert classify_current_band(0.0, 230.0, thresholds) == "unloaded"

    assert IdleRunningService.detect_device_state_with_thresholds(4.0, 230.0, thresholds) == "idle"
    assert classify_current_band(4.0, 230.0, thresholds) == "idle"

    assert IdleRunningService.detect_device_state_with_thresholds(10.0, 230.0, thresholds) == "running"
    assert classify_current_band(10.0, 230.0, thresholds) == "in_load"

    assert IdleRunningService.detect_device_state_with_thresholds(25.0, 230.0, thresholds) == "overconsumption"
    assert classify_current_band(25.0, 230.0, thresholds) == "overconsumption"


def test_overconsumption_split_uses_measured_interval_ratio_only():
    idle_kwh, offhours_kwh, over_kwh = split_loss_components(
        duration_sec=60.0,
        interval_energy_kwh=0.2,
        current_a=25.0,
        voltage_v=230.0,
        pf=None,
        idle_threshold=5.0,
        over_threshold=20.0,
        inside_shift=True,
        power_kw=None,
    )

    assert idle_kwh == 0.0
    assert offhours_kwh == 0.0
    assert over_kwh == pytest.approx(0.04)


def test_migration_backfills_fla_from_legacy_over_threshold_only():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE devices (
                    device_id VARCHAR(50) PRIMARY KEY,
                    overconsumption_current_threshold_a NUMERIC(10,4) NULL,
                    idle_current_threshold NUMERIC(10,4) NULL
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO devices (device_id, overconsumption_current_threshold_a, idle_current_threshold)
                VALUES
                    ('DEVICE-A', 18.5000, 4.0000),
                    ('DEVICE-B', NULL, 6.0000)
                """
            )
        )

        ctx = MigrationContext.configure(conn)
        migration_module.op = Operations(ctx)
        migration_module.upgrade()

        rows = conn.execute(
            text(
                """
                SELECT device_id, full_load_current_a, idle_threshold_pct_of_fla
                FROM devices
                ORDER BY device_id
                """
            )
        ).mappings().all()

    assert float(rows[0]["full_load_current_a"]) == pytest.approx(18.5)
    assert float(rows[0]["idle_threshold_pct_of_fla"]) == pytest.approx(0.25)
    assert rows[1]["full_load_current_a"] is None
    assert float(rows[1]["idle_threshold_pct_of_fla"]) == pytest.approx(0.25)


@pytest.mark.asyncio
async def test_legacy_waste_config_write_populates_fla(session_factory):
    async with session_factory() as session:
        session.add(
            Device(
                device_id="LEGACY-WASTE",
                tenant_id="TENANT-A",
                plant_id="PLANT-1",
                device_name="Legacy Waste",
                device_type="compressor",
            )
        )
        await session.commit()

        service = IdleRunningService(session, _tenant_ctx())
        payload = await service.set_waste_config(
            device_id="LEGACY-WASTE",
            tenant_id="TENANT-A",
            overconsumption_current_threshold_a=20.0,
            unoccupied_weekday_start_time=None,
            unoccupied_weekday_end_time=None,
            unoccupied_weekend_start_time=None,
            unoccupied_weekend_end_time=None,
        )

        assert payload["full_load_current_a"] == pytest.approx(20.0)
        assert payload["derived_overconsumption_threshold_a"] == pytest.approx(20.0)
        assert payload["overconsumption_current_threshold_a"] == pytest.approx(20.0)


@pytest.mark.asyncio
async def test_current_state_exposes_additive_overconsumption_band(session_factory, monkeypatch):
    async with session_factory() as session:
        session.add(
            Device(
                device_id="BAND-DEVICE",
                tenant_id="TENANT-A",
                plant_id="PLANT-1",
                device_name="Band Device",
                device_type="compressor",
                full_load_current_a=20.0,
            )
        )
        session.add(
            DeviceLiveState(
                device_id="BAND-DEVICE",
                tenant_id="TENANT-A",
                runtime_status="running",
                load_state="running",
                last_telemetry_ts=datetime.now(timezone.utc),
                last_current_a=25.0,
                last_voltage_v=230.0,
                version=1,
            )
        )
        await session.commit()

        service = IdleRunningService(session, _tenant_ctx())
        monkeypatch.setattr(IdleRunningService, "_fetch_telemetry", AsyncMock(return_value=[]))

        payload = await service.get_current_state("BAND-DEVICE", "TENANT-A")

        assert payload["state"] == "overconsumption"
        assert payload["current_band"] == "overconsumption"
        assert payload["derived_idle_threshold_a"] == pytest.approx(5.0)
        assert payload["derived_overconsumption_threshold_a"] == pytest.approx(20.0)


@pytest.mark.asyncio
async def test_live_projection_uses_derived_thresholds_for_loss_booking(session_factory, monkeypatch):
    async with session_factory() as session:
        session.add(
            Device(
                device_id="FLA-PROJECTION",
                tenant_id="TENANT-A",
                plant_id="PLANT-1",
                device_name="FLA Projection",
                device_type="compressor",
                full_load_current_a=20.0,
                idle_threshold_pct_of_fla=0.05,
                created_at=datetime(2026, 4, 12, 0, 0, 0),
            )
        )
        session.add(
            DeviceShift(
                device_id="FLA-PROJECTION",
                tenant_id="TENANT-A",
                shift_name="Always On",
                shift_start=datetime(2026, 4, 12, 0, 0, 0).time(),
                shift_end=datetime(2026, 4, 12, 23, 59, 0).time(),
                maintenance_break_minutes=0,
                day_of_week=None,
                is_active=True,
            )
        )
        await session.commit()

        service = LiveProjectionService(session)
        service._health = SimpleNamespace(calculate_health_score=AsyncMock(return_value={"health_score": None}))
        monkeypatch.setattr(live_projection_module.TariffCache, "get", AsyncMock(return_value={"rate": 0.0, "currency": "INR"}))

        first_ts = datetime.now(timezone.utc)
        second_ts = first_ts.replace(microsecond=0)
        second_ts = second_ts.replace(second=(second_ts.second + 10) % 60)
        if second_ts <= first_ts:
            from datetime import timedelta

            second_ts = first_ts + timedelta(seconds=10)
        await service.apply_live_update(
            device_id="FLA-PROJECTION",
            tenant_id="TENANT-A",
            telemetry_payload={"timestamp": first_ts.isoformat(), "current": 25.0, "voltage": 230.0, "energy_kwh": 100.0},
            dynamic_fields={"current": 25.0, "voltage": 230.0, "energy_kwh": 100.0},
        )
        item = await service.apply_live_update(
            device_id="FLA-PROJECTION",
            tenant_id="TENANT-A",
            telemetry_payload={"timestamp": second_ts.isoformat(), "current": 25.0, "voltage": 230.0, "energy_kwh": 100.2},
            dynamic_fields={"current": 25.0, "voltage": 230.0, "energy_kwh": 100.2},
        )

        assert item["load_state"] == "overconsumption"
        assert item["current_band"] == "overconsumption"

        live_state = await session.get(DeviceLiveState, {"device_id": "FLA-PROJECTION", "tenant_id": "TENANT-A"})
        assert live_state is not None
        assert float(live_state.today_overconsumption_kwh or 0.0) == pytest.approx(0.04)
        assert float(live_state.today_energy_kwh or 0.0) == pytest.approx(0.2)
        assert float(live_state.today_idle_kwh or 0.0) == 0.0
