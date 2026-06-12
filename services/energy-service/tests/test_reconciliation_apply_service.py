from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "services" / "energy-service"))
sys.path.insert(1, str(ROOT))

from app.models import (  # noqa: E402
    Base,
    EnergyDeviceDay,
    EnergyDeviceMonth,
    EnergyFleetDay,
    EnergyFleetMonth,
    EnergyReconcileAudit,
)
from app.services.reconciliation_apply import ReconciliationApplyService  # noqa: E402


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
async def test_apply_requires_approval_and_rejection_records_reason(session_factory):
    async with session_factory() as session:
        session.add(
            EnergyReconcileAudit(
                run_id="run-001",
                tenant_id="SH00000001",
                device_id="DEVICE-1",
                day=date(2026, 4, 20),
                expected_energy_kwh=1.3992,
                projected_energy_kwh=10.2,
                drift_kwh=-8.8008,
                status="recomputed",
                new_metrics={"energy_kwh": 1.3992, "idle_kwh": 0.0, "offhours_kwh": 0.0, "overconsumption_kwh": 0.0, "loss_kwh": 0.0},
            )
        )
        await session.commit()

        service = ReconciliationApplyService(session)
        with pytest.raises(ValueError):
            await service.apply_candidate(1, actor="ops-user")

        rejected = await service.reject_candidate(1, actor="ops-user", reason="validated_false_positive")
        refreshed = await session.get(EnergyReconcileAudit, 1)

    assert rejected["status"] == "rejected"
    assert refreshed is not None
    assert refreshed.status == "rejected"
    assert refreshed.rejected_by == "ops-user"
    assert refreshed.rejection_reason == "validated_false_positive"


@pytest.mark.asyncio
async def test_apply_updates_day_and_rebuilds_month_and_fleet_from_corrected_truth(session_factory, monkeypatch):
    async with session_factory() as session:
        session.add_all(
            [
                EnergyDeviceDay(
                    tenant_id="SH00000001",
                    device_id="DEVICE-1",
                    day=date(2026, 4, 20),
                    energy_kwh=10.2,
                    energy_cost_inr=66.3,
                    idle_kwh=0.2,
                    offhours_kwh=0.1,
                    overconsumption_kwh=0.0,
                    loss_kwh=0.3,
                    loss_cost_inr=1.95,
                    quality_flags="[]",
                    version=1,
                ),
                EnergyDeviceDay(
                    tenant_id="SH00000001",
                    device_id="DEVICE-1",
                    day=date(2026, 4, 21),
                    energy_kwh=2.0,
                    energy_cost_inr=13.0,
                    idle_kwh=0.0,
                    offhours_kwh=0.0,
                    overconsumption_kwh=0.0,
                    loss_kwh=0.0,
                    loss_cost_inr=0.0,
                    quality_flags="[]",
                    version=1,
                ),
                EnergyDeviceDay(
                    tenant_id="SH00000001",
                    device_id="DEVICE-2",
                    day=date(2026, 4, 20),
                    energy_kwh=3.0,
                    energy_cost_inr=19.5,
                    idle_kwh=0.0,
                    offhours_kwh=0.0,
                    overconsumption_kwh=0.0,
                    loss_kwh=0.1,
                    loss_cost_inr=0.65,
                    quality_flags="[]",
                    version=1,
                ),
                EnergyReconcileAudit(
                    run_id="run-002",
                    tenant_id="SH00000001",
                    device_id="DEVICE-1",
                    day=date(2026, 4, 20),
                    expected_energy_kwh=1.3992,
                    projected_energy_kwh=10.2,
                    drift_kwh=-8.8008,
                    status="approved",
                    approved_by="reviewer",
                    approved_at=datetime(2026, 4, 22, 9, 0, tzinfo=timezone.utc),
                    new_metrics={
                        "energy_kwh": 1.3992,
                        "idle_kwh": 0.0,
                        "offhours_kwh": 0.0,
                        "overconsumption_kwh": 0.0,
                        "loss_kwh": 0.0,
                    },
                    new_quality_flags={"related_reports": [{"report_id": "rep-1", "report_type": "consumption"}]},
                ),
            ]
        )
        await session.commit()

        service = ReconciliationApplyService(session)
        async def _fake_resolve_historical_tariff(audit):
            return {
                "rate": 6.5,
                "currency": "INR",
                "source": "tenant_tariff_versions",
                "version_id": 3,
                "effective_start_at": "2026-04-01T00:00:00",
                "effective_end_at": None,
            }

        async def _fake_request_report_revisions(audit):
            return [{"source_report_id": "rep-1", "new_report_id": "rep-2", "status": "queued", "tariff_version_id": 3}]

        monkeypatch.setattr(service, "_resolve_historical_tariff", _fake_resolve_historical_tariff)
        monkeypatch.setattr(service, "_request_report_revisions", _fake_request_report_revisions)

        approved = await service.approve_candidate(1, actor="reviewer-2") if False else None  # no-op guard
        result = await service.apply_candidate(1, actor="ops-user")

        day_row = (
            await session.execute(
                select(EnergyDeviceDay).where(
                    EnergyDeviceDay.tenant_id == "SH00000001",
                    EnergyDeviceDay.device_id == "DEVICE-1",
                    EnergyDeviceDay.day == date(2026, 4, 20),
                )
            )
        ).scalar_one()
        month_row = (
            await session.execute(
                select(EnergyDeviceMonth).where(
                    EnergyDeviceMonth.tenant_id == "SH00000001",
                    EnergyDeviceMonth.device_id == "DEVICE-1",
                    EnergyDeviceMonth.month == date(2026, 4, 1),
                )
            )
        ).scalar_one()
        fleet_day = (
            await session.execute(
                select(EnergyFleetDay).where(
                    EnergyFleetDay.tenant_id == "SH00000001",
                    EnergyFleetDay.day == date(2026, 4, 20),
                )
            )
        ).scalar_one()
        fleet_month = (
            await session.execute(
                select(EnergyFleetMonth).where(
                    EnergyFleetMonth.tenant_id == "SH00000001",
                    EnergyFleetMonth.month == date(2026, 4, 1),
                )
            )
        ).scalar_one()
        audit = await session.get(EnergyReconcileAudit, 1)

    assert result["status"] == "applied"
    assert day_row.energy_kwh == pytest.approx(1.3992, abs=1e-6)
    assert day_row.energy_cost_inr == pytest.approx(9.0948, abs=1e-3)
    assert "reconciled_applied" in json.loads(day_row.quality_flags or "[]")
    assert month_row.energy_kwh == pytest.approx(3.3992, abs=1e-6)
    assert month_row.energy_cost_inr == pytest.approx(22.0948, abs=1e-3)
    assert fleet_day.energy_kwh == pytest.approx(4.3992, abs=1e-6)
    assert fleet_day.energy_cost_inr == pytest.approx(28.5948, abs=1e-3)
    assert fleet_month.energy_kwh == pytest.approx(6.3992, abs=1e-6)
    assert fleet_month.energy_cost_inr == pytest.approx(41.5948, abs=1e-3)
    assert audit is not None
    assert audit.status == "applied"
    assert audit.applied_by == "ops-user"
    assert audit.new_quality_flags["applied_tariff"]["version_id"] == 3
    assert audit.new_quality_flags["created_report_revisions"] == [
        {"source_report_id": "rep-1", "new_report_id": "rep-2", "status": "queued", "tariff_version_id": 3}
    ]


@pytest.mark.asyncio
async def test_sync_device_days_from_telemetry_rebuilds_canonical_day_and_rollups(session_factory, monkeypatch):
    async with session_factory() as session:
        session.add(
            EnergyDeviceDay(
                tenant_id="SH00000001",
                device_id="DEVICE-1",
                day=date(2026, 6, 11),
                energy_kwh=100.0,
                energy_cost_inr=800.0,
                idle_kwh=0.0,
                offhours_kwh=50.0,
                overconsumption_kwh=20.0,
                loss_kwh=70.0,
                loss_cost_inr=560.0,
                quality_flags="[]",
                version=1,
            )
        )
        await session.commit()

        monkeypatch.setattr(
            "app.services.reconciliation_apply.ReconciliationPreviewService._fetch_telemetry_rows",
            AsyncMock(return_value=[{"timestamp": "2026-06-11T00:00:00Z", "power": 1.0}]),
        )
        monkeypatch.setattr(
            "app.services.reconciliation_apply.ReconciliationPreviewService._recompute_metrics",
            AsyncMock(
                return_value={
                    "metrics": {
                        "energy_kwh": 132.5,
                        "idle_kwh": 0.0,
                        "offhours_kwh": 66.2,
                        "overconsumption_kwh": 66.3,
                        "loss_kwh": 132.5,
                        "pf_estimated": False,
                        "samples": 10,
                    }
                }
            ),
        )
        monkeypatch.setattr(
            ReconciliationApplyService,
            "_resolve_tariff_for_day",
            AsyncMock(return_value={"rate": 8.0, "source": "test"}),
        )

        service = ReconciliationApplyService(session)
        result = await service.sync_device_days_from_telemetry(
            tenant_id="SH00000001",
            device_ids=["DEVICE-1"],
            day=date(2026, 6, 11),
            actor="test-sync",
        )

        day_row = (
            await session.execute(
                select(EnergyDeviceDay).where(
                    EnergyDeviceDay.tenant_id == "SH00000001",
                    EnergyDeviceDay.device_id == "DEVICE-1",
                    EnergyDeviceDay.day == date(2026, 6, 11),
                )
            )
        ).scalar_one()
        fleet_day = (
            await session.execute(
                select(EnergyFleetDay).where(
                    EnergyFleetDay.tenant_id == "SH00000001",
                    EnergyFleetDay.day == date(2026, 6, 11),
                )
            )
        ).scalar_one()

    assert result["updated"] == 1
    assert day_row.energy_kwh == 132.5
    assert day_row.loss_kwh == 132.5
    assert day_row.energy_cost_inr == 1060.0
    assert day_row.loss_cost_inr == 1060.0
    assert "current_day_sync_applied" in json.loads(day_row.quality_flags or "[]")
    assert fleet_day.energy_kwh == 132.5
    assert fleet_day.loss_kwh == 132.5
