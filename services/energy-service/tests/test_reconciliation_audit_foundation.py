from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "services" / "energy-service"))
sys.path.insert(1, str(ROOT))

from app.models import Base, EnergyReconcileAudit  # noqa: E402
from app.repositories.reconciliation_audit_repository import ReconciliationAuditRepository  # noqa: E402


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_reconciliation_audit_persists_rich_foundation_fields(session_factory):
    async with session_factory() as session:
        repo = ReconciliationAuditRepository(session)
        item = await repo.create_item(
            run_id="run-001",
            tenant_id="SH00000001",
            device_id="DEVICE-1",
            day=date(2026, 4, 20),
            period_type="device_day",
            period_start=datetime(2026, 4, 20, 0, 0, tzinfo=timezone.utc),
            period_end=datetime(2026, 4, 20, 23, 59, tzinfo=timezone.utc),
            expected_energy_kwh=10.2,
            projected_energy_kwh=1.3992,
            drift_kwh=8.8008,
            old_metrics={"energy_kwh": 10.2, "loss_kwh": 0.0},
            new_metrics={"energy_kwh": 1.3992, "loss_kwh": 0.0},
            old_quality_flags={"quality_flags": ["counter_implausible_vs_power"]},
            new_quality_flags={"quality_flags": ["recomputed_from_raw_telemetry"]},
            algorithm_version="interval-energy-v1",
            normalization_version="signed-power-v1",
            source_window_start=datetime(2026, 4, 20, 0, 0, tzinfo=timezone.utc),
            source_window_end=datetime(2026, 4, 21, 0, 0, tzinfo=timezone.utc),
            status="detected",
        )
        await session.commit()
        fetched = await repo.get_item(item.id)

    assert fetched is not None
    assert fetched.run_id == "run-001"
    assert fetched.tenant_id == "SH00000001"
    assert fetched.period_type == "device_day"
    assert fetched.old_metrics == {"energy_kwh": 10.2, "loss_kwh": 0.0}
    assert fetched.new_quality_flags == {"quality_flags": ["recomputed_from_raw_telemetry"]}
    assert fetched.algorithm_version == "interval-energy-v1"
    assert fetched.normalization_version == "signed-power-v1"
    assert fetched.status == "detected"


@pytest.mark.asyncio
async def test_reconciliation_audit_status_lifecycle_updates(session_factory):
    async with session_factory() as session:
        repo = ReconciliationAuditRepository(session)
        item = await repo.create_item(
            run_id="run-002",
            tenant_id="SH00000001",
            device_id="DEVICE-2",
            day=date(2026, 4, 21),
            expected_energy_kwh=5.0,
            projected_energy_kwh=4.5,
            drift_kwh=0.5,
            status="recomputed",
        )
        await session.commit()

        changed = await repo.update_status(
            item.id,
            status="approved",
            approved_by="ops-user",
            approved_at=datetime(2026, 4, 22, 10, 0, tzinfo=timezone.utc),
            repaired=True,
        )
        await session.commit()

        refreshed = await repo.get_item(item.id)
        run_items = await repo.list_run_items("run-002")

    assert changed is True
    assert refreshed is not None
    assert refreshed.status == "approved"
    assert refreshed.approved_by == "ops-user"
    assert refreshed.approved_at is not None
    assert refreshed.repaired is True
    assert len(run_items) == 1
    assert run_items[0].id == item.id
