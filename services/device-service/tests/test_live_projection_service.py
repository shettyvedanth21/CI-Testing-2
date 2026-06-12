from datetime import datetime, time, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace
import os
import sys
from zoneinfo import ZoneInfo
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

BASE_DIR = Path(__file__).resolve().parents[1]
SERVICES_DIR = Path(__file__).resolve().parents[2]
PROJECT_ROOT = Path(__file__).resolve().parents[3]
for path in (BASE_DIR, SERVICES_DIR, PROJECT_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.environ["DATABASE_URL"] = "mysql+aiomysql://test:test@127.0.0.1:3306/test_db"

from app.services.live_projection import LiveProjectionService
from app.database import Base
from app.models import Device, DeviceLatestTelemetrySnapshot, DeviceLiveState, DeviceRecentTelemetrySample
from app.models.device import DeviceShift
from app.models.device import ParameterHealthConfig
from app.services.device import DeviceService
from services.shared.telemetry_normalization import compute_interval_energy_delta, normalize_telemetry_sample

IST = ZoneInfo("Asia/Kolkata")


class _DeleteCaptureSession:
    def __init__(self):
        self.statements = []
        self.commit = AsyncMock()

    async def execute(self, statement):
        self.statements.append(statement)

        class _Result:
            rowcount = 1

        return _Result()


async def _create_projection_device(session, *, device_id: str, **overrides):
    device = Device(
        device_id=device_id,
        tenant_id="ORG-1",
        plant_id="PLANT-1",
        device_name=device_id,
        device_type="compressor",
        created_at=datetime(2026, 4, 9, 10, 0, 0),
        **overrides,
    )
    session.add(device)
    await session.commit()
    return device


def test_power_kw_from_payload_prefers_kw():
    payload = {"kw": 12.5, "power": 9999}
    assert LiveProjectionService._power_kw_from_payload(payload, mapped_power=None) == 12.5


def test_power_kw_from_payload_falls_back_to_watts():
    payload = {"power": 2200}
    assert LiveProjectionService._power_kw_from_payload(payload, mapped_power=None) == 2.2


def test_is_inside_shift_cross_midnight():
    shift = SimpleNamespace(
        is_active=True,
        shift_start=time(22, 0),
        shift_end=time(6, 0),
        day_of_week=0,  # Monday
    )
    monday_23 = datetime(2026, 3, 23, 23, 0, tzinfo=IST)
    tuesday_02 = datetime(2026, 3, 24, 2, 0, tzinfo=IST)
    assert LiveProjectionService._is_inside_shift(monday_23, [shift]) is True
    assert LiveProjectionService._is_inside_shift(tuesday_02, [shift]) is True


def test_as_utc_normalizes_naive_datetime():
    naive = datetime(2026, 3, 19, 12, 0, 0)
    normalized = LiveProjectionService._as_utc(naive)
    assert normalized is not None
    assert normalized.tzinfo is not None
    assert normalized.utcoffset().total_seconds() == 0


def test_as_utc_preserves_aware_datetime_in_utc():
    aware_ist = datetime(2026, 3, 19, 17, 30, 0, tzinfo=IST)
    normalized = LiveProjectionService._as_utc(aware_ist)
    assert normalized is not None
    assert normalized.tzinfo is not None
    assert normalized.utcoffset().total_seconds() == 0
    assert normalized.hour == 12
    assert normalized.minute == 0


@pytest.mark.asyncio
async def test_recompute_clears_health_when_no_active_configs():
    session = AsyncMock()
    session.execute = AsyncMock(
        side_effect=[
            SimpleNamespace(scalar_one_or_none=lambda: 4),
            SimpleNamespace(rowcount=1),
        ]
    )
    session.expire_all = MagicMock()
    service = LiveProjectionService(session)
    state = SimpleNamespace(
        health_score=87.3,
        uptime_percentage=95.0,
        today_uptime_percentage=95.0,
        current_shift_uptime_percentage=95.0,
        version=4,
    )

    service._get_or_create_state = AsyncMock(return_value=state)
    service._health = SimpleNamespace(
        get_health_configs_by_device=AsyncMock(return_value=[]),
        calculate_health_score=AsyncMock(),
    )
    service._shift = SimpleNamespace(calculate_uptime=AsyncMock(return_value={"uptime_percentage": 88.2}))
    service.get_device_snapshot_item = AsyncMock(return_value={"device_id": "M-1", "health_score": None, "version": 5})

    result = await service.recompute_after_configuration_change("M-1", "ORG-1")

    assert state.health_score is None
    assert state.uptime_percentage == 95.0
    assert state.today_uptime_percentage == 95.0
    assert state.current_shift_uptime_percentage == 88.2
    assert state.version == 4
    service._health.calculate_health_score.assert_not_called()
    session.commit.assert_awaited_once()
    session.expire_all.assert_called_once()
    assert result["health_score"] is None


@pytest.mark.asyncio
async def test_recompute_clears_health_when_telemetry_missing():
    session = AsyncMock()
    session.execute = AsyncMock(
        side_effect=[
            SimpleNamespace(scalar_one_or_none=lambda: 2),
            SimpleNamespace(rowcount=1),
        ]
    )
    session.expire_all = MagicMock()
    service = LiveProjectionService(session)
    state = SimpleNamespace(
        health_score=71.4,
        uptime_percentage=82.0,
        today_uptime_percentage=82.0,
        current_shift_uptime_percentage=82.0,
        version=2,
    )

    service._get_or_create_state = AsyncMock(return_value=state)
    service._health = SimpleNamespace(
        get_health_configs_by_device=AsyncMock(return_value=[SimpleNamespace(is_active=True)]),
        calculate_health_score=AsyncMock(),
    )
    service._shift = SimpleNamespace(calculate_uptime=AsyncMock(return_value={"uptime_percentage": 84.0}))
    service._fetch_latest_projection_sample = AsyncMock(return_value={})
    service.get_device_snapshot_item = AsyncMock(return_value={"device_id": "M-2", "health_score": None, "version": 3})

    result = await service.recompute_after_configuration_change("M-2", "ORG-1")

    assert state.health_score is None
    assert state.uptime_percentage == 82.0
    assert state.today_uptime_percentage == 82.0
    assert state.current_shift_uptime_percentage == 84.0
    assert state.version == 2
    service._health.calculate_health_score.assert_not_called()
    session.commit.assert_awaited_once()
    session.expire_all.assert_called_once()
    assert result["health_score"] is None


@pytest.mark.asyncio
async def test_incremental_projection_clears_current_shift_uptime_outside_shift():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        device = Device(
            device_id="DEVICE-UPTIME-1",
            tenant_id="ORG-1",
            plant_id="PLANT-1",
            device_name="Device Uptime 1",
            device_type="compressor",
            created_at=datetime(2026, 4, 9, 10, 0, 0),
        )
        shift = DeviceShift(
            device_id="DEVICE-UPTIME-1",
            tenant_id="ORG-1",
            shift_name="Day Shift",
            shift_start=time(9, 30),
            shift_end=time(18, 30),
            maintenance_break_minutes=0,
            day_of_week=None,
            is_active=True,
        )
        state = DeviceLiveState(
            device_id="DEVICE-UPTIME-1",
            tenant_id="ORG-1",
            runtime_status="running",
            load_state="running",
            uptime_percentage=100.0,
            today_uptime_percentage=100.0,
            current_shift_uptime_percentage=15.71,
            last_sample_ts=datetime(2026, 4, 9, 10, 0, 0, tzinfo=timezone.utc),
            last_energy_kwh=10.0,
            last_power_kw=2.5,
            last_current_a=12.0,
            last_voltage_v=230.0,
            today_running_seconds=300,
            today_effective_seconds=300,
            day_bucket=datetime(2026, 4, 9, 0, 0, 0, tzinfo=timezone.utc).astimezone(IST).date(),
            version=1,
        )
        session.add_all([device, shift, state])
        await session.commit()

        service = LiveProjectionService(session)
        service._health = SimpleNamespace(calculate_health_score=AsyncMock(return_value={"health_score": None}))

        async def _fake_tariff_get(_tenant_id):
            return {"configured": False, "rate": 0.0, "currency": "INR"}

        from app.services import live_projection as live_projection_module

        tariff_get_original = live_projection_module.TariffCache.get
        try:
            live_projection_module.TariffCache.get = AsyncMock(side_effect=_fake_tariff_get)
            await service.apply_live_update(
                device_id="DEVICE-UPTIME-1",
                tenant_id="ORG-1",
                telemetry_payload={
                    "timestamp": "2026-04-09T14:05:00+00:00",
                    "current": 12.0,
                    "voltage": 230.0,
                    "power": 2500.0,
                    "energy_kwh": 10.25,
                },
                dynamic_fields={"current": 12.0, "voltage": 230.0, "power": 2500.0, "energy_kwh": 10.25},
            )
        finally:
            live_projection_module.TariffCache.get = tariff_get_original

        refreshed = await session.get(DeviceLiveState, {"device_id": "DEVICE-UPTIME-1", "tenant_id": "ORG-1"})

    assert refreshed is not None
    assert refreshed.today_uptime_percentage == pytest.approx(100.0)
    assert refreshed.uptime_percentage == pytest.approx(100.0)
    assert refreshed.current_shift_uptime_percentage is None
    await engine.dispose()


@pytest.mark.asyncio
async def test_remove_device_projection_uses_composite_key():
    session = _DeleteCaptureSession()
    service = LiveProjectionService(session)

    await service.remove_device_projection("M-3", "ORG-1")

    assert len(session.statements) == 1
    compiled = str(session.statements[0].compile(compile_kwargs={"literal_binds": False}))
    assert "device_live_state" in compiled
    assert "device_id" in compiled
    assert "tenant_id" in compiled
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_apply_live_update_marks_device_running_on_first_sample_without_loss_deltas():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        device = Device(
            device_id="DEVICE-ONLINE-1",
            tenant_id="ORG-1",
            plant_id="PLANT-1",
            device_name="Device Online 1",
            device_type="compressor",
            created_at=datetime(2026, 4, 9, 10, 0, 0),
        )
        session.add(device)
        await session.commit()

        service = LiveProjectionService(session)
        service._health = SimpleNamespace(calculate_health_score=AsyncMock(return_value={"health_score": None}))
        sample_ts_dt = datetime.now(timezone.utc)
        sample_ts = sample_ts_dt.isoformat()

        async def _fake_tariff_get(_tenant_id):
            return {"configured": False, "rate": 0.0, "currency": "INR"}

        from app.services import live_projection as live_projection_module

        tariff_get_original = live_projection_module.TariffCache.get
        try:
            live_projection_module.TariffCache.get = AsyncMock(side_effect=_fake_tariff_get)

            item = await service.apply_live_update(
                device_id="DEVICE-ONLINE-1",
                tenant_id="ORG-1",
                telemetry_payload={
                    "timestamp": sample_ts,
                    "current": 0.0,
                    "voltage": 230.0,
                    "power": 0.0,
                },
                dynamic_fields={"current": 0.0, "voltage": 230.0, "power": 0.0},
            )
        finally:
            live_projection_module.TariffCache.get = tariff_get_original

        assert item["device_id"] == "DEVICE-ONLINE-1"
        assert item["runtime_status"] == "running"
        assert item["first_telemetry_timestamp"] == sample_ts

        live_state = await session.get(DeviceLiveState, {"device_id": "DEVICE-ONLINE-1", "tenant_id": "ORG-1"})
        assert live_state is not None
        assert live_state.runtime_status == "running"
        assert live_state.last_telemetry_ts is not None

        persisted_device = (
            await session.execute(
                select(Device).where(Device.device_id == "DEVICE-ONLINE-1", Device.tenant_id == "ORG-1")
            )
        ).scalar_one()
        assert persisted_device.last_seen_timestamp is not None
        assert persisted_device.first_telemetry_timestamp is not None
        assert persisted_device.first_telemetry_timestamp.replace(tzinfo=timezone.utc).isoformat() == sample_ts

    await engine.dispose()


@pytest.mark.asyncio
async def test_apply_live_update_persists_latest_numeric_snapshot_from_projection_lane():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        await _create_projection_device(session, device_id="SNAPSHOT-DEVICE-1")

        service = LiveProjectionService(session)
        service._health = SimpleNamespace(calculate_health_score=AsyncMock(return_value={"health_score": None}))

        from app.services import live_projection as live_projection_module

        tariff_get_original = live_projection_module.TariffCache.get
        try:
            live_projection_module.TariffCache.get = AsyncMock(return_value={"configured": False, "rate": 0.0, "currency": "INR"})
            await service.apply_live_update(
                device_id="SNAPSHOT-DEVICE-1",
                tenant_id="ORG-1",
                telemetry_payload={
                    "timestamp": "2026-05-01T10:00:00+00:00",
                    "current": 14.2,
                    "voltage": 231.5,
                    "power": 4200,
                    "temperature": 61.3,
                },
                dynamic_fields={
                    "current": 14.2,
                    "voltage": 231.5,
                    "power": 4200,
                    "temperature": 61.3,
                },
            )
        finally:
            live_projection_module.TariffCache.get = tariff_get_original

        snapshot = await session.get(
            DeviceLatestTelemetrySnapshot,
            {"device_id": "SNAPSHOT-DEVICE-1", "tenant_id": "ORG-1"},
        )
        recent_rows = (
            await session.execute(
                select(DeviceRecentTelemetrySample)
                .where(
                    DeviceRecentTelemetrySample.device_id == "SNAPSHOT-DEVICE-1",
                    DeviceRecentTelemetrySample.tenant_id == "ORG-1",
                )
            )
        ).scalars().all()

    assert snapshot is not None
    assert snapshot.sample_ts is not None
    assert snapshot.projection_version >= 1
    assert snapshot.snapshot_version == 1
    assert snapshot.runtime_status == "running"
    assert snapshot.load_state in {"running", "idle", "unloaded", "overconsumption", "unknown"}
    assert float(snapshot.last_power_kw or 0.0) == pytest.approx(4.2, rel=1e-6)
    assert float(snapshot.last_current_a or 0.0) == pytest.approx(14.2, rel=1e-6)
    assert float(snapshot.last_voltage_v or 0.0) == pytest.approx(231.5, rel=1e-6)
    assert isinstance(snapshot.numeric_fields_json, str)
    assert "\"temperature\"" in snapshot.numeric_fields_json
    assert isinstance(snapshot.source_fields_json, str)
    assert "\"power_field\"" in snapshot.source_fields_json
    assert len(recent_rows) == 1
    assert "\"temperature\"" in recent_rows[0].telemetry_json

    await engine.dispose()


@pytest.mark.asyncio
async def test_apply_live_update_keeps_newer_snapshot_when_older_sample_arrives_late():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        await _create_projection_device(session, device_id="SNAPSHOT-DEVICE-2")

        service = LiveProjectionService(session)
        service._health = SimpleNamespace(calculate_health_score=AsyncMock(return_value={"health_score": None}))

        from app.services import live_projection as live_projection_module

        tariff_get_original = live_projection_module.TariffCache.get
        try:
            live_projection_module.TariffCache.get = AsyncMock(return_value={"configured": False, "rate": 0.0, "currency": "INR"})
            await service.apply_live_update(
                device_id="SNAPSHOT-DEVICE-2",
                tenant_id="ORG-1",
                telemetry_payload={
                    "timestamp": "2026-05-01T10:05:00+00:00",
                    "current": 15.0,
                    "voltage": 232.0,
                    "power": 5000,
                },
                dynamic_fields={"current": 15.0, "voltage": 232.0, "power": 5000},
            )
            await service.apply_live_update(
                device_id="SNAPSHOT-DEVICE-2",
                tenant_id="ORG-1",
                telemetry_payload={
                    "timestamp": "2026-05-01T10:01:00+00:00",
                    "current": 3.0,
                    "voltage": 210.0,
                    "power": 900,
                },
                dynamic_fields={"current": 3.0, "voltage": 210.0, "power": 900},
            )
        finally:
            live_projection_module.TariffCache.get = tariff_get_original

        snapshot = await session.get(
            DeviceLatestTelemetrySnapshot,
            {"device_id": "SNAPSHOT-DEVICE-2", "tenant_id": "ORG-1"},
        )

    assert snapshot is not None
    assert snapshot.sample_ts is not None
    assert snapshot.sample_ts.replace(tzinfo=timezone.utc).isoformat() == "2026-05-01T10:05:00+00:00"
    assert float(snapshot.last_power_kw or 0.0) == pytest.approx(5.0, rel=1e-6)
    assert float(snapshot.last_current_a or 0.0) == pytest.approx(15.0, rel=1e-6)
    assert float(snapshot.last_voltage_v or 0.0) == pytest.approx(232.0, rel=1e-6)

    await engine.dispose()


@pytest.mark.asyncio
async def test_apply_live_update_bounds_recent_telemetry_buffer_per_device():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        await _create_projection_device(session, device_id="SNAPSHOT-DEVICE-3")

        service = LiveProjectionService(session)
        service._health = SimpleNamespace(calculate_health_score=AsyncMock(return_value={"health_score": None}))

        from app.services import live_projection as live_projection_module

        tariff_get_original = live_projection_module.TariffCache.get
        try:
            live_projection_module.TariffCache.get = AsyncMock(return_value={"configured": False, "rate": 0.0, "currency": "INR"})
            for index in range(205):
                await service.apply_live_update(
                    device_id="SNAPSHOT-DEVICE-3",
                    tenant_id="ORG-1",
                    telemetry_payload={
                        "timestamp": f"2026-05-01T10:{index // 60:02d}:{index % 60:02d}+00:00",
                        "current": 10.0 + index,
                        "voltage": 230.0,
                        "power": 1000 + index,
                    },
                    dynamic_fields={"current": 10.0 + index, "voltage": 230.0, "power": 1000 + index},
                )
        finally:
            live_projection_module.TariffCache.get = tariff_get_original

        recent_rows = (
            await session.execute(
                select(DeviceRecentTelemetrySample)
                .where(
                    DeviceRecentTelemetrySample.device_id == "SNAPSHOT-DEVICE-3",
                    DeviceRecentTelemetrySample.tenant_id == "ORG-1",
                )
                .order_by(DeviceRecentTelemetrySample.sample_ts.desc(), DeviceRecentTelemetrySample.id.desc())
            )
        ).scalars().all()

    assert len(recent_rows) == 200
    assert recent_rows[0].sample_ts is not None
    assert recent_rows[-1].sample_ts is not None
    assert recent_rows[0].sample_ts > recent_rows[-1].sample_ts

    await engine.dispose()


@pytest.mark.asyncio
async def test_apply_live_update_ignores_future_sample_without_mutating_runtime_truth():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        await _create_projection_device(session, device_id="DEVICE-FUTURE-1")
        service = LiveProjectionService(session)
        service._health = SimpleNamespace(calculate_health_score=AsyncMock(return_value={"health_score": None}))
        future_ts = datetime.now(timezone.utc) + timedelta(minutes=10)

        item = await service.apply_live_update(
            device_id="DEVICE-FUTURE-1",
            tenant_id="ORG-1",
            telemetry_payload={
                "timestamp": future_ts.isoformat(),
                "power": 100.0,
                "current": 0.5,
                "voltage": 230.0,
            },
        )

        live_state = await session.get(DeviceLiveState, {"device_id": "DEVICE-FUTURE-1", "tenant_id": "ORG-1"})
        device = (
            await session.execute(
                select(Device).where(Device.device_id == "DEVICE-FUTURE-1", Device.tenant_id == "ORG-1")
            )
        ).scalar_one()

    assert item["runtime_status"] == "stopped"
    assert item["last_seen_timestamp"] is None
    assert item["first_telemetry_timestamp"] is None
    assert live_state is not None
    assert live_state.version == 0
    assert live_state.last_telemetry_ts is None
    assert device.last_seen_timestamp is None
    assert device.first_telemetry_timestamp is None
    await engine.dispose()


@pytest.mark.asyncio
async def test_apply_live_update_duplicate_timestamp_is_idempotent():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        await _create_projection_device(session, device_id="DEVICE-DUPLICATE-1")
        service = LiveProjectionService(session)
        service._health = SimpleNamespace(calculate_health_score=AsyncMock(return_value={"health_score": None}))

        from app.services import live_projection as live_projection_module

        tariff_get_original = live_projection_module.TariffCache.get
        try:
            live_projection_module.TariffCache.get = AsyncMock(return_value={"configured": False, "rate": 0.0, "currency": "INR"})
            sample_ts = datetime.now(timezone.utc).replace(microsecond=0)
            first = await service.apply_live_update(
                device_id="DEVICE-DUPLICATE-1",
                tenant_id="ORG-1",
                telemetry_payload={
                    "timestamp": sample_ts.isoformat(),
                    "power": 460.0,
                    "current": 2.0,
                    "voltage": 230.0,
                },
            )
            second = await service.apply_live_update(
                device_id="DEVICE-DUPLICATE-1",
                tenant_id="ORG-1",
                telemetry_payload={
                    "timestamp": sample_ts.isoformat(),
                    "power": 920.0,
                    "current": 4.0,
                    "voltage": 230.0,
                },
            )
        finally:
            live_projection_module.TariffCache.get = tariff_get_original

        live_state = await session.get(DeviceLiveState, {"device_id": "DEVICE-DUPLICATE-1", "tenant_id": "ORG-1"})

    assert first["runtime_status"] == "running"
    assert second["runtime_status"] == "running"
    assert second["last_seen_timestamp"].startswith(sample_ts.strftime("%Y-%m-%dT%H:%M:%S"))
    assert first["last_seen_timestamp"].startswith(sample_ts.strftime("%Y-%m-%dT%H:%M:%S"))
    assert second["version"] == first["version"]
    assert live_state is not None
    assert live_state.version == first["version"]
    assert float(live_state.today_energy_kwh or 0.0) == pytest.approx(0.0, abs=1e-9)
    await engine.dispose()


@pytest.mark.asyncio
async def test_apply_live_update_restores_running_after_device_was_marked_stale():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        await _create_projection_device(session, device_id="DEVICE-RECONNECT-1")
        service = LiveProjectionService(session)
        service._health = SimpleNamespace(calculate_health_score=AsyncMock(return_value={"health_score": None}))

        from app.services import live_projection as live_projection_module

        tariff_get_original = live_projection_module.TariffCache.get
        try:
            live_projection_module.TariffCache.get = AsyncMock(return_value={"configured": False, "rate": 0.0, "currency": "INR"})
            first_ts = datetime.now(timezone.utc).replace(microsecond=0)
            await service.apply_live_update(
                device_id="DEVICE-RECONNECT-1",
                tenant_id="ORG-1",
                telemetry_payload={
                    "timestamp": first_ts.isoformat(),
                    "power": 0.0,
                    "current": 0.0,
                    "voltage": 230.0,
                },
            )

            live_state = await session.get(DeviceLiveState, {"device_id": "DEVICE-RECONNECT-1", "tenant_id": "ORG-1"})
            device = (
                await session.execute(
                    select(Device).where(Device.device_id == "DEVICE-RECONNECT-1", Device.tenant_id == "ORG-1")
                )
            ).scalar_one()
            stale_ts = datetime.now(timezone.utc) - timedelta(seconds=61)
            assert live_state is not None
            live_state.last_telemetry_ts = stale_ts
            live_state.last_sample_ts = stale_ts
            device.last_seen_timestamp = stale_ts
            await session.commit()

            stale_snapshot = await service.get_device_snapshot_item("DEVICE-RECONNECT-1", "ORG-1")
            recovered_ts = datetime.now(timezone.utc).replace(microsecond=0)
            recovered = await service.apply_live_update(
                device_id="DEVICE-RECONNECT-1",
                tenant_id="ORG-1",
                telemetry_payload={
                    "timestamp": recovered_ts.isoformat(),
                    "power": 575.0,
                    "current": 2.5,
                    "voltage": 230.0,
                },
            )
        finally:
            live_projection_module.TariffCache.get = tariff_get_original

    assert stale_snapshot["runtime_status"] == "stopped"
    assert recovered["runtime_status"] == "running"
    assert recovered["last_seen_timestamp"] is not None
    await engine.dispose()


@pytest.mark.asyncio
async def test_apply_live_update_rejects_implausible_counter_jump_and_uses_power_fallback():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        await _create_projection_device(session, device_id="DEVICE-ANOMALY-1")
        service = LiveProjectionService(session)
        service._health = SimpleNamespace(calculate_health_score=AsyncMock(return_value={"health_score": None}))

        from app.services import live_projection as live_projection_module

        tariff_get_original = live_projection_module.TariffCache.get
        try:
            live_projection_module.TariffCache.get = AsyncMock(return_value={"configured": False, "rate": 0.0, "currency": "INR"})

            await service.apply_live_update(
                device_id="DEVICE-ANOMALY-1",
                tenant_id="ORG-1",
                telemetry_payload={"timestamp": "2026-04-14T00:00:00+00:00", "power": 8744.0, "energy_kwh": 0.0},
            )
            item = await service.apply_live_update(
                device_id="DEVICE-ANOMALY-1",
                tenant_id="ORG-1",
                telemetry_payload={"timestamp": "2026-04-14T00:00:20+00:00", "power": 8744.0, "energy_kwh": 8.9},
            )
        finally:
            live_projection_module.TariffCache.get = tariff_get_original

        live_state = await session.get(DeviceLiveState, {"device_id": "DEVICE-ANOMALY-1", "tenant_id": "ORG-1"})

    assert item["energy_debug"]["energy_method"] == "power_integration"
    assert item["energy_debug"]["reason_code"] == "fallback_measured_power"
    assert item["energy_debug"]["quality_class"] == "estimated"
    assert live_state is not None
    assert float(live_state.today_energy_kwh or 0.0) == pytest.approx(8744.0 * 20.0 / 3600.0 / 1000.0, abs=1e-6)
    await engine.dispose()


@pytest.mark.asyncio
async def test_apply_live_update_accepts_healthy_monotonic_counter_and_matches_shared_basis():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        device = await _create_projection_device(session, device_id="DEVICE-COUNTER-1")
        service = LiveProjectionService(session)
        service._health = SimpleNamespace(calculate_health_score=AsyncMock(return_value={"health_score": None}))

        from app.services import live_projection as live_projection_module

        tariff_get_original = live_projection_module.TariffCache.get
        try:
            live_projection_module.TariffCache.get = AsyncMock(return_value={"configured": False, "rate": 0.0, "currency": "INR"})

            await service.apply_live_update(
                device_id="DEVICE-COUNTER-1",
                tenant_id="ORG-1",
                telemetry_payload={"timestamp": "2026-04-14T00:00:00+00:00", "power": 9000.0, "energy_kwh": 10.0},
            )
            item = await service.apply_live_update(
                device_id="DEVICE-COUNTER-1",
                tenant_id="ORG-1",
                telemetry_payload={"timestamp": "2026-04-14T00:05:00+00:00", "power": 9000.0, "energy_kwh": 10.75},
            )
        finally:
            live_projection_module.TariffCache.get = tariff_get_original

        expected = compute_interval_energy_delta(
            normalize_telemetry_sample({"timestamp": "2026-04-14T00:00:00+00:00", "power": 9000.0, "energy_kwh": 10.0}, device),
            normalize_telemetry_sample({"timestamp": "2026-04-14T00:05:00+00:00", "power": 9000.0, "energy_kwh": 10.75}, device),
            max_counter_gap_seconds=300.0,
            max_fallback_gap_seconds=300.0,
        )
        live_state = await session.get(DeviceLiveState, {"device_id": "DEVICE-COUNTER-1", "tenant_id": "ORG-1"})

    assert item["energy_debug"]["energy_method"] == "counter"
    assert item["energy_debug"]["reason_code"] == "counter_accepted"
    assert live_state is not None
    assert float(live_state.today_energy_kwh or 0.0) == pytest.approx(expected.business_energy_delta_kwh, rel=1e-6)
    await engine.dispose()


@pytest.mark.asyncio
async def test_apply_live_update_uses_power_fallback_for_flat_counter_idle_inside_shift():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        await _create_projection_device(
            session,
            device_id="DEVICE-FLAT-IDLE-1",
            full_load_current_a=20.0,
            idle_threshold_pct_of_fla=0.25,
        )
        session.add(
            DeviceShift(
                device_id="DEVICE-FLAT-IDLE-1",
                tenant_id="ORG-1",
                shift_name="Day Shift",
                shift_start=time(9, 30),
                shift_end=time(18, 30),
                maintenance_break_minutes=0,
                day_of_week=None,
                is_active=True,
            )
        )
        await session.commit()

        service = LiveProjectionService(session)
        service._health = SimpleNamespace(calculate_health_score=AsyncMock(return_value={"health_score": None}))

        from app.services import live_projection as live_projection_module

        tariff_get_original = live_projection_module.TariffCache.get
        try:
            live_projection_module.TariffCache.get = AsyncMock(return_value={"configured": False, "rate": 0.0, "currency": "INR"})

            await service.apply_live_update(
                device_id="DEVICE-FLAT-IDLE-1",
                tenant_id="ORG-1",
                telemetry_payload={
                    "timestamp": "2026-05-18T07:00:00+00:00",
                    "current": 0.1,
                    "voltage": 243.0,
                    "power": 67.5,
                    "energy_kwh": 7.4,
                },
            )
            item = await service.apply_live_update(
                device_id="DEVICE-FLAT-IDLE-1",
                tenant_id="ORG-1",
                telemetry_payload={
                    "timestamp": "2026-05-18T07:00:30+00:00",
                    "current": 0.1,
                    "voltage": 243.0,
                    "power": 67.5,
                    "energy_kwh": 7.4,
                },
            )
        finally:
            live_projection_module.TariffCache.get = tariff_get_original

        live_state = await session.get(DeviceLiveState, {"device_id": "DEVICE-FLAT-IDLE-1", "tenant_id": "ORG-1"})

    expected_kwh = 67.5 * 30.0 / 3600.0 / 1000.0
    assert item["energy_debug"]["energy_method"] == "power_integration"
    assert item["energy_debug"]["reason_code"] == "fallback_measured_power"
    assert live_state is not None
    assert float(live_state.today_idle_kwh or 0.0) == pytest.approx(expected_kwh, abs=1e-6)
    assert float(live_state.today_offhours_kwh or 0.0) == 0.0
    assert float(live_state.today_loss_kwh or 0.0) == pytest.approx(expected_kwh, abs=1e-6)
    await engine.dispose()


@pytest.mark.asyncio
async def test_apply_live_update_uses_power_fallback_for_flat_counter_offhours_outside_shift():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        await _create_projection_device(
            session,
            device_id="DEVICE-FLAT-OFF-1",
            full_load_current_a=20.0,
            idle_threshold_pct_of_fla=0.25,
        )
        session.add(
            DeviceShift(
                device_id="DEVICE-FLAT-OFF-1",
                tenant_id="ORG-1",
                shift_name="Day Shift",
                shift_start=time(9, 30),
                shift_end=time(18, 30),
                maintenance_break_minutes=0,
                day_of_week=None,
                is_active=True,
            )
        )
        await session.commit()

        service = LiveProjectionService(session)
        service._health = SimpleNamespace(calculate_health_score=AsyncMock(return_value={"health_score": None}))

        from app.services import live_projection as live_projection_module

        tariff_get_original = live_projection_module.TariffCache.get
        try:
            live_projection_module.TariffCache.get = AsyncMock(return_value={"configured": False, "rate": 0.0, "currency": "INR"})

            await service.apply_live_update(
                device_id="DEVICE-FLAT-OFF-1",
                tenant_id="ORG-1",
                telemetry_payload={
                    "timestamp": "2026-05-18T13:10:00+00:00",
                    "current": 0.1,
                    "voltage": 243.0,
                    "power": 67.5,
                    "energy_kwh": 7.4,
                },
            )
            item = await service.apply_live_update(
                device_id="DEVICE-FLAT-OFF-1",
                tenant_id="ORG-1",
                telemetry_payload={
                    "timestamp": "2026-05-18T13:10:30+00:00",
                    "current": 0.1,
                    "voltage": 243.0,
                    "power": 67.5,
                    "energy_kwh": 7.4,
                },
            )
        finally:
            live_projection_module.TariffCache.get = tariff_get_original

        live_state = await session.get(DeviceLiveState, {"device_id": "DEVICE-FLAT-OFF-1", "tenant_id": "ORG-1"})

    expected_kwh = 67.5 * 30.0 / 3600.0 / 1000.0
    assert item["energy_debug"]["energy_method"] == "power_integration"
    assert item["energy_debug"]["reason_code"] == "fallback_measured_power"
    assert live_state is not None
    assert float(live_state.today_idle_kwh or 0.0) == 0.0
    assert float(live_state.today_offhours_kwh or 0.0) == pytest.approx(expected_kwh, abs=1e-6)
    assert float(live_state.today_loss_kwh or 0.0) == pytest.approx(expected_kwh, abs=1e-6)
    await engine.dispose()


@pytest.mark.asyncio
async def test_apply_live_update_reset_and_long_gap_do_not_inflate_totals():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        await _create_projection_device(session, device_id="DEVICE-GAP-1")
        service = LiveProjectionService(session)
        service._health = SimpleNamespace(calculate_health_score=AsyncMock(return_value={"health_score": None}))

        from app.services import live_projection as live_projection_module

        tariff_get_original = live_projection_module.TariffCache.get
        try:
            live_projection_module.TariffCache.get = AsyncMock(return_value={"configured": False, "rate": 0.0, "currency": "INR"})

            await service.apply_live_update(
                device_id="DEVICE-GAP-1",
                tenant_id="ORG-1",
                telemetry_payload={"timestamp": "2026-04-14T00:00:00+00:00", "power": 5000.0, "energy_kwh": 18.0},
            )
            reset_item = await service.apply_live_update(
                device_id="DEVICE-GAP-1",
                tenant_id="ORG-1",
                telemetry_payload={"timestamp": "2026-04-14T00:05:00+00:00", "power": 5000.0, "energy_kwh": 0.05},
            )
            gap_item = await service.apply_live_update(
                device_id="DEVICE-GAP-1",
                tenant_id="ORG-1",
                telemetry_payload={"timestamp": "2026-04-14T01:10:00+00:00", "power": 5000.0, "energy_kwh": 1.0},
            )
        finally:
            live_projection_module.TariffCache.get = tariff_get_original

        live_state = await session.get(DeviceLiveState, {"device_id": "DEVICE-GAP-1", "tenant_id": "ORG-1"})

    assert reset_item["energy_debug"]["energy_method"] == "power_integration"
    assert gap_item["energy_debug"]["energy_method"] == "none"
    assert gap_item["energy_debug"]["quality_class"] == "gap_exceeded"
    assert live_state is not None
    assert float(live_state.today_energy_kwh or 0.0) == pytest.approx(5000.0 * 300.0 / 3600.0 / 1000.0, rel=1e-6)
    await engine.dispose()


@pytest.mark.asyncio
async def test_apply_live_update_signed_power_and_negative_pf_do_not_inflate_energy():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        await _create_projection_device(session, device_id="DEVICE-SIGNED-1")
        service = LiveProjectionService(session)
        service._health = SimpleNamespace(calculate_health_score=AsyncMock(return_value={"health_score": None}))
        config = {"polarity_mode": "inverted", "energy_flow_mode": "consumption_only"}

        from app.services import live_projection as live_projection_module

        tariff_get_original = live_projection_module.TariffCache.get
        try:
            live_projection_module.TariffCache.get = AsyncMock(return_value={"configured": False, "rate": 0.0, "currency": "INR"})

            first_payload = {
                "timestamp": "2026-04-14T00:00:00+00:00",
                "power": -1500.0,
                "current": -6.0,
                "voltage": 230.0,
                "power_factor": -0.9,
                "energy_kwh": 2.0,
            }
            await service.apply_live_update(
                device_id="DEVICE-SIGNED-1",
                tenant_id="ORG-1",
                telemetry_payload=first_payload,
                normalized_fields=normalize_telemetry_sample(first_payload, config).to_dict(),
            )
            second_payload = {
                "timestamp": "2026-04-14T00:05:00+00:00",
                "power": -1500.0,
                "current": -6.0,
                "voltage": 230.0,
                "power_factor": -0.9,
                "energy_kwh": 2.25,
            }
            item = await service.apply_live_update(
                device_id="DEVICE-SIGNED-1",
                tenant_id="ORG-1",
                telemetry_payload=second_payload,
                normalized_fields=normalize_telemetry_sample(second_payload, config).to_dict(),
            )
        finally:
            live_projection_module.TariffCache.get = tariff_get_original

        live_state = await session.get(DeviceLiveState, {"device_id": "DEVICE-SIGNED-1", "tenant_id": "ORG-1"})

    assert item["energy_debug"]["energy_method"] == "power_integration"
    assert item["energy_debug"]["reason_code"] == "fallback_measured_power"
    assert live_state is not None
    assert float(live_state.today_energy_kwh or 0.0) == pytest.approx(0.125, rel=1e-6)
    assert float(live_state.last_power_kw or 0.0) == pytest.approx(1.5, rel=1e-6)
    await engine.dispose()


@pytest.mark.asyncio
async def test_apply_live_update_handles_missing_tariff_without_crashing():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        device = Device(
            device_id="DEVICE-NO-TARIFF-1",
            tenant_id="ORG-1",
            plant_id="PLANT-1",
            device_name="Device No Tariff 1",
            device_type="compressor",
            created_at=datetime(2026, 4, 9, 10, 0, 0),
        )
        session.add(device)
        await session.commit()

        service = LiveProjectionService(session)
        service._health = SimpleNamespace(calculate_health_score=AsyncMock(return_value={"health_score": None}))

        from app.services import live_projection as live_projection_module

        tariff_get_original = live_projection_module.TariffCache.get
        try:
            live_projection_module.TariffCache.get = AsyncMock(return_value=None)

            item = await service.apply_live_update(
                device_id="DEVICE-NO-TARIFF-1",
                tenant_id="ORG-1",
                telemetry_payload={
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "current": 0.0,
                    "voltage": 230.0,
                    "power": 0.0,
                },
                dynamic_fields={"current": 0.0, "voltage": 230.0, "power": 0.0},
            )
        finally:
            live_projection_module.TariffCache.get = tariff_get_original

        assert item["device_id"] == "DEVICE-NO-TARIFF-1"
        assert item["runtime_status"] == "running"
        assert item["first_telemetry_timestamp"] is not None

        live_state = await session.get(DeviceLiveState, {"device_id": "DEVICE-NO-TARIFF-1", "tenant_id": "ORG-1"})
        assert live_state is not None
        assert live_state.today_loss_cost_inr == 0
        assert live_state.month_energy_cost_inr == 0

    await engine.dispose()


@pytest.mark.asyncio
async def test_apply_live_update_passes_idle_machine_state_to_health_service():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        device = Device(
            device_id="DEVICE-IDLE-1",
            tenant_id="ORG-1",
            plant_id="PLANT-1",
            device_name="Device Idle 1",
            device_type="compressor",
            full_load_current_a=20.0,
            created_at=datetime(2026, 4, 9, 10, 0, 0),
        )
        session.add(device)
        await session.commit()

        service = LiveProjectionService(session)
        health_mock = AsyncMock(return_value={"health_score": 77.0})
        service._health = SimpleNamespace(calculate_health_score=health_mock)

        from app.services import live_projection as live_projection_module

        tariff_get_original = live_projection_module.TariffCache.get
        try:
            live_projection_module.TariffCache.get = AsyncMock(return_value={"configured": False, "rate": 0.0, "currency": "INR"})
            await service.apply_live_update(
                device_id="DEVICE-IDLE-1",
                tenant_id="ORG-1",
                telemetry_payload={
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "current": 3.0,
                    "voltage": 230.0,
                    "power": 0.5,
                },
                dynamic_fields={"current": 3.0, "voltage": 230.0, "power": 0.5},
            )
        finally:
            live_projection_module.TariffCache.get = tariff_get_original

        assert health_mock.await_args.kwargs["machine_state"] == "IDLE"

    await engine.dispose()


@pytest.mark.asyncio
async def test_apply_live_update_passes_unload_machine_state_to_health_service():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        device = Device(
            device_id="DEVICE-UNLOAD-1",
            tenant_id="ORG-1",
            plant_id="PLANT-1",
            device_name="Device Unload 1",
            device_type="compressor",
            full_load_current_a=20.0,
            created_at=datetime(2026, 4, 9, 10, 0, 0),
        )
        session.add(device)
        await session.commit()

        service = LiveProjectionService(session)
        health_mock = AsyncMock(return_value={"health_score": 55.0})
        service._health = SimpleNamespace(calculate_health_score=health_mock)

        from app.services import live_projection as live_projection_module

        tariff_get_original = live_projection_module.TariffCache.get
        try:
            live_projection_module.TariffCache.get = AsyncMock(return_value={"configured": False, "rate": 0.0, "currency": "INR"})
            await service.apply_live_update(
                device_id="DEVICE-UNLOAD-1",
                tenant_id="ORG-1",
                telemetry_payload={
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "current": 0.0,
                    "voltage": 230.0,
                    "power": 0.0,
                },
                dynamic_fields={"current": 0.0, "voltage": 230.0, "power": 0.0},
            )
        finally:
            live_projection_module.TariffCache.get = tariff_get_original

        assert health_mock.await_args.kwargs["machine_state"] == "UNLOAD"

    await engine.dispose()


@pytest.mark.asyncio
async def test_apply_live_update_keeps_first_telemetry_timestamp_on_later_samples():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        device = Device(
            device_id="DEVICE-ONLINE-2",
            tenant_id="ORG-1",
            plant_id="PLANT-1",
            device_name="Device Online 2",
            device_type="compressor",
            created_at=datetime(2026, 4, 9, 10, 0, 0),
        )
        session.add(device)
        await session.commit()

        service = LiveProjectionService(session)
        service._health = SimpleNamespace(calculate_health_score=AsyncMock(return_value={"health_score": None}))

        from app.services import live_projection as live_projection_module

        tariff_get_original = live_projection_module.TariffCache.get
        try:
            live_projection_module.TariffCache.get = AsyncMock(return_value={"configured": False, "rate": 0.0, "currency": "INR"})
            first_ts_dt = datetime.now(timezone.utc)
            second_ts_dt = first_ts_dt + timedelta(minutes=5)
            first_ts = first_ts_dt.isoformat()
            second_ts = second_ts_dt.isoformat()

            await service.apply_live_update(
                device_id="DEVICE-ONLINE-2",
                tenant_id="ORG-1",
                telemetry_payload={
                    "timestamp": first_ts,
                    "current": 0.0,
                    "voltage": 230.0,
                    "power": 0.0,
                },
                dynamic_fields={"current": 0.0, "voltage": 230.0, "power": 0.0},
            )
            await service.apply_live_update(
                device_id="DEVICE-ONLINE-2",
                tenant_id="ORG-1",
                telemetry_payload={
                    "timestamp": second_ts,
                    "current": 0.0,
                    "voltage": 230.0,
                    "power": 0.0,
                },
                dynamic_fields={"current": 0.0, "voltage": 230.0, "power": 0.0},
            )
        finally:
            live_projection_module.TariffCache.get = tariff_get_original

        persisted_device = (
            await session.execute(
                select(Device).where(Device.device_id == "DEVICE-ONLINE-2", Device.tenant_id == "ORG-1")
            )
        ).scalar_one()
        assert persisted_device.first_telemetry_timestamp is not None
        assert persisted_device.first_telemetry_timestamp.replace(tzinfo=timezone.utc).isoformat() == first_ts

    await engine.dispose()


@pytest.mark.asyncio
async def test_backfill_first_telemetry_timestamps_uses_earliest_post_onboarding_sample():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        device = Device(
            device_id="DEVICE-BACKFILL-1",
            tenant_id="ORG-1",
            plant_id="PLANT-1",
            device_name="Backfill Device",
            device_type="compressor",
            created_at=datetime(2026, 4, 9, 10, 0, 0),
        )
        other = Device(
            device_id="DEVICE-BACKFILL-2",
            tenant_id="ORG-1",
            plant_id="PLANT-1",
            device_name="Empty Device",
            device_type="compressor",
            created_at=datetime(2026, 4, 9, 10, 0, 0),
        )
        session.add_all([device, other])
        await session.commit()

        service = LiveProjectionService(session)

        async def fake_fetch_earliest(self, device_id: str, tenant_id: str, *, start_time, timeout_sec=10.0):
            if device_id == "DEVICE-BACKFILL-1":
                return {"timestamp": "2026-04-09T10:03:00+00:00"}
            return {}

        service._fetch_earliest_telemetry = fake_fetch_earliest.__get__(service, LiveProjectionService)

        summary = await service.backfill_first_telemetry_timestamps(max_devices=10)

        assert summary["scanned"] == 2
        assert summary["repaired"] == 1
        assert summary["repaired_device_ids"] == ["DEVICE-BACKFILL-1"]

        repaired = (
            await session.execute(
                select(Device).where(Device.device_id == "DEVICE-BACKFILL-1", Device.tenant_id == "ORG-1")
            )
        ).scalar_one()
        empty = (
            await session.execute(
                select(Device).where(Device.device_id == "DEVICE-BACKFILL-2", Device.tenant_id == "ORG-1")
            )
        ).scalar_one()

        assert repaired.first_telemetry_timestamp is not None
        assert repaired.first_telemetry_timestamp.replace(tzinfo=timezone.utc).isoformat() == "2026-04-09T10:03:00+00:00"
        assert empty.first_telemetry_timestamp is None

    await engine.dispose()


@pytest.mark.asyncio
async def test_update_last_seen_does_not_mutate_first_telemetry_timestamp():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        device = Device(
            device_id="DEVICE-HEARTBEAT-1",
            tenant_id="ORG-1",
            plant_id="PLANT-1",
            device_name="Heartbeat Device",
            device_type="compressor",
            created_at=datetime(2026, 4, 9, 10, 0, 0, tzinfo=timezone.utc),
            first_telemetry_timestamp=datetime(2026, 4, 9, 10, 3, 0, tzinfo=timezone.utc),
        )
        session.add(device)
        await session.commit()

        service = DeviceService(session, None)
        updated = await service.update_last_seen("DEVICE-HEARTBEAT-1", "ORG-1")
        assert updated is not None

        persisted_device = (
            await session.execute(
                select(Device).where(Device.device_id == "DEVICE-HEARTBEAT-1", Device.tenant_id == "ORG-1")
            )
        ).scalar_one()

        assert persisted_device.first_telemetry_timestamp is not None
        assert persisted_device.first_telemetry_timestamp.replace(tzinfo=timezone.utc) == datetime(
            2026,
            4,
            9,
            10,
            3,
            0,
            tzinfo=timezone.utc,
        )
        assert persisted_device.last_seen_timestamp is not None

    await engine.dispose()


@pytest.mark.asyncio
async def test_apply_live_update_scores_generic_telemetry_fields():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        device = Device(
            device_id="DEVICE-GENERIC-1",
            tenant_id="ORG-1",
            plant_id="PLANT-1",
            device_name="Generic Device",
            device_type="compressor",
        )
        session.add_all(
            [
                device,
                ParameterHealthConfig(
                    device_id="DEVICE-GENERIC-1",
                    tenant_id="ORG-1",
                    parameter_name="temperature",
                    normal_min=30.0,
                    normal_max=60.0,
                    weight=60.0,
                    ignore_zero_value=False,
                    is_active=True,
                ),
                ParameterHealthConfig(
                    device_id="DEVICE-GENERIC-1",
                    tenant_id="ORG-1",
                    parameter_name="vibration",
                    normal_min=0.0,
                    normal_max=3.0,
                    weight=40.0,
                    ignore_zero_value=False,
                    is_active=True,
                ),
            ]
        )
        await session.commit()

        service = LiveProjectionService(session)

        async def _fake_tariff_get(_tenant_id):
            return {"configured": False, "rate": 0.0, "currency": "INR"}

        from app.services import live_projection as live_projection_module

        tariff_get_original = live_projection_module.TariffCache.get
        try:
            live_projection_module.TariffCache.get = AsyncMock(side_effect=_fake_tariff_get)

            item = await service.apply_live_update(
                device_id="DEVICE-GENERIC-1",
                tenant_id="ORG-1",
                telemetry_payload={
                    "timestamp": "2026-04-04T12:00:00+00:00",
                    "temperature": 45.0,
                    "vibration": 1.4,
                    "current": 8.5,
                    "voltage": 228.0,
                    "power": 1200.0,
                },
                dynamic_fields={
                    "temperature": 45.0,
                    "vibration": 1.4,
                    "current": 8.5,
                    "voltage": 228.0,
                    "power": 1200.0,
                },
            )
        finally:
            live_projection_module.TariffCache.get = tariff_get_original

        direct = await service._health.calculate_health_score(
            device_id="DEVICE-GENERIC-1",
            tenant_id="ORG-1",
            machine_state="RUNNING",
            telemetry_values={
                "temperature": 45.0,
                "vibration": 1.4,
                "current": 8.5,
                "voltage": 228.0,
                "power": 1200.0,
            },
        )

        live_state = await session.get(DeviceLiveState, {"device_id": "DEVICE-GENERIC-1", "tenant_id": "ORG-1"})

    assert item["health_score"] == direct["health_score"]
    assert live_state is not None
    assert float(live_state.health_score or 0.0) == pytest.approx(float(direct["health_score"] or 0.0), rel=1e-6)
    await engine.dispose()


@pytest.mark.asyncio
async def test_reconcile_recent_projections_reports_repaired_device_ids():
    state = SimpleNamespace(device_id="DEVICE-REPAIR-1", tenant_id="ORG-1", last_sample_ts=None)
    session = AsyncMock()
    session.execute = AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [state])))

    service = LiveProjectionService(
        session,
        SimpleNamespace(tenant_id="ORG-1", require_tenant=lambda: "ORG-1"),
    )
    service._fetch_latest_projection_sample = AsyncMock(
        return_value={"timestamp": "2026-04-04T12:00:00+00:00", "power": 0.0}
    )
    service.apply_live_update = AsyncMock()

    summary = await service.reconcile_recent_projections(max_devices=10)

    assert summary["scanned"] == 1
    assert summary["repaired"] == 1
    assert summary["repaired_device_ids"] == ["DEVICE-REPAIR-1"]
    service.apply_live_update.assert_awaited_once()


@pytest.mark.asyncio
async def test_reconcile_recent_projections_returns_zero_repairs_when_latest_telemetry_missing():
    state = SimpleNamespace(
        device_id="DEVICE-NO-LATEST-1",
        tenant_id="ORG-1",
        last_sample_ts=None,
        last_telemetry_ts=None,
    )
    session = AsyncMock()
    session.execute = AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [state])))

    service = LiveProjectionService(
        session,
        SimpleNamespace(tenant_id="ORG-1", require_tenant=lambda: "ORG-1"),
    )
    service._fetch_latest_projection_sample = AsyncMock(return_value={})
    service.apply_live_update = AsyncMock()
    service._reconcile_timed_out_intervals_for_device = AsyncMock(return_value={"closed_intervals": 0})

    summary = await service.reconcile_recent_projections(max_devices=10)

    assert summary["scanned"] == 1
    assert summary["repaired"] == 0
    assert summary["repaired_device_ids"] == []
    assert summary["closed_intervals"] == 0
    session.rollback.assert_awaited_once()
    service.apply_live_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconcile_recent_projections_uses_mysql_recent_sample_without_data_service():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add_all(
            [
                Device(
                    device_id="DEVICE-MYSQL-REPAIR",
                    tenant_id="ORG-1",
                    plant_id="PLANT-1",
                    device_name="MySQL Repair Device",
                    device_type="compressor",
                ),
                DeviceLiveState(
                    device_id="DEVICE-MYSQL-REPAIR",
                    tenant_id="ORG-1",
                    last_sample_ts=datetime(2026, 4, 4, 11, 55, tzinfo=timezone.utc),
                    last_telemetry_ts=datetime(2026, 4, 4, 11, 55, tzinfo=timezone.utc),
                    runtime_status="running",
                    load_state="running",
                ),
                DeviceRecentTelemetrySample(
                    device_id="DEVICE-MYSQL-REPAIR",
                    tenant_id="ORG-1",
                    sample_ts=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
                    projection_version=3,
                    runtime_status="running",
                    load_state="running",
                    current_band="normal",
                    telemetry_json=json.dumps(
                        {
                            "timestamp": "2026-04-04T12:00:00+00:00",
                            "device_id": "DEVICE-MYSQL-REPAIR",
                            "power": 1200.0,
                            "current": 8.0,
                        }
                    ),
                ),
            ]
        )
        await session.commit()

        service = LiveProjectionService(session)
        service._fetch_latest_telemetry = AsyncMock(side_effect=AssertionError("data-service must not be called"))
        service.apply_live_update = AsyncMock()
        service._reconcile_timed_out_intervals_for_device = AsyncMock(return_value={"closed_intervals": 0})

        summary = await service.reconcile_recent_projections(max_devices=10)

    assert summary["scanned"] == 1
    assert summary["repaired"] == 1
    assert summary["repaired_device_ids"] == ["DEVICE-MYSQL-REPAIR"]
    service.apply_live_update.assert_awaited_once()
    service._fetch_latest_telemetry.assert_not_called()

    await engine.dispose()


@pytest.mark.asyncio
async def test_get_device_snapshot_item_raises_for_unknown_device():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        service = LiveProjectionService(session)

        with pytest.raises(ValueError, match="Device 'MISSING-DEVICE' not found"):
            await service.get_device_snapshot_item("MISSING-DEVICE", "ORG-1")

    await engine.dispose()


@pytest.mark.asyncio
async def test_recompute_today_loss_projection_rebuilds_overconsumption_from_threshold(monkeypatch):
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        device = Device(
            device_id="DEVICE-LOSS-1",
            tenant_id="ORG-1",
            plant_id="PLANT-1",
            device_name="Device Loss 1",
            device_type="compressor",
            full_load_current_a=20.0,
        )
        shift = DeviceShift(
            device_id="DEVICE-LOSS-1",
            tenant_id="ORG-1",
            shift_name="Always On",
            shift_start=time(0, 0),
            shift_end=time(23, 59),
            maintenance_break_minutes=0,
            day_of_week=None,
            is_active=True,
        )
        state = DeviceLiveState(
            device_id="DEVICE-LOSS-1",
            tenant_id="ORG-1",
            runtime_status="running",
            load_state="running",
            version=0,
        )
        session.add_all([device, shift, state])
        await session.commit()

        service = LiveProjectionService(session)

        async def fake_tariff_get(_tenant_id):
            return {"configured": True, "rate": 5.0, "currency": "INR"}

        async def fake_window(*_args, **_kwargs):
            first_ts = datetime.now(timezone.utc)
            second_ts = first_ts + timedelta(minutes=5)
            return [
                {"timestamp": first_ts.isoformat(), "current": 25.0, "voltage": 230.0, "power": 5750.0},
                {"timestamp": second_ts.isoformat(), "current": 25.0, "voltage": 230.0, "power": 5750.0},
            ]

        from app.services import live_projection as live_projection_module

        tariff_get_original = live_projection_module.TariffCache.get
        try:
            live_projection_module.TariffCache.get = AsyncMock(side_effect=fake_tariff_get)
            service._fetch_telemetry_window = AsyncMock(side_effect=fake_window)

            await service.recompute_today_loss_projection("DEVICE-LOSS-1", "ORG-1")
        finally:
            live_projection_module.TariffCache.get = tariff_get_original

        refreshed = await session.get(DeviceLiveState, {"device_id": "DEVICE-LOSS-1", "tenant_id": "ORG-1"})

    assert refreshed is not None
    assert float(refreshed.today_overconsumption_kwh or 0.0) > 0.0
    assert float(refreshed.today_idle_kwh or 0.0) == 0.0
    assert float(refreshed.today_offhours_kwh or 0.0) == 0.0
    assert float(refreshed.today_loss_kwh or 0.0) == pytest.approx(float(refreshed.today_overconsumption_kwh or 0.0), rel=1e-6)
    assert float(refreshed.today_loss_cost_inr or 0.0) > 0.0
    await engine.dispose()


@pytest.mark.asyncio
async def test_recompute_today_loss_projection_uses_fallback_for_flat_counter_idle_and_offhours():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        device = Device(
            device_id="DEVICE-LOSS-FLAT-1",
            tenant_id="ORG-1",
            plant_id="PLANT-1",
            device_name="Device Loss Flat 1",
            device_type="compressor",
            full_load_current_a=20.0,
            idle_threshold_pct_of_fla=0.25,
            created_at=datetime(2026, 4, 9, 10, 0, 0),
        )
        shift = DeviceShift(
            device_id="DEVICE-LOSS-FLAT-1",
            tenant_id="ORG-1",
            shift_name="Day Shift",
            shift_start=time(9, 30),
            shift_end=time(18, 30),
            maintenance_break_minutes=0,
            day_of_week=None,
            is_active=True,
        )
        state = DeviceLiveState(
            device_id="DEVICE-LOSS-FLAT-1",
            tenant_id="ORG-1",
            runtime_status="running",
            load_state="idle",
            version=0,
        )
        session.add_all([device, shift, state])
        await session.commit()

        service = LiveProjectionService(session)

        async def fake_tariff_get(_tenant_id):
            return {"configured": True, "rate": 5.0, "currency": "INR"}

        async def fake_window(*_args, **_kwargs):
            return [
                {
                    "timestamp": "2026-05-18T12:59:00+00:00",
                    "current": 0.1,
                    "voltage": 243.0,
                    "power": 67.5,
                    "energy_kwh": 7.4,
                },
                {
                    "timestamp": "2026-05-18T13:01:00+00:00",
                    "current": 0.1,
                    "voltage": 243.0,
                    "power": 67.5,
                    "energy_kwh": 7.4,
                },
                {
                    "timestamp": "2026-05-18T13:03:00+00:00",
                    "current": 0.1,
                    "voltage": 243.0,
                    "power": 67.5,
                    "energy_kwh": 7.4,
                },
            ]

        from app.services import live_projection as live_projection_module

        tariff_get_original = live_projection_module.TariffCache.get
        try:
            live_projection_module.TariffCache.get = AsyncMock(side_effect=fake_tariff_get)
            service._fetch_telemetry_window = AsyncMock(side_effect=fake_window)

            await service.recompute_today_loss_projection("DEVICE-LOSS-FLAT-1", "ORG-1")
        finally:
            live_projection_module.TariffCache.get = tariff_get_original

        refreshed = await session.get(DeviceLiveState, {"device_id": "DEVICE-LOSS-FLAT-1", "tenant_id": "ORG-1"})

    assert refreshed is not None
    expected_idle = 67.5 * 120.0 / 3600.0 / 1000.0
    expected_offhours = 67.5 * 120.0 / 3600.0 / 1000.0
    assert float(refreshed.today_idle_kwh or 0.0) == pytest.approx(expected_idle, abs=1e-6)
    assert float(refreshed.today_offhours_kwh or 0.0) == pytest.approx(expected_offhours, abs=1e-6)
    assert float(refreshed.today_loss_kwh or 0.0) == pytest.approx(expected_idle + expected_offhours, abs=1e-6)
    assert float(refreshed.today_loss_cost_inr or 0.0) > 0.0
    await engine.dispose()


@pytest.mark.asyncio
async def test_recompute_after_configuration_change_uses_tenant_scoped_health_configs():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add_all(
            [
                Device(
                    device_id="TENANT-A-DEVICE",
                    tenant_id="TENANT-A",
                    plant_id="PLANT-1",
                    device_name="Shared A",
                    device_type="compressor",
                ),
                Device(
                    device_id="TENANT-B-DEVICE",
                    tenant_id="TENANT-B",
                    plant_id="PLANT-1",
                    device_name="Shared B",
                    device_type="compressor",
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
            ]
        )
        await session.commit()

        service = LiveProjectionService(session)
        service._fetch_latest_projection_sample = AsyncMock(return_value={"current": 12.0, "timestamp": "2026-04-11T12:00:00+00:00"})
        service._shift = SimpleNamespace(calculate_uptime=AsyncMock(return_value={"uptime_percentage": 90.0}))

        result_a = await service.recompute_after_configuration_change("TENANT-A-DEVICE", "TENANT-A")
        result_b = await service.recompute_after_configuration_change("TENANT-B-DEVICE", "TENANT-B")

        state_a = await session.get(DeviceLiveState, {"device_id": "TENANT-A-DEVICE", "tenant_id": "TENANT-A"})
        state_b = await session.get(DeviceLiveState, {"device_id": "TENANT-B-DEVICE", "tenant_id": "TENANT-B"})

    assert result_a["health_score"] is not None
    assert result_b["health_score"] is not None
    assert state_a is not None and state_b is not None
    assert state_a.health_score is not None
    assert state_b.health_score is not None
    assert state_a.health_score != state_b.health_score

    await engine.dispose()


@pytest.mark.asyncio
async def test_recompute_after_configuration_change_falls_back_to_running_for_fresh_unclassified_telemetry():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add_all(
            [
                Device(
                    device_id="RECOMPUTE-DEVICE",
                    tenant_id="TENANT-1",
                    plant_id="PLANT-1",
                    device_name="Recompute Device",
                    device_type="compressor",
                ),
                ParameterHealthConfig(
                    device_id="RECOMPUTE-DEVICE",
                    tenant_id="TENANT-1",
                    parameter_name="current",
                    normal_min=8.0,
                    normal_max=18.0,
                    weight=100.0,
                    ignore_zero_value=False,
                    is_active=True,
                ),
            ]
        )
        await session.commit()

        service = LiveProjectionService(session)
        service._fetch_latest_projection_sample = AsyncMock(return_value={"current": 12.0, "timestamp": "2026-04-11T12:00:00+00:00"})
        service._shift = SimpleNamespace(calculate_uptime=AsyncMock(return_value={"uptime_percentage": 90.0}))

        result = await service.recompute_after_configuration_change("RECOMPUTE-DEVICE", "TENANT-1")
        state = await session.get(DeviceLiveState, {"device_id": "RECOMPUTE-DEVICE", "tenant_id": "TENANT-1"})

    assert result["health_score"] is not None
    assert state is not None
    assert state.health_score is not None

    await engine.dispose()


@pytest.mark.asyncio
async def test_get_device_snapshot_item_includes_plant_id():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(
            Device(
                device_id="DEVICE-PLANT-1",
                tenant_id="ORG-1",
                plant_id="PLANT-1",
                device_name="Device Plant 1",
                device_type="compressor",
            )
        )
        await session.commit()

        service = LiveProjectionService(session)
        item = await service.get_device_snapshot_item("DEVICE-PLANT-1", "ORG-1")

    assert item["device_id"] == "DEVICE-PLANT-1"
    assert item["plant_id"] == "PLANT-1"

    await engine.dispose()
