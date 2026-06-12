from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import os
import sys

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

os.environ.setdefault("DATABASE_URL", "mysql+aiomysql://test:test@127.0.0.1:3306/test_db")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

from app.database import Base
from app.models.device import Device, DeviceLiveState
from app.services.dashboard import DashboardService, _get_platform_tz
from app.services.idle_running import TariffCache
from services.shared.tenant_context import TenantContext


def _tenant_ctx() -> TenantContext:
    return TenantContext(
        tenant_id="TENANT-A",
        user_id="tester",
        role="system",
        plant_ids=[],
        is_super_admin=False,
    )


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


@pytest.mark.asyncio
async def test_dashboard_loss_stats_uses_tariff_cache(session_factory, monkeypatch: pytest.MonkeyPatch):
    async with session_factory() as session:
        now = datetime.now(timezone.utc)
        local_day = now.astimezone(_get_platform_tz()).date()
        session.add(
            Device(
                device_id="DEVICE-1",
                tenant_id="TENANT-A",
                plant_id="PLANT-1",
                device_name="Machine 1",
                device_type="compressor",
                location="Plant 1",
                full_load_current_a=20.0,
                idle_threshold_pct_of_fla=0.25,
                idle_current_threshold=5.0,
                last_seen_timestamp=now,
                first_telemetry_timestamp=now,
            )
        )
        session.add(
            DeviceLiveState(
                device_id="DEVICE-1",
                tenant_id="TENANT-A",
                runtime_status="running",
                load_state="idle",
                last_telemetry_ts=now,
                last_sample_ts=now,
                today_idle_kwh=2.0,
                today_offhours_kwh=1.0,
                today_overconsumption_kwh=0.5,
                today_loss_kwh=3.5,
                today_energy_kwh=9.0,
                day_bucket=local_day,
                version=3,
            )
        )
        await session.commit()

        async def fake_tariff_get(tenant_id: str | None = None):
            return {
                "configured": True,
                "rate": 7.5,
                "currency": "INR",
                "cache": "hit",
            }

        monkeypatch.setattr(TariffCache, "get", fake_tariff_get)

        payload = await DashboardService(session, _tenant_ctx()).get_device_loss_stats("DEVICE-1", "TENANT-A")

        assert payload["currency"] == "INR"
        assert payload["tariff_configured"] is True
        assert payload["today"]["idle_cost_inr"] == pytest.approx(15.0)
        assert payload["today"]["total_loss_cost_inr"] == pytest.approx(26.25)
