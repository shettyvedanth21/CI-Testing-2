from pathlib import Path
import os
import sys

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

os.environ["DATABASE_URL"] = "mysql+aiomysql://test:test@127.0.0.1:3306/test_db"

from app.database import Base
from app.models.device import Device, DeviceProperty
from app.services.device_property import DevicePropertyService


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


async def _seed_device(session) -> None:
    session.add(
        Device(
            device_id="KNOWN-DEVICE",
            tenant_id="tenant-a",
            plant_id="PLANT-1",
            device_name="Known Device",
            device_type="compressor",
        )
    )
    await session.commit()


@pytest.mark.asyncio
async def test_sync_from_telemetry_batch_creates_expected_properties(session_factory):
    async with session_factory() as session:
        await _seed_device(session)
        service = DevicePropertyService(session)

        updated = await service.sync_from_telemetry_batch(
            tenant_id="tenant-a",
            telemetry_by_device={
                "KNOWN-DEVICE": {
                    "current": 12.5,
                    "status_text": "running",
                    "enabled": True,
                    "tenant_id": "tenant-a",
                }
            },
        )
        await session.commit()

    assert set(updated) == {"KNOWN-DEVICE"}
    assert [prop.property_name for prop in updated["KNOWN-DEVICE"]] == ["current", "status_text"]

    async with session_factory() as session:
        rows = (
            await session.execute(
                select(DeviceProperty).where(DeviceProperty.device_id == "KNOWN-DEVICE")
            )
        ).scalars().all()

    assert {row.property_name for row in rows} == {"current", "status_text"}


@pytest.mark.asyncio
async def test_sync_from_telemetry_batch_updates_existing_property_without_duplicate_rows(session_factory):
    async with session_factory() as session:
        await _seed_device(session)
        service = DevicePropertyService(session)

        await service.sync_from_telemetry_batch(
            tenant_id="tenant-a",
            telemetry_by_device={"KNOWN-DEVICE": {"current": 10}},
        )
        await session.commit()

        await service.sync_from_telemetry_batch(
            tenant_id="tenant-a",
            telemetry_by_device={"KNOWN-DEVICE": {"current": 10.5}},
        )
        await session.commit()

    async with session_factory() as session:
        rows = (
            await session.execute(
                select(DeviceProperty).where(DeviceProperty.device_id == "KNOWN-DEVICE")
            )
        ).scalars().all()

    assert len(rows) == 1
    assert rows[0].property_name == "current"
    assert rows[0].data_type == "float"
    assert rows[0].is_numeric is True
