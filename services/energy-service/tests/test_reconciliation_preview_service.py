from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "services" / "energy-service"))
sys.path.insert(1, str(ROOT))

from app.models import Base, EnergyDeviceDay, EnergyReconcileAudit, EnergyReconcileRun  # noqa: E402
from app.services import reconciliation_preview as preview_module  # noqa: E402
from app.services.reconciliation_preview import ReconciliationPreviewRequest, ReconciliationPreviewService  # noqa: E402


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
async def test_preview_creates_run_and_audit_candidates_without_mutating_canonical(session_factory, monkeypatch):
    async def _fake_meta_get(device_id: str, tenant_id: str | None = None):
        return {
            "shifts": [],
            "idle_threshold": None,
            "over_threshold": None,
            "energy_flow_mode": "consumption_only",
            "polarity_mode": "normal",
        }

    monkeypatch.setattr(preview_module.meta_cache, "get", _fake_meta_get)

    async with session_factory() as session:
        session.add(
            EnergyDeviceDay(
                tenant_id="SH00000001",
                device_id="DEVICE-1",
                day=date(2026, 4, 20),
                energy_kwh=10.2,
                idle_kwh=0.0,
                offhours_kwh=0.0,
                overconsumption_kwh=0.0,
                loss_kwh=0.0,
                quality_flags='["counter_implausible_vs_power"]',
                version=7,
            )
        )
        await session.commit()

        service = ReconciliationPreviewService(session)

        async def _fake_fetch_rows(*, tenant_id: str | None, device_id: str, day: date):
            return [
                {"timestamp": "2026-04-20T00:00:00+00:00", "power": 1000.0, "energy_kwh": 0.0},
                {"timestamp": "2026-04-20T00:00:20+00:00", "power": 1000.0, "energy_kwh": 8.9},
                {"timestamp": "2026-04-20T01:00:00+00:00", "power": 1000.0, "energy_kwh": 9.5667},
            ]

        async def _fake_report_cache(tenant_id: str | None):
            return [
                {
                    "report_id": "rep-1",
                    "report_type": "consumption",
                    "start_date": "2026-04-20",
                    "end_date": "2026-04-20",
                    "device_scope": "DEVICE-1",
                    "devices": [],
                }
            ]

        monkeypatch.setattr(service, "_fetch_telemetry_rows", _fake_fetch_rows)
        monkeypatch.setattr(service, "_load_completed_report_summaries", _fake_report_cache)

        result = await service.preview(
            ReconciliationPreviewRequest(
                start_date=date(2026, 4, 20),
                end_date=date(2026, 4, 20),
                tenant_id="SH00000001",
                device_ids=["DEVICE-1"],
                requested_by="ops-user",
                min_drift_kwh=0.25,
                min_drift_ratio=0.25,
                include_report_intersections=True,
            )
        )

        refreshed = (
            await session.execute(
                select(EnergyDeviceDay).where(
                    EnergyDeviceDay.tenant_id == "SH00000001",
                    EnergyDeviceDay.device_id == "DEVICE-1",
                    EnergyDeviceDay.day == date(2026, 4, 20),
                )
            )
        ).scalar_one()
        runs = (await session.execute(select(EnergyReconcileRun))).scalars().all()
        audits = (await session.execute(select(EnergyReconcileAudit))).scalars().all()

    assert refreshed.energy_kwh == 10.2
    assert len(runs) == 1
    assert runs[0].status == "completed"
    assert runs[0].candidate_count == 1
    assert result["candidate_count"] == 1
    assert len(audits) == 1
    assert audits[0].status == "recomputed"
    assert audits[0].run_id == runs[0].run_id
    assert audits[0].old_metrics["energy_kwh"] == 10.2
    assert audits[0].new_metrics["energy_kwh"] < 1.1
    assert "abnormal_counter_jumps_detected" in audits[0].new_quality_flags["candidate_reasons"]
    assert audits[0].new_quality_flags["related_reports"] == [
        {"report_id": "rep-1", "report_type": "consumption"}
    ]


@pytest.mark.asyncio
async def test_preview_skips_non_material_days_and_leaves_audit_empty(session_factory, monkeypatch):
    async def _fake_meta_get(device_id: str, tenant_id: str | None = None):
        return {
            "shifts": [],
            "idle_threshold": None,
            "over_threshold": None,
            "energy_flow_mode": "consumption_only",
            "polarity_mode": "normal",
        }

    monkeypatch.setattr(preview_module.meta_cache, "get", _fake_meta_get)

    async with session_factory() as session:
        session.add(
            EnergyDeviceDay(
                tenant_id="SH00000001",
                device_id="DEVICE-2",
                day=date(2026, 4, 21),
                energy_kwh=0.166667,
                idle_kwh=0.0,
                offhours_kwh=0.0,
                overconsumption_kwh=0.0,
                loss_kwh=0.0,
                quality_flags="[]",
                version=1,
            )
        )
        await session.commit()

        service = ReconciliationPreviewService(session)

        async def _fake_fetch_rows(*, tenant_id: str | None, device_id: str, day: date):
            return [
                {"timestamp": "2026-04-21T00:00:00+00:00", "power": 1000.0},
                {"timestamp": "2026-04-21T00:10:00+00:00", "power": 1000.0},
            ]

        async def _fake_report_cache(tenant_id: str | None):
            return []

        monkeypatch.setattr(service, "_fetch_telemetry_rows", _fake_fetch_rows)
        monkeypatch.setattr(service, "_load_completed_report_summaries", _fake_report_cache)

        result = await service.preview(
            ReconciliationPreviewRequest(
                start_date=date(2026, 4, 21),
                end_date=date(2026, 4, 21),
                tenant_id="SH00000001",
                device_ids=["DEVICE-2"],
                min_drift_kwh=0.25,
                min_drift_ratio=0.25,
            )
        )

        runs = (await session.execute(select(EnergyReconcileRun))).scalars().all()
        audits = (await session.execute(select(EnergyReconcileAudit))).scalars().all()

    assert result["candidate_count"] == 0
    assert len(runs) == 1
    assert runs[0].candidate_count == 0
    assert len(audits) == 0
