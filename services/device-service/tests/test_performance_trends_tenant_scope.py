from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

BASE_DIR = Path(__file__).resolve().parents[1]
SERVICES_DIR = Path(__file__).resolve().parents[2]
for path in (BASE_DIR, SERVICES_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.environ["DATABASE_URL"] = "mysql+aiomysql://test:test@127.0.0.1:3306/test_db"

from app.database import Base
from app.models.device import Device, DevicePerformanceTrend
from app.services.performance_trends import PerformanceTrendService


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


@pytest.mark.asyncio
async def test_materialize_latest_bucket_preserves_tenant_ids(session_factory, monkeypatch):
    async with session_factory() as session:
        session.add_all(
            [
                Device(
                    device_id="DEVICE-A",
                    tenant_id="TENANT-A",
                    plant_id="PLANT-1",
                    device_name="Device A",
                    device_type="compressor",
                    data_source_type="metered",
                ),
                Device(
                    device_id="DEVICE-B",
                    tenant_id="TENANT-B",
                    plant_id="PLANT-1",
                    device_name="Device B",
                    device_type="compressor",
                    data_source_type="metered",
                ),
            ]
        )
        await session.commit()

        captured: list[tuple[str, str]] = []

        async def fake_materialize(device_id: str, tenant_id: str, *_args):
            captured.append((device_id, tenant_id))
            return "created"

        service = PerformanceTrendService(session)
        monkeypatch.setattr(service, "_materialize_device_bucket", fake_materialize)

        summary = await service.materialize_latest_bucket()

    assert summary["devices_total"] == 2
    assert set(captured) == {("DEVICE-A", "TENANT-A"), ("DEVICE-B", "TENANT-B")}


@pytest.mark.asyncio
async def test_get_trends_filters_by_tenant_id(session_factory):
    now = datetime.now(timezone.utc)
    bucket = now.replace(minute=0, second=0, microsecond=0)

    async with session_factory() as session:
        session.add_all(
            [
                Device(
                    device_id="SHARED-DEVICE",
                    tenant_id="TENANT-A",
                    plant_id="PLANT-1",
                    device_name="Tenant A Device",
                    device_type="compressor",
                    data_source_type="metered",
                ),
                DevicePerformanceTrend(
                    device_id="SHARED-DEVICE",
                    tenant_id="TENANT-A",
                    bucket_start_utc=bucket - timedelta(minutes=5),
                    bucket_end_utc=bucket,
                    bucket_timezone="Asia/Kolkata",
                    interval_minutes=5,
                    health_score=91.0,
                    uptime_percentage=88.0,
                    planned_minutes=30,
                    effective_minutes=26,
                    break_minutes=0,
                    points_used=4,
                    is_valid=True,
                    message="tenant-a",
                ),
                DevicePerformanceTrend(
                    device_id="SHARED-DEVICE",
                    tenant_id="TENANT-B",
                    bucket_start_utc=bucket - timedelta(minutes=5),
                    bucket_end_utc=bucket,
                    bucket_timezone="Asia/Kolkata",
                    interval_minutes=5,
                    health_score=12.0,
                    uptime_percentage=10.0,
                    planned_minutes=30,
                    effective_minutes=3,
                    break_minutes=0,
                    points_used=4,
                    is_valid=True,
                    message="tenant-b",
                ),
            ]
        )
        await session.commit()

        service = PerformanceTrendService(session)
        result = await service.get_trends(
            device_id="SHARED-DEVICE",
            tenant_id="TENANT-A",
            metric="health",
            range_key="24h",
        )

    assert result["total_points"] == 1
    assert result["points"][0]["health_score"] == 91.0
    assert result["message"] == "tenant-a"


@pytest.mark.asyncio
async def test_get_trends_reads_materialized_rows_without_repairing_or_materializing_on_get(
    session_factory,
    monkeypatch,
):
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    bucket = now - timedelta(minutes=5)

    async with session_factory() as session:
        session.add_all(
            [
                Device(
                    device_id="DEVICE-READ-ONLY",
                    tenant_id="TENANT-A",
                    plant_id="PLANT-1",
                    device_name="Tenant A Device",
                    device_type="compressor",
                    data_source_type="metered",
                ),
                DevicePerformanceTrend(
                    device_id="DEVICE-READ-ONLY",
                    tenant_id="TENANT-A",
                    bucket_start_utc=bucket,
                    bucket_end_utc=now,
                    bucket_timezone="Asia/Kolkata",
                    interval_minutes=5,
                    health_score=83.0,
                    uptime_percentage=90.0,
                    planned_minutes=30,
                    effective_minutes=27,
                    break_minutes=0,
                    points_used=4,
                    is_valid=True,
                    message="materialized-row",
                ),
            ]
        )
        await session.commit()

        service = PerformanceTrendService(session)
        backfill_health = AsyncMock(side_effect=AssertionError("health repair should not run during GET"))
        backfill_uptime = AsyncMock(side_effect=AssertionError("uptime repair should not run during GET"))
        materialize_bucket = AsyncMock(side_effect=AssertionError("bucket materialization should not run during GET"))
        monkeypatch.setattr(service, "backfill_health_scores", backfill_health)
        monkeypatch.setattr(service, "backfill_uptime_components", backfill_uptime)
        monkeypatch.setattr(service, "_materialize_device_bucket", materialize_bucket)

        result = await service.get_trends(
            device_id="DEVICE-READ-ONLY",
            tenant_id="TENANT-A",
            metric="health",
            range_key="24h",
        )

    assert result["points"][0]["health_score"] == 83.0
    backfill_health.assert_not_awaited()
    backfill_uptime.assert_not_awaited()
    materialize_bucket.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_trends_returns_metric_specific_fallback_for_quiet_window(session_factory):
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    recent_valid = now - timedelta(hours=2)

    async with session_factory() as session:
        session.add_all(
            [
                Device(
                    device_id="DEVICE-FALLBACK",
                    tenant_id="TENANT-A",
                    plant_id="PLANT-1",
                    device_name="Tenant A Device",
                    device_type="compressor",
                    data_source_type="metered",
                ),
                DevicePerformanceTrend(
                    device_id="DEVICE-FALLBACK",
                    tenant_id="TENANT-A",
                    bucket_start_utc=recent_valid,
                    bucket_end_utc=recent_valid + timedelta(minutes=5),
                    bucket_timezone="Asia/Kolkata",
                    interval_minutes=5,
                    health_score=77.0,
                    uptime_percentage=88.0,
                    planned_minutes=30,
                    effective_minutes=26,
                    break_minutes=0,
                    points_used=4,
                    is_valid=True,
                    message="Health score calculated from 1 parameter(s) | shift-active",
                ),
            ]
        )
        await session.commit()

        service = PerformanceTrendService(session)
        result = await service.get_trends(
            device_id="DEVICE-FALLBACK",
            tenant_id="TENANT-A",
            metric="health",
            range_key="30m",
        )

    assert result["points"] == []
    assert result["is_stale"] is True
    assert result["fallback_point"] is not None
    assert result["fallback_point"]["value"] == 77.0
    assert result["last_actual_timestamp"] == result["fallback_point"]["timestamp"]
    assert "No new health points in selected window." in result["metric_message"]


@pytest.mark.asyncio
async def test_get_trends_omits_fallback_beyond_horizon(session_factory):
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    old_valid = now - timedelta(days=8)

    async with session_factory() as session:
        session.add_all(
            [
                Device(
                    device_id="DEVICE-OLD",
                    tenant_id="TENANT-A",
                    plant_id="PLANT-1",
                    device_name="Tenant A Device",
                    device_type="compressor",
                    data_source_type="metered",
                ),
                DevicePerformanceTrend(
                    device_id="DEVICE-OLD",
                    tenant_id="TENANT-A",
                    bucket_start_utc=old_valid,
                    bucket_end_utc=old_valid + timedelta(minutes=5),
                    bucket_timezone="Asia/Kolkata",
                    interval_minutes=5,
                    health_score=81.0,
                    uptime_percentage=92.0,
                    planned_minutes=30,
                    effective_minutes=28,
                    break_minutes=0,
                    points_used=4,
                    is_valid=True,
                    message="Health score calculated from 1 parameter(s) | shift-active",
                ),
            ]
        )
        await session.commit()

        service = PerformanceTrendService(session)
        result = await service.get_trends(
            device_id="DEVICE-OLD",
            tenant_id="TENANT-A",
            metric="health",
            range_key="30m",
        )

    assert result["points"] == []
    assert result["fallback_point"] is None
    assert result["is_stale"] is False
    assert result["metric_message"] == "No health trend data available for the selected window."


@pytest.mark.asyncio
async def test_get_trends_uses_metric_specific_message_for_uptime(session_factory):
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    bucket = now - timedelta(minutes=5)

    async with session_factory() as session:
        session.add_all(
            [
                Device(
                    device_id="DEVICE-UPTIME",
                    tenant_id="TENANT-A",
                    plant_id="PLANT-1",
                    device_name="Tenant A Device",
                    device_type="compressor",
                    data_source_type="metered",
                ),
                DevicePerformanceTrend(
                    device_id="DEVICE-UPTIME",
                    tenant_id="TENANT-A",
                    bucket_start_utc=bucket,
                    bucket_end_utc=now,
                    bucket_timezone="Asia/Kolkata",
                    interval_minutes=5,
                    health_score=None,
                    uptime_percentage=86.0,
                    planned_minutes=30,
                    effective_minutes=25,
                    break_minutes=0,
                    points_used=0,
                    is_valid=True,
                    message="No matching telemetry parameters found for configured health metrics. | Shift ended before selected window.",
                ),
            ]
        )
        await session.commit()

        service = PerformanceTrendService(session)
        result = await service.get_trends(
            device_id="DEVICE-UPTIME",
            tenant_id="TENANT-A",
            metric="uptime",
            range_key="24h",
        )

    assert result["points"][0]["uptime_percentage"] == 86.0
    assert result["metric_message"] == "Shift ended before selected window."
    assert "health metrics" not in result["metric_message"]
