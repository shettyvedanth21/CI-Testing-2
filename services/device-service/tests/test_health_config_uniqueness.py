from __future__ import annotations

import os
import sys
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
from app.models.device import Device, ParameterHealthConfig
from app.schemas.device import ParameterHealthConfigCreate, ParameterHealthConfigUpdate
from app.services.health_config import DuplicateHealthConfigError, HealthConfigService


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


async def _seed_device(session, device_id: str = "DEVICE-1", tenant_id: str = "TENANT-1") -> None:
    session.add(
        Device(
            device_id=device_id,
            tenant_id=tenant_id,
            plant_id="PLANT-1",
            device_name="Device 1",
            device_type="compressor",
        )
    )
    await session.commit()


@pytest.mark.asyncio
async def test_validate_weights_fails_when_duplicate_active_rows_exist(session_factory):
    async with session_factory() as session:
        await _seed_device(session)
        session.add_all(
            [
                ParameterHealthConfig(
                    device_id="DEVICE-1",
                    tenant_id="TENANT-1",
                    parameter_name="temperature",
                    canonical_parameter_name="temperature",
                    normal_min=20.0,
                    normal_max=60.0,
                    weight=100.0,
                    ignore_zero_value=False,
                    is_active=True,
                ),
                ParameterHealthConfig(
                    device_id="DEVICE-1",
                    tenant_id="TENANT-1",
                    parameter_name="temperature",
                    canonical_parameter_name="temperature",
                    normal_min=20.0,
                    normal_max=60.0,
                    weight=100.0,
                    ignore_zero_value=False,
                    is_active=True,
                ),
            ]
        )
        await session.commit()

        service = HealthConfigService(session)
        validation = await service.validate_weights("DEVICE-1", "TENANT-1")

    assert validation["is_valid"] is False
    assert validation["total_weight"] == 200.0
    assert len(validation["parameters"]) == 2


@pytest.mark.asyncio
async def test_create_health_config_rejects_duplicate_canonical_parameter(session_factory):
    async with session_factory() as session:
        await _seed_device(session)
        service = HealthConfigService(session)

        created = await service.create_health_config(
            ParameterHealthConfigCreate(
                device_id="DEVICE-1",
                tenant_id="TENANT-1",
                parameter_name="temperature",
                normal_min=20.0,
                normal_max=60.0,
                weight=100.0,
                ignore_zero_value=False,
                is_active=True,
            )
        )

        with pytest.raises(DuplicateHealthConfigError):
            await service.create_health_config(
                ParameterHealthConfigCreate(
                    device_id="DEVICE-1",
                    tenant_id="TENANT-1",
                parameter_name="temperature",
                normal_min=22.0,
                normal_max=62.0,
                weight=100.0,
                ignore_zero_value=False,
                is_active=True,
                )
            )

        configs = await service.get_health_configs_by_device("DEVICE-1", "TENANT-1")

    assert created.canonical_parameter_name == "temperature"
    assert len(configs) == 1
    assert configs[0].id == created.id


@pytest.mark.asyncio
async def test_bulk_create_or_update_matches_canonical_alias_instead_of_creating_duplicate(session_factory):
    async with session_factory() as session:
        await _seed_device(session)
        service = HealthConfigService(session)

        created = await service.bulk_create_or_update(
            "DEVICE-1",
            "TENANT-1",
            [
                {
                    "device_id": "DEVICE-1",
                    "tenant_id": "TENANT-1",
                    "parameter_name": "power_factor",
                    "normal_min": 0.85,
                    "normal_max": 1.0,
                    "weight": 100.0,
                    "ignore_zero_value": False,
                    "is_active": True,
                }
            ],
        )
        updated = await service.bulk_create_or_update(
            "DEVICE-1",
            "TENANT-1",
            [
                {
                    "device_id": "DEVICE-1",
                    "tenant_id": "TENANT-1",
                    "parameter_name": "pf",
                    "normal_min": 0.9,
                    "normal_max": 1.0,
                    "weight": 100.0,
                    "ignore_zero_value": False,
                    "is_active": True,
                }
            ],
        )

        configs = await service.get_health_configs_by_device("DEVICE-1", "TENANT-1")

    assert len(created) == 1
    assert len(updated) == 1
    assert len(configs) == 1
    assert configs[0].id == created[0].id == updated[0].id
    assert configs[0].parameter_name == "pf"
    assert configs[0].canonical_parameter_name == "power_factor"
    assert configs[0].normal_min == 0.9


@pytest.mark.asyncio
async def test_update_health_config_rejects_collision_with_existing_canonical_parameter(session_factory):
    async with session_factory() as session:
        await _seed_device(session)
        service = HealthConfigService(session)

        current = await service.create_health_config(
            ParameterHealthConfigCreate(
                device_id="DEVICE-1",
                tenant_id="TENANT-1",
                parameter_name="current",
                normal_min=8.0,
                normal_max=18.0,
                weight=60.0,
                ignore_zero_value=False,
                is_active=True,
            )
        )
        voltage = await service.create_health_config(
            ParameterHealthConfigCreate(
                device_id="DEVICE-1",
                tenant_id="TENANT-1",
                parameter_name="voltage",
                normal_min=210.0,
                normal_max=240.0,
                weight=40.0,
                ignore_zero_value=False,
                is_active=True,
            )
        )

        updated = await service.update_health_config(
            current.id,
            "DEVICE-1",
            "TENANT-1",
            ParameterHealthConfigUpdate(weight=100.0),
        )

        with pytest.raises(DuplicateHealthConfigError):
            await service.update_health_config(
                voltage.id,
                "DEVICE-1",
                "TENANT-1",
                ParameterHealthConfigUpdate(parameter_name="current_a"),
            )

        configs = await service.get_health_configs_by_device("DEVICE-1", "TENANT-1")

    assert updated is not None
    assert updated.weight == 100.0
    assert len(configs) == 2


@pytest.mark.asyncio
async def test_create_health_config_rejects_cross_tenant_device_reference(session_factory):
    async with session_factory() as session:
        await _seed_device(session, device_id="DEVICE-1", tenant_id="TENANT-A")
        service = HealthConfigService(session)

        with pytest.raises(ValueError, match="Device 'DEVICE-1' not found"):
            await service.create_health_config(
                ParameterHealthConfigCreate(
                    device_id="DEVICE-1",
                    tenant_id="TENANT-B",
                    parameter_name="current",
                    normal_min=8.0,
                    normal_max=18.0,
                    weight=100.0,
                    ignore_zero_value=False,
                    is_active=True,
                )
            )


@pytest.mark.asyncio
async def test_validate_weights_succeeds_when_active_weights_sum_to_100_and_inactive_configs_are_ignored(session_factory):
    async with session_factory() as session:
        await _seed_device(session)
        session.add_all(
            [
                ParameterHealthConfig(
                    device_id="DEVICE-1",
                    tenant_id="TENANT-1",
                    parameter_name="current",
                    canonical_parameter_name="current",
                    normal_min=8.0,
                    normal_max=18.0,
                    weight=60.0,
                    ignore_zero_value=False,
                    is_active=True,
                ),
                ParameterHealthConfig(
                    device_id="DEVICE-1",
                    tenant_id="TENANT-1",
                    parameter_name="voltage",
                    canonical_parameter_name="voltage",
                    normal_min=210.0,
                    normal_max=240.0,
                    weight=40.0,
                    ignore_zero_value=False,
                    is_active=True,
                ),
                ParameterHealthConfig(
                    device_id="DEVICE-1",
                    tenant_id="TENANT-1",
                    parameter_name="pf",
                    canonical_parameter_name="power_factor",
                    normal_min=0.8,
                    normal_max=1.0,
                    weight=25.0,
                    ignore_zero_value=False,
                    is_active=False,
                ),
            ]
        )
        await session.commit()

        service = HealthConfigService(session)
        validation = await service.validate_weights("DEVICE-1", "TENANT-1")

    assert validation["is_valid"] is True
    assert validation["total_weight"] == 100.0
    assert validation["message"] == "Weights sum to 100%"
    assert len(validation["parameters"]) == 3


@pytest.mark.asyncio
async def test_bulk_create_or_update_rejects_duplicate_canonical_parameters_in_same_payload(session_factory):
    async with session_factory() as session:
        await _seed_device(session)
        service = HealthConfigService(session)

        with pytest.raises(DuplicateHealthConfigError):
            await service.bulk_create_or_update(
                "DEVICE-1",
                "TENANT-1",
                [
                    {
                        "device_id": "DEVICE-1",
                        "tenant_id": "TENANT-1",
                        "parameter_name": "current",
                        "normal_min": 8.0,
                        "normal_max": 18.0,
                        "weight": 50.0,
                        "ignore_zero_value": False,
                        "is_active": True,
                    },
                    {
                        "device_id": "DEVICE-1",
                        "tenant_id": "TENANT-1",
                        "parameter_name": "current_a",
                        "normal_min": 9.0,
                        "normal_max": 19.0,
                        "weight": 50.0,
                        "ignore_zero_value": False,
                        "is_active": True,
                    },
                ],
            )
