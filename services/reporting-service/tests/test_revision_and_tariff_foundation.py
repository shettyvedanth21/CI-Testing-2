from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(1, str(ROOT / "services" / "reporting-service"))
sys.path.insert(2, str(ROOT / "services"))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from src.models.energy_reports import Base as EnergyReportBase  # noqa: E402
from src.models.tenant_tariffs import Base as TenantTariffBase  # noqa: E402
from src.repositories.report_repository import ReportRepository  # noqa: E402
from src.repositories.tariff_repository import TariffRepository  # noqa: E402
from src.services.local_bootstrap import ensure_local_tariff_bootstrap, validate_local_bootstrap_contract  # noqa: E402
from src.services.tariff_resolver import resolve_tariff  # noqa: E402
from src.services.tenant_scope import build_service_tenant_context  # noqa: E402
from src.config import settings  # noqa: E402


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(EnergyReportBase.metadata.create_all)
        await conn.run_sync(TenantTariffBase.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_tariff_upsert_creates_current_row_and_version_history(session_factory):
    async with session_factory() as session:
        repo = TariffRepository(session, build_service_tenant_context("SH00000001"))
        tariff = await repo.upsert_tariff(
            tenant_id="SH00000001",
            data={
                "tenant_id": "SH00000001",
                "energy_rate_per_kwh": 6.5,
                "currency": "INR",
                "change_reason": "initial",
                "created_by": "ops",
            },
        )
        versions = await repo.list_versions("SH00000001")

    assert float(tariff.energy_rate_per_kwh) == 6.5
    assert len(versions) == 1
    assert versions[0].version_number == 1
    assert float(versions[0].energy_rate_per_kwh) == 6.5
    assert versions[0].change_reason == "initial"
    assert versions[0].created_by == "ops"


@pytest.mark.asyncio
async def test_tariff_versions_resolve_deterministically_and_do_not_overlap(session_factory):
    start_a = datetime(2026, 4, 1, 0, 0, 0)
    start_b = datetime(2026, 5, 1, 0, 0, 0)

    async with session_factory() as session:
        repo = TariffRepository(session, build_service_tenant_context("SH00000001"))
        await repo.upsert_tariff(
            tenant_id="SH00000001",
            data={
                "tenant_id": "SH00000001",
                "energy_rate_per_kwh": 6.5,
                "currency": "INR",
                "effective_start_at": start_a,
            },
        )
        await repo.upsert_tariff(
            tenant_id="SH00000001",
            data={
                "tenant_id": "SH00000001",
                "energy_rate_per_kwh": 7.25,
                "currency": "INR",
                "effective_start_at": start_b,
            },
        )
        versions = await repo.list_versions("SH00000001")
        resolved_a = await repo.get_effective_version("SH00000001", effective_at=start_a + timedelta(days=10))
        resolved_b = await repo.get_effective_version("SH00000001", effective_at=start_b + timedelta(days=10))

    assert len(versions) == 2
    assert versions[0].effective_end_at == start_b
    assert versions[0].superseded_by_version_id == versions[1].id
    assert resolved_a is not None and float(resolved_a.energy_rate_per_kwh) == 6.5
    assert resolved_b is not None and float(resolved_b.energy_rate_per_kwh) == 7.25


@pytest.mark.asyncio
async def test_tariff_versions_reject_backdated_overlap(session_factory):
    async with session_factory() as session:
        repo = TariffRepository(session, build_service_tenant_context("SH00000001"))
        await repo.upsert_tariff(
            tenant_id="SH00000001",
            data={
                "tenant_id": "SH00000001",
                "energy_rate_per_kwh": 6.5,
                "currency": "INR",
                "effective_start_at": datetime(2026, 4, 1, 0, 0, 0),
            },
        )
        with pytest.raises(ValueError):
            await repo.create_tariff_version(
                tenant_id="SH00000001",
                data={"energy_rate_per_kwh": 7.0, "currency": "INR"},
                effective_start_at=datetime(2026, 3, 1, 0, 0, 0),
            )


@pytest.mark.asyncio
async def test_resolve_tariff_uses_effective_version_when_available(session_factory):
    async with session_factory() as session:
        repo = TariffRepository(session, build_service_tenant_context("SH00000001"))
        await repo.upsert_tariff(
            tenant_id="SH00000001",
            data={
                "tenant_id": "SH00000001",
                "energy_rate_per_kwh": 8.0,
                "currency": "INR",
                "effective_start_at": datetime(2026, 4, 1, 0, 0, 0),
                "change_reason": "revision",
                "created_by": "finance",
            },
        )
        resolved = await resolve_tariff(session, "SH00000001", effective_at=datetime(2026, 4, 15, 12, 0, 0))

    assert resolved.rate == 8.0
    assert resolved.source == "tenant_tariff_versions"
    assert resolved.version_id == 1
    assert resolved.effective_start_at is not None


@pytest.mark.asyncio
async def test_current_tariff_read_flow_remains_compatible(session_factory):
    async with session_factory() as session:
        repo = TariffRepository(session, build_service_tenant_context("SH00000001"))
        await repo.upsert_tariff(
            tenant_id="SH00000001",
            data={"tenant_id": "SH00000001", "energy_rate_per_kwh": 6.5, "currency": "INR"},
        )
        current = await repo.get_tariff("SH00000001")

    assert current is not None
    assert float(current.energy_rate_per_kwh) == 6.5
    assert current.currency == "INR"


@pytest.mark.asyncio
async def test_report_revision_metadata_persists_before_authoritative_switch(session_factory):
    async with session_factory() as session:
        repo = ReportRepository(session, ctx=build_service_tenant_context("SH00000001"))
        first = await repo.create_report(
            report_id="report-v1",
            report_type="consumption",
            params={"tenant_id": "SH00000001", "device_id": "DEVICE-1"},
            tenant_id="SH00000001",
        )
        second = await repo.create_revision_report(
            new_report_id="report-v2",
            supersedes_report_id="report-v1",
            revision_reason="corrected_after_reconciliation",
            tenant_id="SH00000001",
            generated_from_reconciliation_run_id="run-001",
            tariff_version_id=5,
        )
        refreshed_first = await repo.get_report("report-v1", tenant_id="SH00000001")
        refreshed_second = await repo.get_report("report-v2", tenant_id="SH00000001")

    assert first.root_report_id == "report-v1"
    assert first.revision_number == 1
    assert refreshed_first is not None and refreshed_first.is_authoritative is True
    assert refreshed_first.superseded_by_report_id is None
    assert refreshed_second is not None
    assert refreshed_second.root_report_id == "report-v1"
    assert refreshed_second.revision_number == 2
    assert refreshed_second.supersedes_report_id == "report-v1"
    assert refreshed_second.is_authoritative is False
    assert refreshed_second.generated_from_reconciliation_run_id == "run-001"
    assert refreshed_second.tariff_version_id == 5


def test_reporting_local_bootstrap_defaults_safe():
    assert settings.LOCAL_BOOTSTRAP_ENABLED is False


def test_reporting_local_bootstrap_contract_blocks_production(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "LOCAL_BOOTSTRAP_ENABLED", True)

    with pytest.raises(RuntimeError, match="LOCAL_BOOTSTRAP_ENABLED cannot be true in production"):
        validate_local_bootstrap_contract()


@pytest.mark.asyncio
async def test_local_tariff_bootstrap_creates_deterministic_demo_tariff(monkeypatch, session_factory):
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "LOCAL_BOOTSTRAP_ENABLED", True)
    monkeypatch.setattr(settings, "LOCAL_BOOTSTRAP_TENANT_ID", "SH00000001")
    monkeypatch.setattr(settings, "LOCAL_BOOTSTRAP_TARIFF_RATE", 8.0)
    monkeypatch.setattr(settings, "LOCAL_BOOTSTRAP_TARIFF_CURRENCY", "INR")
    monkeypatch.setattr(settings, "LOCAL_BOOTSTRAP_TARIFF_DEMAND_CHARGE_PER_KW", 0.0)
    monkeypatch.setattr(settings, "LOCAL_BOOTSTRAP_TARIFF_REACTIVE_PENALTY_RATE", 0.0)
    monkeypatch.setattr(settings, "LOCAL_BOOTSTRAP_TARIFF_FIXED_MONTHLY_CHARGE", 0.0)
    monkeypatch.setattr(settings, "LOCAL_BOOTSTRAP_TARIFF_POWER_FACTOR_THRESHOLD", 0.9)
    monkeypatch.setattr(settings, "LOCAL_BOOTSTRAP_TARIFF_EFFECTIVE_START_AT", "2026-01-01T00:00:00+00:00")

    async with session_factory() as session:
        result = await ensure_local_tariff_bootstrap(session)
        repo = TariffRepository(session, build_service_tenant_context("SH00000001"))
        tariff = await repo.get_tariff("SH00000001")
        versions = await repo.list_versions("SH00000001")

    assert result == {"tariff_created": True, "tariff_updated": False}
    assert tariff is not None
    assert float(tariff.energy_rate_per_kwh) == 8.0
    assert tariff.currency == "INR"
    assert len(versions) == 1
    assert versions[0].version_number == 1
    assert versions[0].effective_start_at == datetime(2026, 1, 1, 0, 0, 0)


@pytest.mark.asyncio
async def test_local_tariff_bootstrap_is_idempotent_and_normalizes_existing_tariff(monkeypatch, session_factory):
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "LOCAL_BOOTSTRAP_ENABLED", True)
    monkeypatch.setattr(settings, "LOCAL_BOOTSTRAP_TENANT_ID", "SH00000001")
    monkeypatch.setattr(settings, "LOCAL_BOOTSTRAP_TARIFF_RATE", 8.0)
    monkeypatch.setattr(settings, "LOCAL_BOOTSTRAP_TARIFF_CURRENCY", "INR")
    monkeypatch.setattr(settings, "LOCAL_BOOTSTRAP_TARIFF_DEMAND_CHARGE_PER_KW", 0.0)
    monkeypatch.setattr(settings, "LOCAL_BOOTSTRAP_TARIFF_REACTIVE_PENALTY_RATE", 0.0)
    monkeypatch.setattr(settings, "LOCAL_BOOTSTRAP_TARIFF_FIXED_MONTHLY_CHARGE", 0.0)
    monkeypatch.setattr(settings, "LOCAL_BOOTSTRAP_TARIFF_POWER_FACTOR_THRESHOLD", 0.9)
    monkeypatch.setattr(settings, "LOCAL_BOOTSTRAP_TARIFF_EFFECTIVE_START_AT", "2026-01-01T00:00:00+00:00")

    async with session_factory() as session:
        repo = TariffRepository(session, build_service_tenant_context("SH00000001"))
        await repo.upsert_tariff(
            tenant_id="SH00000001",
            data={
                "tenant_id": "SH00000001",
                "energy_rate_per_kwh": 6.5,
                "currency": "INR",
                "effective_start_at": datetime(2025, 12, 1, 0, 0, 0),
                "change_reason": "old",
            },
        )
        first = await ensure_local_tariff_bootstrap(session)
        second = await ensure_local_tariff_bootstrap(session)
        tariff = await repo.get_tariff("SH00000001")
        versions = await repo.list_versions("SH00000001")

    assert first == {"tariff_created": False, "tariff_updated": True}
    assert second == {"tariff_created": False, "tariff_updated": False}
    assert tariff is not None
    assert float(tariff.energy_rate_per_kwh) == 8.0
    assert len(versions) == 2
    assert versions[-1].effective_start_at == datetime(2026, 1, 1, 0, 0, 0)


@pytest.mark.asyncio
async def test_local_tariff_bootstrap_ignores_later_legitimate_revisions(monkeypatch, session_factory):
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "LOCAL_BOOTSTRAP_ENABLED", True)
    monkeypatch.setattr(settings, "LOCAL_BOOTSTRAP_TENANT_ID", "SH00000001")
    monkeypatch.setattr(settings, "LOCAL_BOOTSTRAP_TARIFF_RATE", 8.0)
    monkeypatch.setattr(settings, "LOCAL_BOOTSTRAP_TARIFF_CURRENCY", "INR")
    monkeypatch.setattr(settings, "LOCAL_BOOTSTRAP_TARIFF_DEMAND_CHARGE_PER_KW", 0.0)
    monkeypatch.setattr(settings, "LOCAL_BOOTSTRAP_TARIFF_REACTIVE_PENALTY_RATE", 0.0)
    monkeypatch.setattr(settings, "LOCAL_BOOTSTRAP_TARIFF_FIXED_MONTHLY_CHARGE", 0.0)
    monkeypatch.setattr(settings, "LOCAL_BOOTSTRAP_TARIFF_POWER_FACTOR_THRESHOLD", 0.9)
    monkeypatch.setattr(settings, "LOCAL_BOOTSTRAP_TARIFF_EFFECTIVE_START_AT", "2026-01-01T00:00:00+00:00")

    async with session_factory() as session:
        repo = TariffRepository(session, build_service_tenant_context("SH00000001"))
        created = await ensure_local_tariff_bootstrap(session)
        await repo.upsert_tariff(
            tenant_id="SH00000001",
            data={
                "tenant_id": "SH00000001",
                "energy_rate_per_kwh": 9.25,
                "currency": "INR",
                "effective_start_at": datetime(2026, 2, 1, 0, 0, 0),
                "change_reason": "finance_revision",
                "created_by": "finance",
            },
        )
        replay = await ensure_local_tariff_bootstrap(session)
        tariff = await repo.get_tariff("SH00000001")
        versions = await repo.list_versions("SH00000001")

    assert created == {"tariff_created": True, "tariff_updated": False}
    assert replay == {"tariff_created": False, "tariff_updated": False}
    assert tariff is not None
    assert float(tariff.energy_rate_per_kwh) == 9.25
    assert len(versions) == 2
    assert versions[0].effective_start_at == datetime(2026, 1, 1, 0, 0, 0)
    assert versions[1].effective_start_at == datetime(2026, 2, 1, 0, 0, 0)


@pytest.mark.asyncio
async def test_local_tariff_bootstrap_blocks_true_bootstrap_mismatch(monkeypatch, session_factory):
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "LOCAL_BOOTSTRAP_ENABLED", True)
    monkeypatch.setattr(settings, "LOCAL_BOOTSTRAP_TENANT_ID", "SH00000001")
    monkeypatch.setattr(settings, "LOCAL_BOOTSTRAP_TARIFF_RATE", 8.0)
    monkeypatch.setattr(settings, "LOCAL_BOOTSTRAP_TARIFF_CURRENCY", "INR")
    monkeypatch.setattr(settings, "LOCAL_BOOTSTRAP_TARIFF_DEMAND_CHARGE_PER_KW", 0.0)
    monkeypatch.setattr(settings, "LOCAL_BOOTSTRAP_TARIFF_REACTIVE_PENALTY_RATE", 0.0)
    monkeypatch.setattr(settings, "LOCAL_BOOTSTRAP_TARIFF_FIXED_MONTHLY_CHARGE", 0.0)
    monkeypatch.setattr(settings, "LOCAL_BOOTSTRAP_TARIFF_POWER_FACTOR_THRESHOLD", 0.9)
    monkeypatch.setattr(settings, "LOCAL_BOOTSTRAP_TARIFF_EFFECTIVE_START_AT", "2026-01-01T00:00:00+00:00")

    async with session_factory() as session:
        repo = TariffRepository(session, build_service_tenant_context("SH00000001"))
        await repo.upsert_tariff(
            tenant_id="SH00000001",
            data={
                "tenant_id": "SH00000001",
                "energy_rate_per_kwh": 7.0,
                "currency": "INR",
                "effective_start_at": datetime(2026, 1, 1, 0, 0, 0),
                "change_reason": "manual_seed",
                "created_by": "ops",
            },
        )

        with pytest.raises(RuntimeError, match="baseline does not match"):
            await ensure_local_tariff_bootstrap(session)
