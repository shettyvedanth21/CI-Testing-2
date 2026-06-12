from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo
import os
import sys

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

BASE_DIR = Path(__file__).resolve().parents[1]
SERVICES_DIR = Path(__file__).resolve().parents[2]
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEVICE_SERVICE_DIR = SERVICES_DIR / "device-service"
for path in (BASE_DIR, DEVICE_SERVICE_DIR, SERVICES_DIR, PROJECT_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.environ.setdefault("DATABASE_URL", "mysql+aiomysql://test:test@127.0.0.1:3306/test_db")

from app.database import Base
from app.models import Device, DeviceLiveState
from app.models.device import DeviceStateInterval, DeviceStateIntervalType, TELEMETRY_TIMEOUT_SECONDS
from app.services import live_projection as live_projection_module
from app.services.device_state_intervals import DeviceStateIntervalService
from app.services.live_projection import LiveProjectionService

IST = ZoneInfo("Asia/Kolkata")


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


async def _make_service(session, monkeypatch: pytest.MonkeyPatch) -> LiveProjectionService:
    monkeypatch.setattr(
        live_projection_module.TariffCache,
        "get",
        AsyncMock(return_value={"configured": False, "rate": 0.0, "currency": "INR"}),
    )
    service = LiveProjectionService(session)
    service._health = SimpleNamespace(calculate_health_score=AsyncMock(return_value={"health_score": None}))
    return service


async def _seed_device(session, *, device_id: str = "DEVICE-1") -> None:
    session.add(
        Device(
            device_id=device_id,
            tenant_id="TENANT-A",
            plant_id="PLANT-1",
            device_name="Tracked Device",
            device_type="compressor",
            full_load_current_a=20.0,
            created_at=datetime(2026, 4, 12, 0, 0, 0, tzinfo=timezone.utc),
        )
    )
    await session.commit()


async def _apply_sample(
    service: LiveProjectionService,
    *,
    device_id: str,
    ts: datetime,
    current: float,
    voltage: float = 230.0,
) -> dict:
    power = max(current * voltage, 0.0)
    return await service.apply_live_update(
        device_id=device_id,
        tenant_id="TENANT-A",
        telemetry_payload={
            "timestamp": ts.isoformat(),
            "current": current,
            "voltage": voltage,
            "power": power,
        },
        dynamic_fields={"current": current, "voltage": voltage, "power": power},
    )


async def _intervals(session, state_type: DeviceStateIntervalType) -> list[DeviceStateInterval]:
    result = await session.execute(
        select(DeviceStateInterval)
        .where(
            DeviceStateInterval.tenant_id == "TENANT-A",
            DeviceStateInterval.device_id == "DEVICE-1",
            DeviceStateInterval.state_type == state_type.value,
        )
        .order_by(DeviceStateInterval.started_at.asc(), DeviceStateInterval.id.asc())
    )
    return list(result.scalars().all())


async def _all_device_intervals(session) -> list[DeviceStateInterval]:
    result = await session.execute(
        select(DeviceStateInterval)
        .where(
            DeviceStateInterval.tenant_id == "TENANT-A",
            DeviceStateInterval.device_id == "DEVICE-1",
        )
        .order_by(DeviceStateInterval.started_at.asc(), DeviceStateInterval.id.asc())
    )
    return list(result.scalars().all())


async def _live_state(session) -> DeviceLiveState | None:
    return await session.get(DeviceLiveState, {"device_id": "DEVICE-1", "tenant_id": "TENANT-A"})


@pytest.mark.asyncio
async def test_idle_transition_opens_and_closes_interval_rows(session_factory, monkeypatch):
    async with session_factory() as session:
        await _seed_device(session)
        service = await _make_service(session, monkeypatch)

        first_ts = datetime.now(timezone.utc)
        second_ts = first_ts + timedelta(seconds=30)

        await _apply_sample(service, device_id="DEVICE-1", ts=first_ts, current=2.0)
        item = await _apply_sample(service, device_id="DEVICE-1", ts=second_ts, current=8.0)

        intervals = await _intervals(session, DeviceStateIntervalType.IDLE)
        assert item["load_state"] == "running"
        assert len(intervals) == 1
        assert intervals[0].is_open is False
        assert intervals[0].started_at == first_ts
        assert intervals[0].ended_at == second_ts
        assert intervals[0].duration_sec == 30


@pytest.mark.asyncio
async def test_overconsumption_transition_opens_and_closes_interval_rows(session_factory, monkeypatch):
    async with session_factory() as session:
        await _seed_device(session)
        service = await _make_service(session, monkeypatch)

        first_ts = datetime.now(timezone.utc)
        second_ts = first_ts + timedelta(seconds=45)

        await _apply_sample(service, device_id="DEVICE-1", ts=first_ts, current=25.0)
        await _apply_sample(service, device_id="DEVICE-1", ts=second_ts, current=8.0)

        intervals = await _intervals(session, DeviceStateIntervalType.OVERCONSUMPTION)
        assert len(intervals) == 1
        assert intervals[0].is_open is False
        assert intervals[0].started_at == first_ts
        assert intervals[0].ended_at == second_ts
        assert intervals[0].duration_sec == 45


@pytest.mark.asyncio
async def test_runtime_on_opens_without_duplicate_rows(session_factory, monkeypatch):
    async with session_factory() as session:
        await _seed_device(session)
        service = await _make_service(session, monkeypatch)

        first_ts = datetime.now(timezone.utc)
        second_ts = first_ts + timedelta(seconds=15)
        third_ts = first_ts + timedelta(seconds=30)

        await _apply_sample(service, device_id="DEVICE-1", ts=first_ts, current=0.0)
        await _apply_sample(service, device_id="DEVICE-1", ts=second_ts, current=0.0)
        item = await _apply_sample(service, device_id="DEVICE-1", ts=third_ts, current=2.0)

        intervals = await _intervals(session, DeviceStateIntervalType.RUNTIME_ON)
        assert item["runtime_status"] == "running"
        assert len(intervals) == 1
        assert intervals[0].is_open is True
        assert intervals[0].started_at == first_ts
        assert intervals[0].ended_at is None
        assert intervals[0].duration_sec is None


@pytest.mark.asyncio
async def test_repeated_idle_telemetry_does_not_create_duplicate_interval_rows(session_factory, monkeypatch):
    async with session_factory() as session:
        await _seed_device(session)
        service = await _make_service(session, monkeypatch)

        base_ts = datetime.now(timezone.utc)
        await _apply_sample(
            service,
            device_id="DEVICE-1",
            ts=base_ts,
            current=2.0,
        )
        await _apply_sample(
            service,
            device_id="DEVICE-1",
            ts=base_ts + timedelta(seconds=10),
            current=2.0,
        )
        await _apply_sample(
            service,
            device_id="DEVICE-1",
            ts=base_ts + timedelta(seconds=20),
            current=2.0,
        )

        idle_intervals = await _intervals(session, DeviceStateIntervalType.IDLE)
        runtime_intervals = await _intervals(session, DeviceStateIntervalType.RUNTIME_ON)
        assert len(idle_intervals) == 1
        assert len(runtime_intervals) == 1
        assert idle_intervals[0].is_open is True
        assert runtime_intervals[0].is_open is True


@pytest.mark.asyncio
async def test_interval_service_enforces_single_open_interval_per_device_and_state_type(session_factory):
    async with session_factory() as session:
        await _seed_device(session)
        service = DeviceStateIntervalService(session)
        first_ts = datetime.now(timezone.utc)
        second_ts = first_ts + timedelta(minutes=5)

        first = await service.open_interval(
            tenant_id="TENANT-A",
            device_id="DEVICE-1",
            state_type=DeviceStateIntervalType.IDLE,
            started_at=first_ts,
            sample_ts=first_ts,
            opened_reason="test_open",
            source="live_projection",
        )
        second = await service.open_interval(
            tenant_id="TENANT-A",
            device_id="DEVICE-1",
            state_type=DeviceStateIntervalType.IDLE,
            started_at=second_ts,
            sample_ts=second_ts,
            opened_reason="test_open",
            source="live_projection",
        )
        await session.commit()

        rows = await _intervals(session, DeviceStateIntervalType.IDLE)
        assert first.id == second.id
        assert len(rows) == 1
        assert rows[0].is_open is True


@pytest.mark.asyncio
async def test_interval_timestamps_remain_timezone_aware_and_ist_interpretable(session_factory, monkeypatch):
    async with session_factory() as session:
        await _seed_device(session)
        service = await _make_service(session, monkeypatch)

        sample_ts = datetime(2026, 4, 12, 18, 29, 45, tzinfo=timezone.utc)
        await _apply_sample(service, device_id="DEVICE-1", ts=sample_ts, current=2.0)

        interval = (await _intervals(session, DeviceStateIntervalType.IDLE))[0]
        assert interval.started_at.tzinfo is not None
        assert interval.opened_by_sample_ts is not None and interval.opened_by_sample_ts.tzinfo is not None
        assert interval.started_at.astimezone(IST).isoformat() == "2026-04-12T23:59:45+05:30"


@pytest.mark.asyncio
async def test_ist_day_rollover_preserves_interval_timestamps_and_live_projection_behavior(session_factory, monkeypatch):
    async with session_factory() as session:
        await _seed_device(session)
        service = await _make_service(session, monkeypatch)

        local_today = datetime.now(IST)
        first_ts = datetime(
            local_today.year,
            local_today.month,
            local_today.day,
            23,
            59,
            45,
            tzinfo=IST,
        ).astimezone(timezone.utc)
        second_ts = first_ts + timedelta(seconds=30)

        await _apply_sample(service, device_id="DEVICE-1", ts=first_ts, current=2.0)
        item = await _apply_sample(service, device_id="DEVICE-1", ts=second_ts, current=8.0)

        live_state = await session.get(DeviceLiveState, {"device_id": "DEVICE-1", "tenant_id": "TENANT-A"})
        intervals = await _intervals(session, DeviceStateIntervalType.IDLE)

        assert item["runtime_status"] == "running"
        assert item["load_state"] == "running"
        assert live_state is not None
        assert live_state.day_bucket == second_ts.astimezone(IST).date()
        assert len(intervals) == 1
        assert intervals[0].started_at == first_ts
        assert intervals[0].ended_at == second_ts
        assert intervals[0].duration_sec == 30


@pytest.mark.asyncio
async def test_runtime_on_interval_closes_when_telemetry_becomes_stale(session_factory, monkeypatch):
    async with session_factory() as session:
        await _seed_device(session)
        service = await _make_service(session, monkeypatch)

        sample_ts = datetime(2026, 4, 12, 10, 0, 0, tzinfo=timezone.utc)
        await _apply_sample(service, device_id="DEVICE-1", ts=sample_ts, current=8.0)

        summary = await service._reconcile_timed_out_intervals_for_device(
            tenant_id="TENANT-A",
            device_id="DEVICE-1",
            authoritative_last_seen=sample_ts,
            now_utc=sample_ts + timedelta(seconds=TELEMETRY_TIMEOUT_SECONDS + 1),
        )

        runtime_intervals = await _intervals(session, DeviceStateIntervalType.RUNTIME_ON)
        live_state = await _live_state(session)
        expected_end = sample_ts + timedelta(seconds=TELEMETRY_TIMEOUT_SECONDS)
        assert summary["closed_intervals"] == 1
        assert summary["persisted_state_updated"] is True
        assert len(runtime_intervals) == 1
        assert runtime_intervals[0].is_open is False
        assert runtime_intervals[0].ended_at == expected_end
        assert runtime_intervals[0].duration_sec == TELEMETRY_TIMEOUT_SECONDS
        assert runtime_intervals[0].closed_reason == "telemetry_timeout"
        assert runtime_intervals[0].source == "timeout_reconciler"
        assert live_state is not None
        assert live_state.runtime_status == "stopped"
        assert live_state.load_state == "unknown"
        assert live_state.idle_streak_started_at is None
        assert live_state.idle_streak_duration_sec == 0


@pytest.mark.asyncio
async def test_idle_interval_closes_when_device_times_out(session_factory, monkeypatch):
    async with session_factory() as session:
        await _seed_device(session)
        service = await _make_service(session, monkeypatch)

        sample_ts = datetime(2026, 4, 12, 10, 5, 0, tzinfo=timezone.utc)
        await _apply_sample(service, device_id="DEVICE-1", ts=sample_ts, current=2.0)

        summary = await service._reconcile_timed_out_intervals_for_device(
            tenant_id="TENANT-A",
            device_id="DEVICE-1",
            authoritative_last_seen=sample_ts,
            now_utc=sample_ts + timedelta(seconds=TELEMETRY_TIMEOUT_SECONDS + 1),
        )

        idle_intervals = await _intervals(session, DeviceStateIntervalType.IDLE)
        runtime_intervals = await _intervals(session, DeviceStateIntervalType.RUNTIME_ON)
        expected_end = sample_ts + timedelta(seconds=TELEMETRY_TIMEOUT_SECONDS)
        assert summary["closed_intervals"] == 2
        assert idle_intervals[0].ended_at == expected_end
        assert idle_intervals[0].duration_sec == TELEMETRY_TIMEOUT_SECONDS
        assert idle_intervals[0].closed_reason == "telemetry_timeout"
        assert runtime_intervals[0].ended_at == expected_end


@pytest.mark.asyncio
async def test_overconsumption_interval_closes_when_device_times_out(session_factory, monkeypatch):
    async with session_factory() as session:
        await _seed_device(session)
        service = await _make_service(session, monkeypatch)

        sample_ts = datetime(2026, 4, 12, 10, 10, 0, tzinfo=timezone.utc)
        await _apply_sample(service, device_id="DEVICE-1", ts=sample_ts, current=25.0)

        summary = await service._reconcile_timed_out_intervals_for_device(
            tenant_id="TENANT-A",
            device_id="DEVICE-1",
            authoritative_last_seen=sample_ts,
            now_utc=sample_ts + timedelta(seconds=TELEMETRY_TIMEOUT_SECONDS + 1),
        )

        over_intervals = await _intervals(session, DeviceStateIntervalType.OVERCONSUMPTION)
        runtime_intervals = await _intervals(session, DeviceStateIntervalType.RUNTIME_ON)
        expected_end = sample_ts + timedelta(seconds=TELEMETRY_TIMEOUT_SECONDS)
        assert summary["closed_intervals"] == 2
        assert over_intervals[0].ended_at == expected_end
        assert over_intervals[0].duration_sec == TELEMETRY_TIMEOUT_SECONDS
        assert over_intervals[0].closed_reason == "telemetry_timeout"
        assert runtime_intervals[0].ended_at == expected_end


@pytest.mark.asyncio
async def test_timeout_reconciliation_is_idempotent_across_repeated_runs(session_factory, monkeypatch):
    async with session_factory() as session:
        await _seed_device(session)
        service = await _make_service(session, monkeypatch)

        sample_ts = datetime(2026, 4, 12, 10, 15, 0, tzinfo=timezone.utc)
        await _apply_sample(service, device_id="DEVICE-1", ts=sample_ts, current=2.0)

        first = await service._reconcile_timed_out_intervals_for_device(
            tenant_id="TENANT-A",
            device_id="DEVICE-1",
            authoritative_last_seen=sample_ts,
            now_utc=sample_ts + timedelta(seconds=TELEMETRY_TIMEOUT_SECONDS + 1),
        )
        second = await service._reconcile_timed_out_intervals_for_device(
            tenant_id="TENANT-A",
            device_id="DEVICE-1",
            authoritative_last_seen=sample_ts,
            now_utc=sample_ts + timedelta(seconds=TELEMETRY_TIMEOUT_SECONDS + 120),
        )

        idle_intervals = await _intervals(session, DeviceStateIntervalType.IDLE)
        runtime_intervals = await _intervals(session, DeviceStateIntervalType.RUNTIME_ON)
        live_state = await _live_state(session)
        assert first["closed_intervals"] == 2
        assert first["persisted_state_updated"] is True
        assert second["closed_intervals"] == 0
        assert second["persisted_state_updated"] is False
        assert len(idle_intervals) == 1
        assert len(runtime_intervals) == 1
        assert live_state is not None
        assert live_state.runtime_status == "stopped"
        assert live_state.load_state == "unknown"


@pytest.mark.asyncio
async def test_reconcile_open_interval_timeouts_repairs_stale_open_rows_after_restart(session_factory, monkeypatch):
    async with session_factory() as session:
        await _seed_device(session)
        service = await _make_service(session, monkeypatch)

        sample_ts = datetime(2026, 4, 12, 10, 20, 0, tzinfo=timezone.utc)
        await _apply_sample(service, device_id="DEVICE-1", ts=sample_ts, current=2.0)

        live_state = await session.get(DeviceLiveState, {"device_id": "DEVICE-1", "tenant_id": "TENANT-A"})
        assert live_state is not None
        live_state.last_telemetry_ts = sample_ts
        live_state.last_sample_ts = sample_ts
        await session.commit()

    async with session_factory() as restart_session:
        restart_service = await _make_service(restart_session, monkeypatch)
        summary = await restart_service.reconcile_open_interval_timeouts(max_devices=10)

        idle_intervals = await _intervals(restart_session, DeviceStateIntervalType.IDLE)
        runtime_intervals = await _intervals(restart_session, DeviceStateIntervalType.RUNTIME_ON)
        expected_end = sample_ts + timedelta(seconds=TELEMETRY_TIMEOUT_SECONDS)
        assert summary["closed_intervals"] == 2
        assert summary["closed_device_ids"] == ["DEVICE-1"]
        assert idle_intervals[0].ended_at == expected_end
        assert runtime_intervals[0].ended_at == expected_end


@pytest.mark.asyncio
async def test_cleanup_only_affects_old_closed_rows_and_preserves_open_rows(session_factory):
    async with session_factory() as session:
        await _seed_device(session)
        service = DeviceStateIntervalService(session)
        now_utc = datetime(2026, 4, 16, 12, 0, 0, tzinfo=timezone.utc)

        session.add_all(
            [
                DeviceStateInterval(
                    tenant_id="TENANT-A",
                    device_id="DEVICE-1",
                    state_type=DeviceStateIntervalType.IDLE.value,
                    started_at=now_utc - timedelta(days=40, minutes=10),
                    ended_at=now_utc - timedelta(days=40),
                    duration_sec=600,
                    is_open=False,
                    source="live_projection",
                ),
                DeviceStateInterval(
                    tenant_id="TENANT-A",
                    device_id="DEVICE-1",
                    state_type=DeviceStateIntervalType.OVERCONSUMPTION.value,
                    started_at=now_utc - timedelta(days=2, minutes=10),
                    ended_at=now_utc - timedelta(days=2),
                    duration_sec=600,
                    is_open=False,
                    source="live_projection",
                ),
                DeviceStateInterval(
                    tenant_id="TENANT-A",
                    device_id="DEVICE-1",
                    state_type=DeviceStateIntervalType.RUNTIME_ON.value,
                    started_at=now_utc - timedelta(days=50),
                    ended_at=None,
                    duration_sec=None,
                    is_open=True,
                    source="live_projection",
                ),
            ]
        )
        await session.commit()

        summary = await service.cleanup_closed_intervals_for_tenant(
            tenant_id="TENANT-A",
            retention_days=14,
            batch_size=100,
            max_batches=10,
            now_utc=now_utc,
        )
        await session.commit()

        rows = await _all_device_intervals(session)
        assert summary["deleted"] == 1
        assert len(rows) == 2
        assert sum(1 for row in rows if row.is_open) == 1


@pytest.mark.asyncio
async def test_cleanup_respects_retention_cutoff_boundary(session_factory):
    async with session_factory() as session:
        await _seed_device(session)
        service = DeviceStateIntervalService(session)
        now_utc = datetime(2026, 4, 16, 12, 0, 0, tzinfo=timezone.utc)
        cutoff = now_utc - timedelta(days=14)

        session.add_all(
            [
                DeviceStateInterval(
                    tenant_id="TENANT-A",
                    device_id="DEVICE-1",
                    state_type=DeviceStateIntervalType.IDLE.value,
                    started_at=cutoff - timedelta(minutes=10),
                    ended_at=cutoff,
                    duration_sec=600,
                    is_open=False,
                    source="live_projection",
                ),
                DeviceStateInterval(
                    tenant_id="TENANT-A",
                    device_id="DEVICE-1",
                    state_type=DeviceStateIntervalType.IDLE.value,
                    started_at=cutoff - timedelta(days=1, minutes=10),
                    ended_at=cutoff - timedelta(days=1),
                    duration_sec=600,
                    is_open=False,
                    source="live_projection",
                ),
            ]
        )
        await session.commit()

        summary = await service.cleanup_closed_intervals_for_tenant(
            tenant_id="TENANT-A",
            retention_days=14,
            batch_size=100,
            max_batches=10,
            now_utc=now_utc,
        )
        await session.commit()

        rows = await _all_device_intervals(session)
        assert summary["deleted"] == 1
        assert len(rows) == 1
        assert rows[0].ended_at == cutoff


@pytest.mark.asyncio
async def test_cleanup_works_in_batches_and_is_idempotent(session_factory):
    async with session_factory() as session:
        await _seed_device(session)
        service = DeviceStateIntervalService(session)
        now_utc = datetime(2026, 4, 16, 12, 0, 0, tzinfo=timezone.utc)

        intervals = []
        for idx in range(5):
            intervals.append(
                DeviceStateInterval(
                    tenant_id="TENANT-A",
                    device_id="DEVICE-1",
                    state_type=DeviceStateIntervalType.IDLE.value,
                    started_at=now_utc - timedelta(days=30, minutes=idx + 1),
                    ended_at=now_utc - timedelta(days=30, minutes=idx),
                    duration_sec=60,
                    is_open=False,
                    source="live_projection",
                )
            )
        session.add_all(intervals)
        await session.commit()

        first = await service.cleanup_closed_intervals_for_tenant(
            tenant_id="TENANT-A",
            retention_days=14,
            batch_size=2,
            max_batches=2,
            now_utc=now_utc,
        )
        await session.commit()
        second = await service.cleanup_closed_intervals_for_tenant(
            tenant_id="TENANT-A",
            retention_days=14,
            batch_size=2,
            max_batches=2,
            now_utc=now_utc,
        )
        await session.commit()
        third = await service.cleanup_closed_intervals_for_tenant(
            tenant_id="TENANT-A",
            retention_days=14,
            batch_size=2,
            max_batches=2,
            now_utc=now_utc,
        )
        await session.commit()

        rows = await _all_device_intervals(session)
        assert first["deleted"] == 4
        assert second["deleted"] == 1
        assert third["deleted"] == 0
        assert len(rows) == 0


@pytest.mark.asyncio
async def test_collect_open_interval_observability_reports_open_counts_and_stale_open(session_factory):
    async with session_factory() as session:
        await _seed_device(session)
        service = DeviceStateIntervalService(session)
        now_utc = datetime(2026, 4, 16, 12, 0, 0, tzinfo=timezone.utc)

        session.add_all(
            [
                DeviceStateInterval(
                    tenant_id="TENANT-A",
                    device_id="DEVICE-1",
                    state_type=DeviceStateIntervalType.IDLE.value,
                    started_at=now_utc - timedelta(days=20),
                    ended_at=None,
                    duration_sec=None,
                    is_open=True,
                    source="live_projection",
                ),
                DeviceStateInterval(
                    tenant_id="TENANT-A",
                    device_id="DEVICE-1",
                    state_type=DeviceStateIntervalType.RUNTIME_ON.value,
                    started_at=now_utc - timedelta(days=1),
                    ended_at=None,
                    duration_sec=None,
                    is_open=True,
                    source="live_projection",
                ),
                DeviceStateInterval(
                    tenant_id="TENANT-A",
                    device_id="DEVICE-1",
                    state_type=DeviceStateIntervalType.OVERCONSUMPTION.value,
                    started_at=now_utc - timedelta(days=30),
                    ended_at=now_utc - timedelta(days=29),
                    duration_sec=86400,
                    is_open=False,
                    source="live_projection",
                ),
            ]
        )
        await session.commit()

        summary = await service.collect_open_interval_observability(
            tenant_id="TENANT-A",
            stale_open_alert_days=7,
            now_utc=now_utc,
        )

        assert summary["open_total"] == 2
        assert summary["stale_open_count"] == 1
        assert summary["open_counts_by_state"]["idle"] == 1
        assert summary["open_counts_by_state"]["runtime_on"] == 1
