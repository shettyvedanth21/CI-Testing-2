from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
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
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

from app.database import Base
from app.models.device import Device, DeviceLiveState, TenantEmissionFactor
from app.services import dashboard as dashboard_module
from app.services.dashboard import DashboardService
from app.services.emission_factor_cache import EmissionFactorCache, build_co2_overview
from services.shared.tenant_context import TenantContext


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


def _tenant_ctx(tenant_id: str) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        user_id="tester",
        role="org_admin",
        plant_ids=[],
        is_super_admin=False,
    )


def _seeded_factor_payload(factor_value: float = 0.716) -> dict:
    return {
        "configured": True,
        "factor_value": factor_value,
        "factor_unit": "kg_co2_per_kwh",
        "method": "location_based",
        "country": "IN",
        "region": "all_india_grid",
        "source_name": "Central Electricity Authority CO2 Baseline Database",
        "source_version": "Version 19.0",
        "factor_year": "FY2022-23",
        "factor_source": "platform_default",
    }


def _unconfigured_factor_payload() -> dict:
    return {
        "configured": False,
        "factor_value": None,
        "factor_unit": "kg_co2_per_kwh",
        "method": None,
        "country": None,
        "region": None,
        "source_name": None,
        "source_version": None,
        "factor_year": None,
        "factor_source": "unconfigured",
    }


@pytest.mark.asyncio
async def test_build_co2_overview_computation_accuracy():
    payload = build_co2_overview(
        tenant_id="TENANT-A",
        today_energy_kwh=100.0,
        today_loss_kwh=10.0,
        today_loss_available=True,
        month_energy_kwh=2400.0,
        factor_payload=_seeded_factor_payload(0.716),
    )
    assert payload["available"] is True
    assert payload["today"]["co2_kg"] == pytest.approx(71.6, abs=1e-4)
    assert payload["today"]["avoidable_co2_kg"] == pytest.approx(7.16, abs=1e-4)
    assert payload["month"]["co2_kg"] == pytest.approx(1718.4, abs=1e-4)
    assert payload["today"]["avoidable_co2_available"] is True
    assert payload["today"]["avoidable_co2_reason"] is None


@pytest.mark.asyncio
async def test_build_co2_overview_zero_energy_is_valid():
    payload = build_co2_overview(
        tenant_id="TENANT-A",
        today_energy_kwh=0.0,
        today_loss_kwh=0.0,
        today_loss_available=True,
        month_energy_kwh=0.0,
        factor_payload=_seeded_factor_payload(0.716),
    )
    assert payload["available"] is True
    assert payload["today"]["co2_kg"] == 0.0
    assert payload["today"]["available"] is True
    assert payload["today"]["avoidable_co2_kg"] == 0.0
    assert payload["today"]["avoidable_co2_available"] is True


@pytest.mark.asyncio
async def test_build_co2_overview_missing_factor_returns_unavailable():
    payload = build_co2_overview(
        tenant_id="TENANT-A",
        today_energy_kwh=100.0,
        today_loss_kwh=10.0,
        today_loss_available=True,
        month_energy_kwh=2400.0,
        factor_payload=_unconfigured_factor_payload(),
    )
    assert payload["available"] is False
    assert payload["reason"] == "emission_factor_not_configured"
    assert payload["factor_source"] == "unconfigured"


@pytest.mark.asyncio
async def test_build_co2_overview_loss_not_current_day():
    payload = build_co2_overview(
        tenant_id="TENANT-A",
        today_energy_kwh=100.0,
        today_loss_kwh=10.0,
        today_loss_available=False,
        month_energy_kwh=2400.0,
        factor_payload=_seeded_factor_payload(0.716),
    )
    assert payload["available"] is True
    assert payload["today"]["co2_kg"] == pytest.approx(71.6, abs=1e-4)
    assert payload["today"]["avoidable_co2_available"] is False
    assert payload["today"]["avoidable_co2_reason"] == "loss_data_not_current_day"
    assert payload["today"]["avoidable_co2_kg"] is None


@pytest.mark.asyncio
async def test_build_co2_overview_week_always_unavailable():
    payload = build_co2_overview(
        tenant_id="TENANT-A",
        today_energy_kwh=100.0,
        today_loss_kwh=10.0,
        today_loss_available=True,
        month_energy_kwh=2400.0,
        factor_payload=_seeded_factor_payload(0.716),
    )
    assert payload["week"]["available"] is False
    assert payload["week"]["reason"] == "weekly_projection_not_supported"


@pytest.mark.asyncio
async def test_build_co2_overview_month_avoidable_always_unavailable():
    payload = build_co2_overview(
        tenant_id="TENANT-A",
        today_energy_kwh=100.0,
        today_loss_kwh=10.0,
        today_loss_available=True,
        month_energy_kwh=2400.0,
        factor_payload=_seeded_factor_payload(0.716),
    )
    assert payload["month"]["avoidable_co2_available"] is False
    assert payload["month"]["avoidable_co2_reason"] == "monthly_loss_projection_not_supported"


@pytest.mark.asyncio
async def test_build_co2_overview_factor_metadata_is_auditable():
    payload = build_co2_overview(
        tenant_id="TENANT-A",
        today_energy_kwh=100.0,
        today_loss_kwh=10.0,
        today_loss_available=True,
        month_energy_kwh=2400.0,
        factor_payload=_seeded_factor_payload(0.716),
    )
    assert payload["factor"]["value"] == 0.716
    assert payload["factor"]["unit"] == "kg_co2_per_kwh"
    assert payload["factor"]["method"] == "location_based"
    assert payload["factor"]["country"] == "IN"
    assert payload["factor"]["region"] == "all_india_grid"
    assert payload["factor"]["source"] == "Central Electricity Authority CO2 Baseline Database"
    assert payload["factor"]["source_version"] == "Version 19.0"
    assert payload["factor"]["factor_year"] == "FY2022-23"
    assert payload["factor_source"] == "platform_default"
    assert payload["calculation_version"] == "co2_scope2_v1"


@pytest.mark.asyncio
async def test_emission_factor_cache_reads_tenant_row(session_factory, monkeypatch: pytest.MonkeyPatch):
    EmissionFactorCache.invalidate()

    async with session_factory() as session:
        session.add(
            TenantEmissionFactor(
                id=1,
                tenant_id="TENANT-A",
                country="IN",
                region="all_india_grid",
                method="location_based",
                factor_value=0.850,
                factor_unit="kg_co2_per_kwh",
                source_name="Custom Tenant Source",
                source_version="v1",
                factor_year="FY2023-24",
                is_active=True,
            )
        )
        await session.commit()

    original_read = EmissionFactorCache._read_from_db

    async def _read_using_test_session(tenant_id):
        async with session_factory() as s:
            row = None
            if tenant_id is not None:
                result = await s.execute(
                    select(TenantEmissionFactor).where(
                        TenantEmissionFactor.tenant_id == tenant_id,
                        TenantEmissionFactor.is_active.is_(True),
                    )
                )
                row = result.scalar_one_or_none()
            if row is None:
                result = await s.execute(
                    select(TenantEmissionFactor).where(
                        TenantEmissionFactor.tenant_id == "__platform_default__",
                        TenantEmissionFactor.is_active.is_(True),
                    )
                )
                row = result.scalar_one_or_none()
            if row is None:
                return None
            source = "tenant_default"
            if row.tenant_id == "__platform_default__":
                source = "platform_default"
            return {
                "configured": True,
                "factor_value": float(row.factor_value),
                "factor_unit": row.factor_unit or "kg_co2_per_kwh",
                "method": row.method or "location_based",
                "country": row.country or "IN",
                "region": row.region or "all_india_grid",
                "source_name": row.source_name,
                "source_version": row.source_version,
                "factor_year": row.factor_year,
                "factor_source": source,
            }

    monkeypatch.setattr(EmissionFactorCache, "_read_from_db", _read_using_test_session)

    result = await EmissionFactorCache.get("TENANT-A")
    assert result["configured"] is True
    assert result["factor_value"] == pytest.approx(0.85, abs=1e-4)
    assert result["factor_source"] == "tenant_default"
    EmissionFactorCache.invalidate()


@pytest.mark.asyncio
async def test_emission_factor_cache_falls_back_to_platform_default(session_factory, monkeypatch: pytest.MonkeyPatch):
    EmissionFactorCache.invalidate()

    async with session_factory() as session:
        session.add(
            TenantEmissionFactor(
                id=1,
                tenant_id="__platform_default__",
                country="IN",
                region="all_india_grid",
                method="location_based",
                factor_value=0.716,
                factor_unit="kg_co2_per_kwh",
                source_name="CEA India Grid",
                source_version="v19",
                factor_year="FY2022-23",
                is_active=True,
            )
        )
        await session.commit()

    async def _read_using_test_session(tenant_id):
        async with session_factory() as s:
            row = None
            if tenant_id is not None:
                result = await s.execute(
                    select(TenantEmissionFactor).where(
                        TenantEmissionFactor.tenant_id == tenant_id,
                        TenantEmissionFactor.is_active.is_(True),
                    )
                )
                row = result.scalar_one_or_none()
            if row is None:
                result = await s.execute(
                    select(TenantEmissionFactor).where(
                        TenantEmissionFactor.tenant_id == "__platform_default__",
                        TenantEmissionFactor.is_active.is_(True),
                    )
                )
                row = result.scalar_one_or_none()
            if row is None:
                return None
            source = "tenant_default"
            if row.tenant_id == "__platform_default__":
                source = "platform_default"
            return {
                "configured": True,
                "factor_value": float(row.factor_value),
                "factor_unit": row.factor_unit or "kg_co2_per_kwh",
                "method": row.method or "location_based",
                "country": row.country or "IN",
                "region": row.region or "all_india_grid",
                "source_name": row.source_name,
                "source_version": row.source_version,
                "factor_year": row.factor_year,
                "factor_source": source,
            }

    monkeypatch.setattr(EmissionFactorCache, "_read_from_db", _read_using_test_session)

    result = await EmissionFactorCache.get("TENANT-NO-ROW")
    assert result["configured"] is True
    assert result["factor_value"] == pytest.approx(0.716, abs=1e-4)
    assert result["factor_source"] == "platform_default"
    EmissionFactorCache.invalidate()


@pytest.mark.asyncio
async def test_emission_factor_cache_unconfigured_when_no_rows(session_factory, monkeypatch: pytest.MonkeyPatch):
    EmissionFactorCache.invalidate()

    async def _read_using_test_session(tenant_id):
        async with session_factory() as s:
            row = None
            if tenant_id is not None:
                result = await s.execute(
                    select(TenantEmissionFactor).where(
                        TenantEmissionFactor.tenant_id == tenant_id,
                        TenantEmissionFactor.is_active.is_(True),
                    )
                )
                row = result.scalar_one_or_none()
            if row is None:
                result = await s.execute(
                    select(TenantEmissionFactor).where(
                        TenantEmissionFactor.tenant_id == "__platform_default__",
                        TenantEmissionFactor.is_active.is_(True),
                    )
                )
                row = result.scalar_one_or_none()
            if row is None:
                return None
            source = "tenant_default"
            if row.tenant_id == "__platform_default__":
                source = "platform_default"
            return {
                "configured": True,
                "factor_value": float(row.factor_value),
                "factor_unit": row.factor_unit or "kg_co2_per_kwh",
                "method": row.method or "location_based",
                "country": row.country or "IN",
                "region": row.region or "all_india_grid",
                "source_name": row.source_name,
                "source_version": row.source_version,
                "factor_year": row.factor_year,
                "factor_source": source,
            }

    monkeypatch.setattr(EmissionFactorCache, "_read_from_db", _read_using_test_session)

    result = await EmissionFactorCache.get("TENANT-NO-ROW")
    assert result["configured"] is False
    assert result["factor_source"] == "unconfigured"
    EmissionFactorCache.invalidate()


@pytest.mark.asyncio
async def test_loss_stats_includes_co2_overview(session_factory, monkeypatch: pytest.MonkeyPatch):
    local_day = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata")).date()

    async with session_factory() as session:
        session.add_all(
            [
                Device(
                    device_id="CO2-DEVICE-A",
                    tenant_id="TENANT-A",
                    plant_id="PLANT-1",
                    device_name="CO2 Test A",
                    device_type="compressor",
                ),
                DeviceLiveState(
                    device_id="CO2-DEVICE-A",
                    tenant_id="TENANT-A",
                    runtime_status="running",
                    load_state="running",
                    day_bucket=local_day,
                    today_energy_kwh=120.0,
                    today_loss_kwh=5.0,
                    month_energy_kwh=2400.0,
                    version=1,
                ),
            ]
        )
        await session.commit()

        monkeypatch.setattr(DashboardService, "_get_tariff", AsyncMock(return_value=(5.0, "INR")))

        async def fake_factor_get(tenant_id=None):
            return _seeded_factor_payload(0.716)

        monkeypatch.setattr(EmissionFactorCache, "get", fake_factor_get)

        payload = await DashboardService(session, _tenant_ctx("TENANT-A")).get_device_loss_stats(
            "CO2-DEVICE-A",
            "TENANT-A",
        )

    assert "co2_overview" in payload
    assert payload["co2_overview"]["available"] is True
    assert payload["co2_overview"]["today"]["co2_kg"] == pytest.approx(85.92, abs=1e-2)
    assert payload["co2_overview"]["today"]["avoidable_co2_kg"] == pytest.approx(3.58, abs=1e-2)
    assert payload["co2_overview"]["month"]["co2_kg"] == pytest.approx(1718.4, abs=1e-2)
    assert payload["co2_overview"]["week"]["available"] is False
    assert payload["co2_overview"]["month"]["avoidable_co2_available"] is False


@pytest.mark.asyncio
async def test_loss_stats_co2_unavailable_when_factor_missing(session_factory, monkeypatch: pytest.MonkeyPatch):
    local_day = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata")).date()

    async with session_factory() as session:
        session.add_all(
            [
                Device(
                    device_id="CO2-DEVICE-B",
                    tenant_id="TENANT-B",
                    plant_id="PLANT-1",
                    device_name="CO2 Test B",
                    device_type="compressor",
                ),
                DeviceLiveState(
                    device_id="CO2-DEVICE-B",
                    tenant_id="TENANT-B",
                    runtime_status="running",
                    load_state="running",
                    day_bucket=local_day,
                    today_energy_kwh=50.0,
                    today_loss_kwh=2.0,
                    month_energy_kwh=1000.0,
                    version=1,
                ),
            ]
        )
        await session.commit()

        monkeypatch.setattr(DashboardService, "_get_tariff", AsyncMock(return_value=(5.0, "INR")))

        async def fake_factor_get(tenant_id=None):
            return _unconfigured_factor_payload()

        monkeypatch.setattr(EmissionFactorCache, "get", fake_factor_get)

        payload = await DashboardService(session, _tenant_ctx("TENANT-B")).get_device_loss_stats(
            "CO2-DEVICE-B",
            "TENANT-B",
        )

    assert "co2_overview" in payload
    assert payload["co2_overview"]["available"] is False
    assert payload["co2_overview"]["reason"] == "emission_factor_not_configured"


@pytest.mark.asyncio
async def test_loss_stats_co2_avoidable_unavailable_when_stale_day(session_factory, monkeypatch: pytest.MonkeyPatch):
    from datetime import timedelta

    stale_day = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata")).date() - timedelta(days=1)

    async with session_factory() as session:
        session.add_all(
            [
                Device(
                    device_id="CO2-DEVICE-C",
                    tenant_id="TENANT-A",
                    plant_id="PLANT-1",
                    device_name="CO2 Test C",
                    device_type="compressor",
                ),
                DeviceLiveState(
                    device_id="CO2-DEVICE-C",
                    tenant_id="TENANT-A",
                    runtime_status="running",
                    load_state="running",
                    day_bucket=stale_day,
                    today_energy_kwh=80.0,
                    today_loss_kwh=4.0,
                    month_energy_kwh=1600.0,
                    version=1,
                ),
            ]
        )
        await session.commit()

        monkeypatch.setattr(DashboardService, "_get_tariff", AsyncMock(return_value=(5.0, "INR")))

        async def fake_factor_get(tenant_id=None):
            return _seeded_factor_payload(0.716)

        monkeypatch.setattr(EmissionFactorCache, "get", fake_factor_get)

        payload = await DashboardService(session, _tenant_ctx("TENANT-A")).get_device_loss_stats(
            "CO2-DEVICE-C",
            "TENANT-A",
        )

    assert "co2_overview" in payload
    assert payload["co2_overview"]["available"] is True
    assert payload["co2_overview"]["today"]["avoidable_co2_available"] is False
    assert payload["co2_overview"]["today"]["avoidable_co2_reason"] == "loss_data_not_current_day"


@pytest.mark.asyncio
async def test_payload_backward_compatibility(session_factory, monkeypatch: pytest.MonkeyPatch):
    local_day = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata")).date()

    async with session_factory() as session:
        session.add_all(
            [
                Device(
                    device_id="CO2-COMPAT",
                    tenant_id="TENANT-A",
                    plant_id="PLANT-1",
                    device_name="CO2 Compat",
                    device_type="compressor",
                ),
                DeviceLiveState(
                    device_id="CO2-COMPAT",
                    tenant_id="TENANT-A",
                    runtime_status="running",
                    load_state="running",
                    day_bucket=local_day,
                    today_energy_kwh=50.0,
                    today_loss_kwh=2.0,
                    today_idle_kwh=1.0,
                    today_offhours_kwh=0.5,
                    today_overconsumption_kwh=0.5,
                    month_energy_kwh=1000.0,
                    version=1,
                ),
            ]
        )
        await session.commit()

        monkeypatch.setattr(DashboardService, "_get_tariff", AsyncMock(return_value=(5.0, "INR")))

        async def fake_factor_get(tenant_id=None):
            return _seeded_factor_payload(0.716)

        monkeypatch.setattr(EmissionFactorCache, "get", fake_factor_get)

        payload = await DashboardService(session, _tenant_ctx("TENANT-A")).get_device_loss_stats(
            "CO2-COMPAT",
            "TENANT-A",
        )

    assert "today" in payload
    assert payload["today"]["idle_kwh"] == pytest.approx(1.0, abs=1e-4)
    assert payload["today"]["off_hours_kwh"] == pytest.approx(0.5, abs=1e-4)
    assert payload["today"]["overconsumption_kwh"] == pytest.approx(0.5, abs=1e-4)
    assert payload["today"]["total_loss_kwh"] == pytest.approx(2.0, abs=1e-4)
    assert payload["today"]["today_energy_kwh"] == pytest.approx(50.0, abs=1e-4)
    assert payload["tariff_configured"] is True
    assert "co2_overview" in payload


@pytest.mark.asyncio
async def test_co2_path_makes_no_outbound_http_calls(session_factory, monkeypatch: pytest.MonkeyPatch):
    from app.services import live_dashboard as live_dashboard_module
    from app.services.live_dashboard import LiveDashboardService
    from app.services import shared_http as shared_http_module

    local_day = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata")).date()

    network_call_flag = {"called": False}

    def _mark_network_call(*args, **kwargs):
        network_call_flag["called"] = True
        raise RuntimeError("Outbound HTTP call attempted during CO2 computation path")

    monkeypatch.setattr(shared_http_module, "get_client", _mark_network_call)
    monkeypatch.setattr(shared_http_module, "request_with_retries", _mark_network_call)

    async with session_factory() as session:
        session.add_all(
            [
                Device(
                    device_id="CO2-NO-NET",
                    tenant_id="TENANT-A",
                    plant_id="PLANT-1",
                    device_name="CO2 No Net",
                    device_type="compressor",
                ),
                DeviceLiveState(
                    device_id="CO2-NO-NET",
                    tenant_id="TENANT-A",
                    runtime_status="running",
                    load_state="running",
                    day_bucket=local_day,
                    today_energy_kwh=100.0,
                    today_loss_kwh=10.0,
                    month_energy_kwh=2400.0,
                    version=1,
                ),
            ]
        )
        await session.commit()

        async def fake_factor_get(tenant_id=None):
            return _seeded_factor_payload(0.716)

        monkeypatch.setattr(EmissionFactorCache, "get", fake_factor_get)
        monkeypatch.setattr(DashboardService, "_get_tariff", AsyncMock(return_value=(5.0, "INR")))

        payload_full = await DashboardService(session, _tenant_ctx("TENANT-A")).get_device_loss_stats(
            "CO2-NO-NET",
            "TENANT-A",
        )

        payload_summary = await LiveDashboardService(session, _tenant_ctx("TENANT-A")).get_dashboard_bootstrap_summary(
            device_id="CO2-NO-NET",
            tenant_id="TENANT-A",
        )

    assert network_call_flag["called"] is False, "CO2 path made an outbound HTTP call — violates no-cross-service constraint"
    assert "co2_overview" in payload_full
    assert payload_summary.get("loss_overview", {}).get("co2_overview") is not None
