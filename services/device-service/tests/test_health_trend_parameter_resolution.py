from __future__ import annotations

import os
import sys
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

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
from app.models.device import Device, DevicePerformanceTrend, ParameterHealthConfig, DeviceShift, DeviceRecentTelemetrySample
from app.services.health_config import HealthConfigService
from app.services import performance_trends as performance_trends_module
from app.services.performance_trends import PerformanceTrendService
from app.services.shift import ShiftService


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


async def _seed_device_with_health_config(session, *, device_id: str = "DEVICE-1", tenant_id: str = "TENANT-1"):
    session.add(
        Device(
            device_id=device_id,
            tenant_id=tenant_id,
            plant_id="PLANT-1",
            device_name="Compressor",
            device_type="compressor",
            data_source_type="metered",
        )
    )
    session.add_all(
        [
            ParameterHealthConfig(
                device_id=device_id,
                tenant_id=tenant_id,
                parameter_name="current",
                normal_min=8.0,
                normal_max=18.0,
                weight=60.0,
                ignore_zero_value=False,
                is_active=True,
            ),
            ParameterHealthConfig(
                device_id=device_id,
                tenant_id=tenant_id,
                parameter_name="voltage",
                normal_min=210.0,
                normal_max=250.0,
                weight=40.0,
                ignore_zero_value=False,
                is_active=True,
            ),
        ]
    )
    await session.commit()


async def _seed_device_with_configs(session, configs, *, device_id: str = "DEVICE-1", tenant_id: str = "TENANT-1"):
    session.add(
        Device(
            device_id=device_id,
            tenant_id=tenant_id,
            plant_id="PLANT-1",
            device_name="Compressor",
            device_type="compressor",
            data_source_type="metered",
        )
    )
    session.add_all(
        [
            ParameterHealthConfig(
                device_id=device_id,
                tenant_id=tenant_id,
                **config,
            )
            for config in configs
        ]
    )
    await session.commit()


async def _seed_shift(
    session,
    *,
    device_id: str,
    tenant_id: str,
    shift_name: str = "D1",
    shift_start: str = "09:00",
    shift_end: str = "17:00",
    maintenance_break_minutes: int = 0,
):
    from datetime import time

    start_hour, start_minute = map(int, shift_start.split(":"))
    end_hour, end_minute = map(int, shift_end.split(":"))
    session.add(
        DeviceShift(
            device_id=device_id,
            tenant_id=tenant_id,
            shift_name=shift_name,
            shift_start=time(start_hour, start_minute),
            shift_end=time(end_hour, end_minute),
            maintenance_break_minutes=maintenance_break_minutes,
            day_of_week=None,
            is_active=True,
        )
    )
    await session.commit()


@pytest.mark.asyncio
async def test_health_score_does_not_resolve_phase_diagnostics_for_canonical_metrics(session_factory):
    async with session_factory() as session:
        await _seed_device_with_health_config(session)
        service = HealthConfigService(session)

        result = await service.calculate_health_score(
            device_id="DEVICE-1",
            tenant_id="TENANT-1",
            machine_state="RUNNING",
            telemetry_values={
                "current_l1": 12.0,
                "current_l2": 10.0,
                "current_l3": 9.0,
                "voltage_l1": 231.0,
                "voltage_l2": 229.0,
                "voltage_l3": 230.0,
            },
        )

    assert result["health_score"] is None
    assert result["parameters_included"] == 0
    assert result["message"] == "No matching telemetry parameters found for configured health metrics."


@pytest.mark.asyncio
async def test_health_score_keeps_no_data_when_no_matching_metric_exists(session_factory):
    async with session_factory() as session:
        await _seed_device_with_health_config(session)
        service = HealthConfigService(session)

        result = await service.calculate_health_score(
            device_id="DEVICE-1",
            tenant_id="TENANT-1",
            machine_state="RUNNING",
            telemetry_values={"temperature": 42.0},
        )

    assert result["health_score"] is None
    assert result["parameters_included"] == 0
    assert result["message"] == "No matching telemetry parameters found for configured health metrics."


@pytest.mark.asyncio
async def test_health_score_includes_any_configured_metric_with_matching_telemetry(session_factory):
    async with session_factory() as session:
        await _seed_device_with_configs(
            session,
            [
                {
                    "parameter_name": "temperature",
                    "normal_min": 30.0,
                    "normal_max": 60.0,
                    "weight": 55.0,
                    "ignore_zero_value": False,
                    "is_active": True,
                },
                {
                    "parameter_name": "vibration",
                    "normal_min": 0.0,
                    "normal_max": 3.0,
                    "weight": 45.0,
                    "ignore_zero_value": False,
                    "is_active": True,
                },
            ],
        )
        service = HealthConfigService(session)

        result = await service.calculate_health_score(
            device_id="DEVICE-1",
            tenant_id="TENANT-1",
            machine_state="RUNNING",
            telemetry_values={
                "temperature": 42.0,
                "vibration": 1.5,
            },
        )

    assert result["health_score"] is not None
    assert result["parameters_included"] == 2
    assert result["parameters_skipped"] == 0
    assert len(result["parameter_scores"]) == 2
    assert {row["parameter_name"] for row in result["parameter_scores"]} == {"temperature", "vibration"}


@pytest.mark.asyncio
async def test_health_score_marks_missing_configured_metrics_explicitly(session_factory):
    async with session_factory() as session:
        await _seed_device_with_configs(
            session,
            [
                {
                    "parameter_name": "temperature",
                    "normal_min": 30.0,
                    "normal_max": 60.0,
                    "weight": 60.0,
                    "ignore_zero_value": False,
                    "is_active": True,
                },
                {
                    "parameter_name": "pressure",
                    "normal_min": 2.0,
                    "normal_max": 6.0,
                    "weight": 40.0,
                    "ignore_zero_value": False,
                    "is_active": True,
                },
            ],
        )
        service = HealthConfigService(session)

        result = await service.calculate_health_score(
            device_id="DEVICE-1",
            tenant_id="TENANT-1",
            machine_state="RUNNING",
            telemetry_values={"temperature": 45.0},
        )

    missing = next(row for row in result["parameter_scores"] if row["parameter_name"] == "pressure")
    assert result["health_score"] is not None
    assert result["parameters_included"] == 1
    assert result["parameters_skipped"] == 1
    assert missing["value"] is None
    assert missing["raw_score"] is None
    assert missing["weighted_score"] == 0.0
    assert missing["status"] == "Missing Telemetry"


@pytest.mark.asyncio
async def test_health_score_resolves_power_factor_alias_centrally(session_factory):
    async with session_factory() as session:
        await _seed_device_with_configs(
            session,
            [
                {
                    "parameter_name": "power_factor",
                    "normal_min": 0.85,
                    "normal_max": 1.0,
                    "weight": 100.0,
                    "ignore_zero_value": False,
                    "is_active": True,
                }
            ],
        )
        service = HealthConfigService(session)

        result = await service.calculate_health_score(
            device_id="DEVICE-1",
            tenant_id="TENANT-1",
            machine_state="RUNNING",
            telemetry_values={"pf": 0.93},
        )

    assert result["health_score"] is not None
    assert result["parameter_scores"][0]["telemetry_key"] == "pf"
    assert result["parameter_scores"][0]["resolution"] == "alias"


@pytest.mark.asyncio
async def test_health_score_uses_locked_discrete_boundaries(session_factory):
    async with session_factory() as session:
        await _seed_device_with_configs(
            session,
            [
                {
                    "parameter_name": "temperature",
                    "normal_min": 100.0,
                    "normal_max": 200.0,
                    "weight": 100.0,
                    "ignore_zero_value": False,
                    "is_active": True,
                }
            ],
        )
        service = HealthConfigService(session)

        inside = await service.calculate_health_score(
            device_id="DEVICE-1",
            tenant_id="TENANT-1",
            machine_state="RUNNING",
            telemetry_values={"temperature": 150.0},
        )
        lower_boundary = await service.calculate_health_score(
            device_id="DEVICE-1",
            tenant_id="TENANT-1",
            machine_state="RUNNING",
            telemetry_values={"temperature": 85.0},
        )
        upper_boundary = await service.calculate_health_score(
            device_id="DEVICE-1",
            tenant_id="TENANT-1",
            machine_state="RUNNING",
            telemetry_values={"temperature": 230.0},
        )
        below_lower = await service.calculate_health_score(
            device_id="DEVICE-1",
            tenant_id="TENANT-1",
            machine_state="RUNNING",
            telemetry_values={"temperature": 84.99},
        )
        above_upper = await service.calculate_health_score(
            device_id="DEVICE-1",
            tenant_id="TENANT-1",
            machine_state="RUNNING",
            telemetry_values={"temperature": 230.01},
        )

    assert inside["health_score"] == 100.0
    assert inside["parameter_scores"][0]["raw_score"] == 100.0
    assert inside["parameter_scores"][0]["status"] == "Healthy"

    assert lower_boundary["health_score"] == 50.0
    assert lower_boundary["parameter_scores"][0]["raw_score"] == 50.0
    assert lower_boundary["parameter_scores"][0]["status"] == "Warning"

    assert upper_boundary["health_score"] == 50.0
    assert upper_boundary["parameter_scores"][0]["raw_score"] == 50.0
    assert upper_boundary["parameter_scores"][0]["status"] == "Warning"

    assert below_lower["health_score"] == 0.0
    assert below_lower["parameter_scores"][0]["raw_score"] == 0.0
    assert below_lower["parameter_scores"][0]["status"] == "Critical"

    assert above_upper["health_score"] == 0.0
    assert above_upper["parameter_scores"][0]["raw_score"] == 0.0
    assert above_upper["parameter_scores"][0]["status"] == "Critical"


@pytest.mark.asyncio
async def test_health_score_uses_locked_weighted_aggregation(session_factory):
    async with session_factory() as session:
        await _seed_device_with_configs(
            session,
            [
                {
                    "parameter_name": "param_1",
                    "normal_min": 10.0,
                    "normal_max": 20.0,
                    "weight": 40.0,
                    "ignore_zero_value": False,
                    "is_active": True,
                },
                {
                    "parameter_name": "param_2",
                    "normal_min": 30.0,
                    "normal_max": 40.0,
                    "weight": 30.0,
                    "ignore_zero_value": False,
                    "is_active": True,
                },
                {
                    "parameter_name": "param_3",
                    "normal_min": 100.0,
                    "normal_max": 200.0,
                    "weight": 10.0,
                    "ignore_zero_value": False,
                    "is_active": True,
                },
                {
                    "parameter_name": "param_4",
                    "normal_min": 50.0,
                    "normal_max": 60.0,
                    "weight": 20.0,
                    "ignore_zero_value": False,
                    "is_active": True,
                },
            ],
        )
        service = HealthConfigService(session)

        result = await service.calculate_health_score(
            device_id="DEVICE-1",
            tenant_id="TENANT-1",
            machine_state="RUNNING",
            telemetry_values={
                "param_1": 15.0,
                "param_2": 35.0,
                "param_3": 85.0,
                "param_4": 70.0,
            },
        )

    assert result["health_score"] == 75.0
    assert [row["raw_score"] for row in result["parameter_scores"]] == [100.0, 100.0, 50.0, 0.0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("machine_state", "expected_score"),
    [
        ("RUNNING", 100.0),
        ("IDLE", 100.0),
        ("UNLOAD", 100.0),
        ("OFF", None),
        ("POWER CUT", None),
    ],
)
async def test_health_score_honors_machine_state_eligibility(session_factory, machine_state, expected_score):
    async with session_factory() as session:
        await _seed_device_with_configs(
            session,
            [
                {
                    "parameter_name": "temperature",
                    "normal_min": 30.0,
                    "normal_max": 60.0,
                    "weight": 100.0,
                    "ignore_zero_value": False,
                    "is_active": True,
                }
            ],
        )
        service = HealthConfigService(session)

        result = await service.calculate_health_score(
            device_id="DEVICE-1",
            tenant_id="TENANT-1",
            machine_state=machine_state,
            telemetry_values={"temperature": 45.0},
        )

    assert result["health_score"] == expected_score
    if expected_score is None:
        assert result["status"] == "Standby"
        assert result["parameter_scores"] == []
    else:
        assert result["status"] == "Excellent"
        assert result["parameter_scores"][0]["raw_score"] == 100.0


@pytest.mark.asyncio
async def test_health_score_ignores_zero_when_configured(session_factory):
    async with session_factory() as session:
        await _seed_device_with_configs(
            session,
            [
                {
                    "parameter_name": "current",
                    "normal_min": 8.0,
                    "normal_max": 18.0,
                    "weight": 100.0,
                    "ignore_zero_value": True,
                    "is_active": True,
                }
            ],
        )
        service = HealthConfigService(session)

        result = await service.calculate_health_score(
            device_id="DEVICE-1",
            tenant_id="TENANT-1",
            machine_state="RUNNING",
            telemetry_values={"current": 0.0},
        )

    assert result["health_score"] is None
    assert result["parameters_included"] == 0
    assert result["parameters_skipped"] == 1
    assert result["parameter_scores"][0]["status"] == "Ignored Zero"


@pytest.mark.asyncio
async def test_materialize_device_bucket_computes_health_score_from_aggregate_fields(session_factory, monkeypatch):
    bucket_end = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    bucket_start = bucket_end - timedelta(minutes=5)

    async with session_factory() as session:
        await _seed_device_with_health_config(session)
        service = PerformanceTrendService(session)

        async def fake_fetch(*_args, **_kwargs):
            return (
                {
                    "current": 11.5,
                    "voltage": 232.0,
                },
                1,
            )

        async def fake_uptime(*_args, **_kwargs):
            return 88.0, 30, 26, 0, "uptime-ok"

        monkeypatch.setattr(service, "_fetch_bucket_health_sample", fake_fetch)
        monkeypatch.setattr(service, "_get_uptime_components", fake_uptime)

        result = await service._materialize_device_bucket(
            "DEVICE-1",
            "TENANT-1",
            bucket_start,
            bucket_end,
        )
        await session.commit()

        row = (
            await session.execute(
                DevicePerformanceTrend.__table__.select().where(
                    DevicePerformanceTrend.device_id == "DEVICE-1",
                    DevicePerformanceTrend.tenant_id == "TENANT-1",
                )
            )
        ).mappings().one()

    assert result == "created"
    assert row["health_score"] is not None
    assert row["uptime_percentage"] == 88.0
    assert row["points_used"] == 1


@pytest.mark.asyncio
async def test_materialize_device_bucket_preserves_uptime_when_health_data_missing(session_factory, monkeypatch):
    bucket_end = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    bucket_start = bucket_end - timedelta(minutes=5)

    async with session_factory() as session:
        await _seed_device_with_health_config(session, device_id="DEVICE-2")
        service = PerformanceTrendService(session)

        async def fake_fetch(*_args, **_kwargs):
            return ({"temperature": 42.0}, 1)

        async def fake_uptime(*_args, **_kwargs):
            return 91.0, 30, 27, 0, "uptime-only"

        monkeypatch.setattr(service, "_fetch_bucket_health_sample", fake_fetch)
        monkeypatch.setattr(service, "_get_uptime_components", fake_uptime)

        result = await service._materialize_device_bucket(
            "DEVICE-2",
            "TENANT-1",
            bucket_start,
            bucket_end,
        )
        await session.commit()

        row = (
            await session.execute(
                DevicePerformanceTrend.__table__.select().where(
                    DevicePerformanceTrend.device_id == "DEVICE-2",
                    DevicePerformanceTrend.tenant_id == "TENANT-1",
                )
            )
        ).mappings().one()

    assert result == "created"
    assert row["health_score"] is None
    assert row["uptime_percentage"] == 91.0
    assert row["is_valid"] is True


@pytest.mark.asyncio
async def test_backfill_health_scores_updates_only_selected_null_rows(session_factory, monkeypatch):
    bucket_end = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(second=0, microsecond=0)
    bucket_start = bucket_end - timedelta(minutes=5)
    untouched_bucket = bucket_start - timedelta(minutes=5)

    async with session_factory() as session:
        await _seed_device_with_health_config(session, device_id="DEVICE-3", tenant_id="TENANT-1")
        await _seed_device_with_health_config(session, device_id="DEVICE-4", tenant_id="TENANT-2")
        session.add_all(
            [
                DevicePerformanceTrend(
                    device_id="DEVICE-3",
                    tenant_id="TENANT-1",
                    bucket_start_utc=bucket_start,
                    bucket_end_utc=bucket_end,
                    bucket_timezone="Asia/Kolkata",
                    interval_minutes=5,
                    health_score=None,
                    uptime_percentage=80.0,
                    planned_minutes=30,
                    effective_minutes=24,
                    break_minutes=0,
                    points_used=0,
                    is_valid=True,
                    message="No matching telemetry parameters found for configured health metrics. | uptime-preserved",
                ),
                DevicePerformanceTrend(
                    device_id="DEVICE-3",
                    tenant_id="TENANT-1",
                    bucket_start_utc=untouched_bucket,
                    bucket_end_utc=bucket_start,
                    bucket_timezone="Asia/Kolkata",
                    interval_minutes=5,
                    health_score=None,
                    uptime_percentage=70.0,
                    planned_minutes=30,
                    effective_minutes=21,
                    break_minutes=0,
                    points_used=0,
                    is_valid=True,
                    message="outside-range",
                ),
                DevicePerformanceTrend(
                    device_id="DEVICE-4",
                    tenant_id="TENANT-2",
                    bucket_start_utc=bucket_start,
                    bucket_end_utc=bucket_end,
                    bucket_timezone="Asia/Kolkata",
                    interval_minutes=5,
                    health_score=None,
                    uptime_percentage=75.0,
                    planned_minutes=30,
                    effective_minutes=22,
                    break_minutes=0,
                    points_used=0,
                    is_valid=True,
                    message="other-tenant",
                ),
            ]
        )
        await session.commit()

        async def fake_fetch(device_id: str, tenant_id: str, *_args):
            if (device_id, tenant_id) == ("DEVICE-3", "TENANT-1"):
                return ({"current": 12.0, "voltage": 232.0}, 1)
            return ({"temperature": 55.0}, 1)

        service = PerformanceTrendService(session)
        monkeypatch.setattr(service, "_fetch_bucket_health_sample", fake_fetch)

        summary = await service.backfill_health_scores(
            start_utc=bucket_start,
            end_utc=bucket_end + timedelta(minutes=1),
            tenant_id="TENANT-1",
            only_missing_health=True,
        )

        rows = (
            await session.execute(
                DevicePerformanceTrend.__table__.select().order_by(
                    DevicePerformanceTrend.tenant_id.asc(),
                    DevicePerformanceTrend.device_id.asc(),
                    DevicePerformanceTrend.bucket_start_utc.asc(),
                )
            )
        ).mappings().all()

    assert summary["scanned"] == 1
    assert summary["updated"] == 1
    assert summary["failed"] == 0
    target_rows = [row for row in rows if row["device_id"] == "DEVICE-3" and row["tenant_id"] == "TENANT-1"]
    assert len(target_rows) == 2
    target = next(row for row in target_rows if row["health_score"] is not None)
    assert target["health_score"] is not None
    assert target["uptime_percentage"] == 80.0
    assert target["message"] == "Health score calculated from 2 parameter(s) | uptime-preserved"
    outside = next(row for row in target_rows if row["health_score"] is None)
    assert outside["health_score"] is None
    other_tenant = next(row for row in rows if row["device_id"] == "DEVICE-4")
    assert other_tenant["health_score"] is None


@pytest.mark.asyncio
async def test_backfill_health_scores_is_idempotent_and_preserves_valid_rows_by_default(session_factory, monkeypatch):
    bucket_end = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(second=0, microsecond=0)
    bucket_start = bucket_end - timedelta(minutes=5)

    async with session_factory() as session:
        await _seed_device_with_health_config(session, device_id="DEVICE-5", tenant_id="TENANT-1")
        session.add_all(
            [
                DevicePerformanceTrend(
                    device_id="DEVICE-5",
                    tenant_id="TENANT-1",
                    bucket_start_utc=bucket_start,
                    bucket_end_utc=bucket_end,
                    bucket_timezone="Asia/Kolkata",
                    interval_minutes=5,
                    health_score=None,
                    uptime_percentage=82.0,
                    planned_minutes=30,
                    effective_minutes=25,
                    break_minutes=0,
                    points_used=0,
                    is_valid=True,
                    message="No matching telemetry parameters found for configured health metrics.",
                ),
                DevicePerformanceTrend(
                    device_id="DEVICE-5",
                    tenant_id="TENANT-1",
                    bucket_start_utc=bucket_start - timedelta(minutes=5),
                    bucket_end_utc=bucket_start,
                    bucket_timezone="Asia/Kolkata",
                    interval_minutes=5,
                    health_score=88.0,
                    uptime_percentage=79.0,
                    planned_minutes=30,
                    effective_minutes=24,
                    break_minutes=0,
                    points_used=1,
                    is_valid=True,
                    message="already-good",
                ),
            ]
        )
        await session.commit()

        async def fake_fetch(*_args, **_kwargs):
            return ({"current": 11.0, "voltage": 231.0}, 1)

        service = PerformanceTrendService(session)
        monkeypatch.setattr(service, "_fetch_bucket_health_sample", fake_fetch)

        first = await service.backfill_health_scores(
            start_utc=bucket_start - timedelta(minutes=5),
            end_utc=bucket_end + timedelta(minutes=1),
            tenant_id="TENANT-1",
            only_missing_health=True,
        )
        second = await service.backfill_health_scores(
            start_utc=bucket_start - timedelta(minutes=5),
            end_utc=bucket_end + timedelta(minutes=1),
            tenant_id="TENANT-1",
            only_missing_health=True,
        )

        rows = (
            await session.execute(
                DevicePerformanceTrend.__table__.select().where(
                    DevicePerformanceTrend.device_id == "DEVICE-5",
                    DevicePerformanceTrend.tenant_id == "TENANT-1",
                )
            )
        ).mappings().all()

    assert first["updated"] == 1
    assert second["updated"] == 0
    assert second["unchanged"] == 0
    healthy_row = next(row for row in rows if row["health_score"] == 88.0)
    assert healthy_row["message"] == "already-good"


@pytest.mark.asyncio
async def test_backfill_health_scores_can_rewrite_existing_health_when_requested(session_factory, monkeypatch):
    bucket_end = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(second=0, microsecond=0)
    bucket_start = bucket_end - timedelta(minutes=5)

    async with session_factory() as session:
        await _seed_device_with_health_config(session, device_id="DEVICE-6", tenant_id="TENANT-1")
        session.add(
            DevicePerformanceTrend(
                device_id="DEVICE-6",
                tenant_id="TENANT-1",
                bucket_start_utc=bucket_start,
                bucket_end_utc=bucket_end,
                bucket_timezone="Asia/Kolkata",
                interval_minutes=5,
                health_score=95.0,
                uptime_percentage=85.0,
                planned_minutes=30,
                effective_minutes=26,
                break_minutes=0,
                points_used=1,
                is_valid=True,
                message="old-health",
            )
        )
        await session.commit()

        async def fake_fetch(*_args, **_kwargs):
            return ({"current": 30.0, "voltage": 231.0}, 1)

        service = PerformanceTrendService(session)
        monkeypatch.setattr(service, "_fetch_bucket_health_sample", fake_fetch)

        summary = await service.backfill_health_scores(
            start_utc=bucket_start,
            end_utc=bucket_end + timedelta(minutes=1),
            tenant_id="TENANT-1",
            only_missing_health=False,
        )

        row = (
            await session.execute(
                DevicePerformanceTrend.__table__.select().where(
                    DevicePerformanceTrend.device_id == "DEVICE-6",
                    DevicePerformanceTrend.tenant_id == "TENANT-1",
                )
            )
        ).mappings().one()

    assert summary["scanned"] == 1
    assert summary["updated"] == 1
    assert row["health_score"] != 95.0
    assert row["uptime_percentage"] == 85.0


@pytest.mark.asyncio
async def test_get_trends_self_heals_recent_null_health_rows(session_factory, monkeypatch):
    bucket_end = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(second=0, microsecond=0)
    bucket_start = bucket_end - timedelta(minutes=5)

    async with session_factory() as session:
        await _seed_device_with_health_config(session, device_id="DEVICE-7", tenant_id="TENANT-1")
        await _seed_device_with_health_config(session, device_id="DEVICE-7-T2", tenant_id="TENANT-2")
        session.add_all(
            [
                DevicePerformanceTrend(
                    device_id="DEVICE-7",
                    tenant_id="TENANT-1",
                    bucket_start_utc=bucket_start,
                    bucket_end_utc=bucket_end,
                    bucket_timezone="Asia/Kolkata",
                    interval_minutes=5,
                    health_score=None,
                    uptime_percentage=84.0,
                    planned_minutes=30,
                    effective_minutes=25,
                    break_minutes=0,
                    points_used=0,
                    is_valid=True,
                    message="No matching telemetry parameters found for configured health metrics.",
                ),
                DevicePerformanceTrend(
                    device_id="DEVICE-7-T2",
                    tenant_id="TENANT-2",
                    bucket_start_utc=bucket_start,
                    bucket_end_utc=bucket_end,
                    bucket_timezone="Asia/Kolkata",
                    interval_minutes=5,
                    health_score=None,
                    uptime_percentage=61.0,
                    planned_minutes=30,
                    effective_minutes=18,
                    break_minutes=0,
                    points_used=0,
                    is_valid=True,
                    message="other-tenant",
                ),
            ]
        )
        await session.commit()

        async def fake_fetch(device_id: str, tenant_id: str, *_args):
            if tenant_id == "TENANT-1":
                return ({"current": 12.0, "voltage": 231.0}, 1)
            return ({"temperature": 50.0}, 1)

        service = PerformanceTrendService(session)
        monkeypatch.setattr(service, "_fetch_bucket_health_sample", fake_fetch)

        result = await service.get_trends(
            device_id="DEVICE-7",
            tenant_id="TENANT-1",
            metric="health",
            range_key="24h",
        )

        rows = (
            await session.execute(
                DevicePerformanceTrend.__table__.select().where(
                    DevicePerformanceTrend.device_id.in_(["DEVICE-7", "DEVICE-7-T2"]),
                )
            )
        ).mappings().all()

    assert result["total_points"] == 1
    assert result["points"][0]["health_score"] is not None
    assert result["points"][0]["uptime_percentage"] == 84.0
    assert len(rows) == 2
    tenant_one = next(row for row in rows if row["tenant_id"] == "TENANT-1")
    tenant_two = next(row for row in rows if row["tenant_id"] == "TENANT-2")
    assert tenant_one["health_score"] is not None
    assert tenant_two["health_score"] is None


@pytest.mark.asyncio
async def test_recent_health_repair_rewrites_existing_recent_scores_for_config_change(session_factory, monkeypatch):
    bucket_end = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(second=0, microsecond=0)
    bucket_start = bucket_end - timedelta(minutes=5)

    async with session_factory() as session:
        await _seed_device_with_health_config(session, device_id="DEVICE-8", tenant_id="TENANT-1")
        session.add(
            DevicePerformanceTrend(
                device_id="DEVICE-8",
                tenant_id="TENANT-1",
                bucket_start_utc=bucket_start,
                bucket_end_utc=bucket_end,
                bucket_timezone="Asia/Kolkata",
                interval_minutes=5,
                health_score=96.0,
                uptime_percentage=87.0,
                planned_minutes=30,
                effective_minutes=26,
                break_minutes=0,
                points_used=1,
                is_valid=True,
                message="previous-health",
            )
        )
        await session.commit()

        async def fake_fetch(*_args, **_kwargs):
            return ({"current": 30.0, "voltage": 231.0}, 1)

        service = PerformanceTrendService(session)
        monkeypatch.setattr(service, "_fetch_bucket_health_sample", fake_fetch)

        summary = await service.repair_recent_health_window(
            device_id="DEVICE-8",
            tenant_id="TENANT-1",
            rewrite_existing_health=True,
        )
        row = (
            await session.execute(
                DevicePerformanceTrend.__table__.select().where(
                    DevicePerformanceTrend.device_id == "DEVICE-8",
                    DevicePerformanceTrend.tenant_id == "TENANT-1",
                )
            )
        ).mappings().one()

    assert summary["scanned"] == 1
    assert summary["updated"] == 1
    assert row["health_score"] != 96.0
    assert row["uptime_percentage"] == 87.0


@pytest.mark.asyncio
async def test_live_score_and_trend_score_agree_for_same_tenant(session_factory, monkeypatch):
    bucket_end = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(second=0, microsecond=0)
    bucket_start = bucket_end - timedelta(minutes=5)

    async with session_factory() as session:
        await _seed_device_with_health_config(session, device_id="DEVICE-9", tenant_id="TENANT-1")
        health_service = HealthConfigService(session)
        trend_service = PerformanceTrendService(session)

        telemetry_values = {"current": 12.0, "voltage": 231.0}
        live_result = await health_service.calculate_health_score(
            device_id="DEVICE-9",
            tenant_id="TENANT-1",
            machine_state="RUNNING",
            telemetry_values=telemetry_values,
        )

        async def fake_fetch(*_args, **_kwargs):
            return (telemetry_values, 1)

        async def fake_uptime(*_args, **_kwargs):
            return 88.0, 30, 26, 0, "uptime-ok"

        monkeypatch.setattr(trend_service, "_fetch_bucket_health_sample", fake_fetch)
        monkeypatch.setattr(trend_service, "_get_uptime_components", fake_uptime)

        result = await trend_service._materialize_device_bucket(
            "DEVICE-9",
            "TENANT-1",
            bucket_start,
            bucket_end,
        )
        await session.commit()

        row = (
            await session.execute(
                DevicePerformanceTrend.__table__.select().where(
                    DevicePerformanceTrend.device_id == "DEVICE-9",
                    DevicePerformanceTrend.tenant_id == "TENANT-1",
                )
            )
        ).mappings().one()

    assert result == "created"
    assert live_result["health_score"] is not None
    assert row["health_score"] == live_result["health_score"]


@pytest.mark.asyncio
async def test_live_score_and_trend_score_agree_for_custom_metric(session_factory, monkeypatch):
    bucket_end = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(second=0, microsecond=0)
    bucket_start = bucket_end - timedelta(minutes=5)

    async with session_factory() as session:
        await _seed_device_with_configs(
            session,
            [
                {
                    "parameter_name": "temperature",
                    "normal_min": 30.0,
                    "normal_max": 60.0,
                    "weight": 60.0,
                    "ignore_zero_value": False,
                    "is_active": True,
                },
                {
                    "parameter_name": "pressure",
                    "normal_min": 2.0,
                    "normal_max": 6.0,
                    "weight": 40.0,
                    "ignore_zero_value": False,
                    "is_active": True,
                },
            ],
            device_id="DEVICE-10",
            tenant_id="TENANT-1",
        )
        health_service = HealthConfigService(session)
        trend_service = PerformanceTrendService(session)

        telemetry_values = {"temperature": 43.0, "pressure": 4.5}
        live_result = await health_service.calculate_health_score(
            device_id="DEVICE-10",
            tenant_id="TENANT-1",
            machine_state="RUNNING",
            telemetry_values=telemetry_values,
        )

        async def fake_fetch(*_args, **_kwargs):
            return (telemetry_values, 1)

        async def fake_uptime(*_args, **_kwargs):
            return 88.0, 30, 26, 0, "uptime-ok"

        monkeypatch.setattr(trend_service, "_fetch_bucket_health_sample", fake_fetch)
        monkeypatch.setattr(trend_service, "_get_uptime_components", fake_uptime)

        result = await trend_service._materialize_device_bucket(
            "DEVICE-10",
            "TENANT-1",
            bucket_start,
            bucket_end,
        )
        await session.commit()

        row = (
            await session.execute(
                DevicePerformanceTrend.__table__.select().where(
                    DevicePerformanceTrend.device_id == "DEVICE-10",
                    DevicePerformanceTrend.tenant_id == "TENANT-1",
                )
            )
        ).mappings().one()

    assert result == "created"
    assert live_result["health_score"] is not None
    assert row["health_score"] == live_result["health_score"]


@pytest.mark.asyncio
async def test_materialize_device_bucket_uses_latest_bucket_sample_for_health_not_mean(session_factory, monkeypatch):
    bucket_end = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    bucket_start = bucket_end - timedelta(minutes=5)

    async with session_factory() as session:
        await _seed_device_with_health_config(session, device_id="DEVICE-LATEST", tenant_id="TENANT-1")
        service = PerformanceTrendService(session)

        async def fake_latest(*_args, **_kwargs):
            return ({"current": 11.2, "voltage": 231.5}, 1)

        async def fake_mean(*_args, **_kwargs):
            return ({"temperature": 44.0}, 1)

        async def fake_uptime(*_args, **_kwargs):
            return 87.0, 30, 26, 0, "uptime-ok"

        monkeypatch.setattr(service, "_fetch_bucket_health_sample", fake_latest)
        monkeypatch.setattr(service, "_fetch_bucket_telemetry_mean", fake_mean)
        monkeypatch.setattr(service, "_get_uptime_components", fake_uptime)

        result = await service._materialize_device_bucket(
            "DEVICE-LATEST",
            "TENANT-1",
            bucket_start,
            bucket_end,
        )
        await session.commit()

        row = (
            await session.execute(
                DevicePerformanceTrend.__table__.select().where(
                    DevicePerformanceTrend.device_id == "DEVICE-LATEST",
                    DevicePerformanceTrend.tenant_id == "TENANT-1",
                )
            )
        ).mappings().one()

    assert result == "created"
    assert row["health_score"] is not None
    assert row["points_used"] == 1


@pytest.mark.asyncio
async def test_fetch_bucket_health_sample_uses_recent_projection_rows_without_data_service(session_factory, monkeypatch):
    bucket_end = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    bucket_start = bucket_end - timedelta(minutes=5)

    async with session_factory() as session:
        await _seed_device_with_health_config(session, device_id="DEVICE-RECENT", tenant_id="TENANT-1")
        session.add_all(
            [
                DeviceRecentTelemetrySample(
                    device_id="DEVICE-RECENT",
                    tenant_id="TENANT-1",
                    sample_ts=bucket_start + timedelta(minutes=1),
                    projection_version=1,
                    runtime_status="running",
                    load_state="running",
                    current_band="normal",
                    telemetry_json=json.dumps(
                        {
                            "timestamp": (bucket_start + timedelta(minutes=1)).isoformat(),
                            "current": 11.2,
                            "voltage": 231.5,
                        }
                    ),
                ),
                DeviceRecentTelemetrySample(
                    device_id="DEVICE-RECENT",
                    tenant_id="TENANT-1",
                    sample_ts=bucket_start + timedelta(minutes=4),
                    projection_version=2,
                    runtime_status="running",
                    load_state="running",
                    current_band="normal",
                    telemetry_json=json.dumps(
                        {
                            "timestamp": (bucket_start + timedelta(minutes=4)).isoformat(),
                            "current": 12.1,
                            "voltage": 232.0,
                        }
                    ),
                ),
            ]
        )
        await session.commit()

        service = PerformanceTrendService(session)
        monkeypatch.setattr(
            performance_trends_module,
            "request_with_retries",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("data-service must not be called")),
        )

        values, points_used = await service._fetch_bucket_health_sample(
            "DEVICE-RECENT",
            "TENANT-1",
            bucket_start,
            bucket_end,
        )

    assert values["current"] == 12.1
    assert values["voltage"] == 232.0
    assert points_used == 2


@pytest.mark.asyncio
async def test_backfill_health_scores_repairs_null_rows_using_latest_bucket_sample(session_factory, monkeypatch):
    bucket_end = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(second=0, microsecond=0)
    bucket_start = bucket_end - timedelta(minutes=5)

    async with session_factory() as session:
        await _seed_device_with_health_config(session, device_id="DEVICE-REPAIR", tenant_id="TENANT-1")
        session.add(
            DevicePerformanceTrend(
                device_id="DEVICE-REPAIR",
                tenant_id="TENANT-1",
                bucket_start_utc=bucket_start,
                bucket_end_utc=bucket_end,
                bucket_timezone="Asia/Kolkata",
                interval_minutes=5,
                health_score=None,
                uptime_percentage=83.0,
                planned_minutes=30,
                effective_minutes=25,
                break_minutes=0,
                points_used=0,
                is_valid=True,
                message="No matching telemetry parameters found for configured health metrics. | uptime-preserved",
            )
        )
        await session.commit()

        service = PerformanceTrendService(session)

        async def fake_latest(*_args, **_kwargs):
            return ({"current": 10.5, "voltage": 229.0}, 1)

        async def fake_mean(*_args, **_kwargs):
            return ({}, 0)

        monkeypatch.setattr(service, "_fetch_bucket_health_sample", fake_latest)
        monkeypatch.setattr(service, "_fetch_bucket_telemetry_mean", fake_mean)

        summary = await service.backfill_health_scores(
            start_utc=bucket_start,
            end_utc=bucket_end + timedelta(minutes=1),
            tenant_id="TENANT-1",
            device_id="DEVICE-REPAIR",
            only_missing_health=True,
        )

        row = (
            await session.execute(
                DevicePerformanceTrend.__table__.select().where(
                    DevicePerformanceTrend.device_id == "DEVICE-REPAIR",
                    DevicePerformanceTrend.tenant_id == "TENANT-1",
                )
            )
        ).mappings().one()

    assert summary["updated"] == 1
    assert row["health_score"] is not None
    assert row["uptime_percentage"] == 83.0


@pytest.mark.asyncio
async def test_get_trends_self_heals_recent_null_uptime_rows(session_factory, monkeypatch):
    bucket_end = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(second=0, microsecond=0)
    bucket_start = bucket_end - timedelta(minutes=5)

    async with session_factory() as session:
        await _seed_device_with_health_config(session, device_id="DEVICE-10", tenant_id="TENANT-1")
        session.add(
            DevicePerformanceTrend(
                device_id="DEVICE-10",
                tenant_id="TENANT-1",
                bucket_start_utc=bucket_start,
                bucket_end_utc=bucket_end,
                bucket_timezone="Asia/Kolkata",
                interval_minutes=5,
                health_score=77.0,
                uptime_percentage=None,
                planned_minutes=0,
                effective_minutes=0,
                break_minutes=0,
                points_used=1,
                is_valid=True,
                message="Health score calculated from 2 parameter(s)",
            )
        )
        await session.commit()

        service = PerformanceTrendService(session)

        async def fake_uptime(*_args, **_kwargs):
            return 88.0, 30, 26, 4, "Runtime uptime computed from telemetry for recent shift window(s): D1"

        monkeypatch.setattr(service, "_get_uptime_components", fake_uptime)

        result = await service.get_trends(
            device_id="DEVICE-10",
            tenant_id="TENANT-1",
            metric="uptime",
            range_key="24h",
        )

        row = (
            await session.execute(
                DevicePerformanceTrend.__table__.select().where(
                    DevicePerformanceTrend.device_id == "DEVICE-10",
                    DevicePerformanceTrend.tenant_id == "TENANT-1",
                )
            )
        ).mappings().one()

    assert result["total_points"] == 1
    assert result["points"][0]["uptime_percentage"] == 88.0
    assert row["uptime_percentage"] == 88.0
    assert row["planned_minutes"] == 30
    assert row["effective_minutes"] == 26
    assert row["break_minutes"] == 4
    assert row["health_score"] == 77.0
    assert row["message"] == "Health score calculated from 2 parameter(s) | Runtime uptime computed from telemetry for recent shift window(s): D1"


@pytest.mark.asyncio
async def test_calculate_uptime_for_window_uses_recent_shift_overlap_even_after_shift_end(session_factory, monkeypatch):
    window_start = datetime(2026, 4, 4, 4, 10, tzinfo=timezone.utc)
    window_end = datetime(2026, 4, 4, 4, 40, tzinfo=timezone.utc)

    async with session_factory() as session:
        session.add(
            Device(
                device_id="DEVICE-11",
                tenant_id="TENANT-1",
                plant_id="PLANT-1",
                device_name="Compressor",
                device_type="compressor",
                data_source_type="metered",
            )
        )
        await session.commit()
        await _seed_shift(
            session,
            device_id="DEVICE-11",
            tenant_id="TENANT-1",
            shift_name="D1",
            shift_start="09:00",
            shift_end="10:00",
        )

        service = ShiftService(session)

        async def fake_fetch(*_args, **_kwargs):
            return [
                {"timestamp": "2026-04-04T04:10:00+00:00", "power": 10.0},
                {"timestamp": "2026-04-04T04:20:00+00:00", "power": 10.0},
                {"timestamp": "2026-04-04T04:30:00+00:00", "power": 10.0},
            ]

        monkeypatch.setattr(service, "_fetch_telemetry_window", fake_fetch)

        result = await service.calculate_uptime_for_window(
            "DEVICE-11",
            "TENANT-1",
            window_start,
            window_end,
        )

    assert result["uptime_percentage"] == 100.0
    assert result["total_planned_minutes"] == 20
    assert result["total_effective_minutes"] == 20
    assert result["actual_running_minutes"] == 20
    assert result["message"] == "Runtime uptime computed from telemetry for recent shift window(s): D1"
