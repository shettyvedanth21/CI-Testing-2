from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "services" / "energy-service"))
sys.path.insert(1, str(ROOT))

from app.models import (
    Base,
    EnergyDeviceDay,
    EnergyDeviceMonth,
    EnergyFleetDay,
    EnergyFleetMonth,
    EnergyReconcileAudit,
    EnergyReconcileRun,
)
from scripts.backfill_energy_cost import (
    TariffRateCache,
    _detect_drifted_rows,
    _guarded_update_row,
    _rebuild_aggregates,
    _write_audit_entry,
    _write_run_record,
    run_backfill,
)


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


@pytest_asyncio.fixture
async def session(session_factory):
    async with session_factory() as s:
        yield s


def _add_device_day(
    session: AsyncSession,
    *,
    tenant_id: str = "SH00000001",
    device_id: str = "DEV-1",
    day: date,
    energy_kwh: float = 10.0,
    energy_cost_inr: float = 0.0,
    loss_kwh: float = 0.0,
    loss_cost_inr: float = 0.0,
    idle_kwh: float = 0.0,
    offhours_kwh: float = 0.0,
    overconsumption_kwh: float = 0.0,
    quality_flags: str = "[]",
    version: int = 1,
) -> EnergyDeviceDay:
    row = EnergyDeviceDay(
        tenant_id=tenant_id,
        device_id=device_id,
        day=day,
        energy_kwh=energy_kwh,
        energy_cost_inr=energy_cost_inr,
        loss_kwh=loss_kwh,
        loss_cost_inr=loss_cost_inr,
        idle_kwh=idle_kwh,
        offhours_kwh=offhours_kwh,
        overconsumption_kwh=overconsumption_kwh,
        quality_flags=quality_flags,
        version=version,
    )
    session.add(row)
    return row


CUTOFF = date(2026, 5, 1)


@pytest.mark.asyncio
async def test_detect_primary_drift_energy_kwh_positive_cost_zero(session):
    _add_device_day(session, day=date(2026, 4, 10), energy_kwh=10.0, energy_cost_inr=0.0)
    await session.commit()
    rows = await _detect_drifted_rows(session, CUTOFF, None)
    assert len(rows) == 1
    assert rows[0].energy_kwh == 10.0


@pytest.mark.asyncio
async def test_detect_does_not_match_zero_kwh(session):
    _add_device_day(session, day=date(2026, 4, 10), energy_kwh=0.0, energy_cost_inr=0.0)
    await session.commit()
    rows = await _detect_drifted_rows(session, CUTOFF, None)
    assert len(rows) == 0


@pytest.mark.asyncio
async def test_detect_does_not_match_already_nonzero_cost(session):
    _add_device_day(session, day=date(2026, 4, 10), energy_kwh=10.0, energy_cost_inr=65.0)
    await session.commit()
    rows = await _detect_drifted_rows(session, CUTOFF, None)
    assert len(rows) == 0


@pytest.mark.asyncio
async def test_detect_excludes_recent_rows(session, monkeypatch):
    from scripts import backfill_energy_cost as bm
    monkeypatch.setattr(bm, "_local_today", lambda: date(2026, 4, 12))
    _add_device_day(session, day=date(2026, 4, 11), energy_kwh=10.0, energy_cost_inr=0.0)
    _add_device_day(session, day=date(2026, 4, 10), energy_kwh=10.0, energy_cost_inr=0.0)
    await session.commit()
    rows = await _detect_drifted_rows(session, date(2026, 4, 11), None)
    assert len(rows) == 1
    assert rows[0].day == date(2026, 4, 10)


@pytest.mark.asyncio
async def test_detect_secondary_drift_loss_cost_zero(session):
    _add_device_day(
        session,
        day=date(2026, 4, 10),
        energy_kwh=10.0,
        energy_cost_inr=65.0,
        loss_kwh=3.0,
        loss_cost_inr=0.0,
    )
    await session.commit()
    rows = await _detect_drifted_rows(session, CUTOFF, None)
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_tariff_resolution_mid_month_change(session):
    cache = TariffRateCache("http://unused")
    cache._tenant_versions["SH00000001"] = [
        {
            "id": "1",
            "rate": 5.0,
            "effective_from": "2026-04-01T00:00:00+00:00",
            "effective_end_at": "2026-04-15T00:00:00+00:00",
        },
        {
            "id": "2",
            "rate": 8.0,
            "effective_from": "2026-04-15T00:00:00+00:00",
            "effective_end_at": None,
        },
    ]
    before = cache.resolve_local("SH00000001", date(2026, 4, 10))
    assert before is not None
    assert before["rate"] == 5.0
    after = cache.resolve_local("SH00000001", date(2026, 4, 20))
    assert after is not None
    assert after["rate"] == 8.0


@pytest.mark.asyncio
async def test_guarded_write_only_repairs_zero_cost_fields(session):
    row = _add_device_day(
        session,
        day=date(2026, 4, 10),
        energy_kwh=10.0,
        energy_cost_inr=0.0,
        loss_kwh=3.0,
        loss_cost_inr=0.0,
    )
    await session.commit()
    result = await _guarded_update_row(session, row, 6.5, 6.5, dry_run=False)
    assert result["needs_repair"] is True
    assert result["new_energy_cost_inr"] == 65.0
    assert result["new_loss_cost_inr"] == 19.5
    await session.commit()
    refreshed = (await session.execute(select(EnergyDeviceDay).where(EnergyDeviceDay.id == row.id))).scalar_one()
    assert float(refreshed.energy_cost_inr) == pytest.approx(65.0, abs=0.01)
    assert float(refreshed.loss_cost_inr) == pytest.approx(19.5, abs=0.01)
    flags = json.loads(refreshed.quality_flags or "[]")
    assert "cost_backfill_applied" in flags


@pytest.mark.asyncio
async def test_guarded_write_does_not_overwrite_nonzero_cost(session):
    row = _add_device_day(
        session,
        day=date(2026, 4, 10),
        energy_kwh=10.0,
        energy_cost_inr=65.0,
        loss_kwh=3.0,
        loss_cost_inr=0.0,
    )
    await session.commit()
    result = await _guarded_update_row(session, row, 6.5, 6.5, dry_run=False)
    assert result["needs_repair"] is True
    assert result["new_energy_cost_inr"] == 65.0
    assert result["new_loss_cost_inr"] == pytest.approx(19.5, abs=0.01)


@pytest.mark.asyncio
async def test_idempotency_running_twice_does_not_re_repair(session):
    row = _add_device_day(
        session,
        day=date(2026, 4, 10),
        energy_kwh=10.0,
        energy_cost_inr=0.0,
    )
    await session.commit()
    await _guarded_update_row(session, row, 6.5, 6.5, dry_run=False)
    await session.commit()
    refreshed = (await session.execute(select(EnergyDeviceDay).where(EnergyDeviceDay.id == row.id))).scalar_one()
    result2 = await _guarded_update_row(session, refreshed, 6.5, 6.5, dry_run=False)
    assert result2["needs_repair"] is False
    assert result2["reason"] == "guard_no_match"


@pytest.mark.asyncio
async def test_rebuild_device_month_sum_matches_day_rows(session):
    d1 = _add_device_day(
        session, device_id="DEV-1", day=date(2026, 4, 10),
        energy_kwh=10.0, energy_cost_inr=0.0, loss_kwh=2.0, loss_cost_inr=0.0,
    )
    d2 = _add_device_day(
        session, device_id="DEV-1", day=date(2026, 4, 11),
        energy_kwh=5.0, energy_cost_inr=0.0, loss_kwh=1.0, loss_cost_inr=0.0,
    )
    dm = EnergyDeviceMonth(
        tenant_id="SH00000001", device_id="DEV-1", month=date(2026, 4, 1),
        energy_kwh=15.0, energy_cost_inr=0.0, loss_kwh=3.0, loss_cost_inr=0.0, version=1,
    )
    session.add(dm)
    await session.commit()
    await _guarded_update_row(session, d1, 6.5, 6.5, dry_run=False)
    await _guarded_update_row(session, d2, 6.5, 6.5, dry_run=False)
    await session.commit()
    await _rebuild_aggregates(
        session, "SH00000001",
        {("SH00000001", "DEV-1", date(2026, 4, 1))},
        set(), set(), dry_run=False,
    )
    await session.commit()
    refreshed_month = (
        await session.execute(
            select(EnergyDeviceMonth).where(
                EnergyDeviceMonth.tenant_id == "SH00000001",
                EnergyDeviceMonth.device_id == "DEV-1",
                EnergyDeviceMonth.month == date(2026, 4, 1),
            )
        )
    ).scalar_one()
    assert float(refreshed_month.energy_cost_inr) == pytest.approx(97.5, abs=0.01)
    assert float(refreshed_month.loss_cost_inr) == pytest.approx(19.5, abs=0.01)


@pytest.mark.asyncio
async def test_rebuild_fleet_day_sum_matches_day_rows(session):
    d1 = _add_device_day(
        session, device_id="DEV-1", day=date(2026, 4, 10),
        energy_kwh=10.0, energy_cost_inr=0.0, loss_kwh=2.0, loss_cost_inr=0.0,
    )
    d2 = _add_device_day(
        session, device_id="DEV-2", day=date(2026, 4, 10),
        energy_kwh=5.0, energy_cost_inr=0.0, loss_kwh=1.0, loss_cost_inr=0.0,
    )
    fd = EnergyFleetDay(
        tenant_id="SH00000001", day=date(2026, 4, 10),
        energy_kwh=15.0, energy_cost_inr=0.0, loss_kwh=3.0, loss_cost_inr=0.0, version=1,
    )
    session.add(fd)
    await session.commit()
    await _guarded_update_row(session, d1, 6.5, 6.5, dry_run=False)
    await _guarded_update_row(session, d2, 6.5, 6.5, dry_run=False)
    await session.commit()
    await _rebuild_aggregates(
        session, "SH00000001",
        set(),
        {("SH00000001", date(2026, 4, 10))},
        set(), dry_run=False,
    )
    await session.commit()
    refreshed_fd = (
        await session.execute(
            select(EnergyFleetDay).where(
                EnergyFleetDay.tenant_id == "SH00000001",
                EnergyFleetDay.day == date(2026, 4, 10),
            )
        )
    ).scalar_one()
    assert float(refreshed_fd.energy_cost_inr) == pytest.approx(97.5, abs=0.01)
    assert float(refreshed_fd.loss_cost_inr) == pytest.approx(19.5, abs=0.01)


@pytest.mark.asyncio
async def test_rebuild_fleet_month_sum_matches_day_rows(session):
    d1 = _add_device_day(
        session, device_id="DEV-1", day=date(2026, 4, 10),
        energy_kwh=10.0, energy_cost_inr=0.0, loss_kwh=2.0, loss_cost_inr=0.0,
    )
    d2 = _add_device_day(
        session, device_id="DEV-2", day=date(2026, 4, 15),
        energy_kwh=5.0, energy_cost_inr=0.0, loss_kwh=1.0, loss_cost_inr=0.0,
    )
    fm = EnergyFleetMonth(
        tenant_id="SH00000001", month=date(2026, 4, 1),
        energy_kwh=15.0, energy_cost_inr=0.0, loss_kwh=3.0, loss_cost_inr=0.0, version=1,
    )
    session.add(fm)
    await session.commit()
    await _guarded_update_row(session, d1, 6.5, 6.5, dry_run=False)
    await _guarded_update_row(session, d2, 6.5, 6.5, dry_run=False)
    await session.commit()
    await _rebuild_aggregates(
        session, "SH00000001",
        set(),
        set(),
        {("SH00000001", date(2026, 4, 1))},
        dry_run=False,
    )
    await session.commit()
    refreshed_fm = (
        await session.execute(
            select(EnergyFleetMonth).where(
                EnergyFleetMonth.tenant_id == "SH00000001",
                EnergyFleetMonth.month == date(2026, 4, 1),
            )
        )
    ).scalar_one()
    assert float(refreshed_fm.energy_cost_inr) == pytest.approx(97.5, abs=0.01)
    assert float(refreshed_fm.loss_cost_inr) == pytest.approx(19.5, abs=0.01)


@pytest.mark.asyncio
async def test_audit_row_created(session):
    row = _add_device_day(
        session, day=date(2026, 4, 10),
        energy_kwh=10.0, energy_cost_inr=0.0, loss_kwh=2.0, loss_cost_inr=0.0,
    )
    await session.commit()
    repair_info = await _guarded_update_row(session, row, 6.5, 6.5, dry_run=False)
    await session.commit()
    tariff_info = {"rate": 6.5, "source": "tenant_tariff_versions_local", "version_id": "3"}
    run_id = f"backfill-test-{uuid4().hex[:8]}"
    await _write_audit_entry(session, run_id, row, repair_info, tariff_info)
    await session.commit()
    audits = (await session.execute(
        select(EnergyReconcileAudit).where(EnergyReconcileAudit.run_id == run_id)
    )).scalars().all()
    assert len(audits) == 1
    a = audits[0]
    assert a.status == "applied"
    assert a.applied_by == "svc:energy-backfill"
    assert a.algorithm_version == "cost_backfill_v1"
    assert a.new_metrics["energy_cost_inr"] == pytest.approx(65.0, abs=0.01)
    assert a.new_metrics["loss_cost_inr"] == pytest.approx(13.0, abs=0.01)
    assert a.new_quality_flags["applied_rate"] == 6.5
    assert a.new_quality_flags["backfill_run_id"] == run_id


@pytest.mark.asyncio
async def test_dry_run_performs_no_writes(session):
    _add_device_day(
        session, day=date(2026, 4, 10),
        energy_kwh=10.0, energy_cost_inr=0.0, loss_kwh=2.0, loss_cost_inr=0.0,
    )
    await session.commit()
    from scripts import backfill_energy_cost as bm
    tariff_cache = TariffRateCache("http://unused")
    tariff_cache._tenant_versions["SH00000001"] = [
        {
            "id": "1",
            "rate": 6.5,
            "effective_from": "2026-01-01T00:00:00+00:00",
            "effective_end_at": None,
        },
    ]
    import httpx
    original_resolve = tariff_cache.resolve_http

    async def _fake_resolve(client, tid, day):
        return {"rate": 6.5, "source": "test_local", "version_id": 1}

    monkeypatch_resolve = _fake_resolve

    rows_before = (await session.execute(
        select(EnergyDeviceDay).where(EnergyDeviceDay.day == date(2026, 4, 10))
    )).scalars().all()
    assert all(float(r.energy_cost_inr) == 0.0 for r in rows_before)

    row = rows_before[0]
    result = await _guarded_update_row(session, row, 6.5, 6.5, dry_run=True)
    assert result["needs_repair"] is True
    assert result["dry_run"] is True
    assert result["new_energy_cost_inr"] == 65.0

    await session.commit()
    rows_after = (await session.execute(
        select(EnergyDeviceDay).where(EnergyDeviceDay.day == date(2026, 4, 10))
    )).scalars().all()
    assert all(float(r.energy_cost_inr) == 0.0 for r in rows_after)
