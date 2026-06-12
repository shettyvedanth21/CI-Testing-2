from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from decimal import Decimal

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

ROOT = Path(__file__).resolve().parents[3]
SERVICE_ROOT = ROOT / "services" / "reporting-service"
SERVICES_ROOT = ROOT / "services"
for path in (ROOT, SERVICE_ROOT, SERVICES_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("INFLUXDB_URL", "http://localhost:8086")
os.environ.setdefault("INFLUXDB_TOKEN", "test-token")

from src.models.tenant_tariffs import Base as TenantTariffBase
from src.repositories.tariff_repository import TariffRepository
from src.schemas.requests import TariffRequest
from src.services.tariff_resolver import resolve_tariff
from src.services.tenant_scope import build_service_tenant_context


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(TenantTariffBase.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    ("payload", "expected_message"),
    [
        (
            {
                "tenant_id": "TENANT-A",
                "energy_rate_per_kwh": Decimal("-1"),
                "currency": "INR",
            },
            "greater than 0",
        ),
        (
            {
                "tenant_id": "TENANT-A",
                "energy_rate_per_kwh": Decimal("8.5"),
                "power_factor_threshold": Decimal("1.5"),
                "currency": "INR",
            },
            "less than or equal to 1",
        ),
        (
            {
                "tenant_id": "TENANT-A",
                "energy_rate_per_kwh": Decimal("8.5"),
                "currency": "rupees",
            },
            "3-letter ISO-style code",
        ),
    ],
)
def test_tariff_request_rejects_invalid_inputs(payload, expected_message):
    with pytest.raises(ValidationError, match=expected_message):
        TariffRequest(**payload)


@pytest.mark.asyncio
async def test_resolve_tariff_switches_versions_at_exact_boundary(session_factory):
    start_a = datetime(2026, 4, 1, 0, 0, 0)
    start_b = datetime(2026, 5, 1, 0, 0, 0)

    async with session_factory() as session:
        repo = TariffRepository(session, build_service_tenant_context("TENANT-A"))
        await repo.upsert_tariff(
            tenant_id="TENANT-A",
            data={
                "tenant_id": "TENANT-A",
                "energy_rate_per_kwh": 6.5,
                "currency": "INR",
                "effective_start_at": start_a,
            },
        )
        await repo.upsert_tariff(
            tenant_id="TENANT-A",
            data={
                "tenant_id": "TENANT-A",
                "energy_rate_per_kwh": 7.25,
                "currency": "INR",
                "effective_start_at": start_b,
            },
        )

        just_before = await resolve_tariff(session, "TENANT-A", effective_at=start_b - timedelta(seconds=1))
        at_boundary = await resolve_tariff(session, "TENANT-A", effective_at=start_b)

    assert just_before.rate == 6.5
    assert just_before.source == "tenant_tariff_versions"
    assert at_boundary.rate == 7.25
    assert at_boundary.source == "tenant_tariff_versions"
