from __future__ import annotations

import os
import sys
from datetime import datetime, time, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

BASE_DIR = Path(__file__).resolve().parents[1]
SERVICES_DIR = Path(__file__).resolve().parents[2]
PROJECT_ROOT = Path(__file__).resolve().parents[3]
WASTE_SERVICE_DIR = PROJECT_ROOT / "services" / "waste-analysis-service"
for existing in list(sys.path):
    try:
        existing_path = Path(existing).resolve()
    except Exception:
        continue
    if existing_path.parent == SERVICES_DIR.resolve() and existing_path != BASE_DIR.resolve():
        sys.path.remove(existing)
for module_name, module in list(sys.modules.items()):
    if module_name == "app" or module_name.startswith("app."):
        module_file = Path(getattr(module, "__file__", "") or "")
        if str(module_file) and BASE_DIR.resolve() not in module_file.resolve().parents:
            sys.modules.pop(module_name, None)
for path in (SERVICES_DIR, PROJECT_ROOT, WASTE_SERVICE_DIR, BASE_DIR):
    path_str = str(path)
    if path_str in sys.path:
        sys.path.remove(path_str)
    sys.path.insert(0, path_str)

os.environ["DATABASE_URL"] = "mysql+aiomysql://test:test@127.0.0.1:3306/test_db"

from app.database import Base
from app.models.device import Device, DeviceLiveState, DeviceShift
from app.services.dashboard import DashboardService
from app.services import live_projection as live_projection_module
from app.services.live_projection import LiveProjectionService
from services.shared.tenant_context import TenantContext
from src.services.waste_engine import compute_device_waste


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


@pytest.mark.asyncio
async def test_live_projection_loss_stats_and_waste_analysis_share_exclusive_accounting(monkeypatch, session_factory):
    rows = [
        {"timestamp": "2026-04-04T04:30:00+00:00", "current": 0.7, "voltage": 230.0, "power": 160.0},
        {"timestamp": "2026-04-04T04:40:00+00:00", "current": 0.7, "voltage": 230.0, "power": 160.0},
        {"timestamp": "2026-04-04T05:00:00+00:00", "current": 25.0, "voltage": 230.0, "power": 5750.0},
        {"timestamp": "2026-04-04T05:10:00+00:00", "current": 25.0, "voltage": 230.0, "power": 5750.0},
        {"timestamp": "2026-04-04T13:00:00+00:00", "current": 0.7, "voltage": 230.0, "power": 160.0},
        {"timestamp": "2026-04-04T13:10:00+00:00", "current": 0.7, "voltage": 230.0, "power": 160.0},
    ]

    async with session_factory() as session:
        session.add_all(
            [
                Device(
                    device_id="LOSS-DEVICE-1",
                    tenant_id="TENANT-A",
                    plant_id="PLANT-1",
                    device_name="Loss Device",
                    device_type="compressor",
                    full_load_current_a=20.0,
                    idle_threshold_pct_of_fla=0.05,
                ),
                DeviceLiveState(
                    device_id="LOSS-DEVICE-1",
                    tenant_id="TENANT-A",
                    runtime_status="running",
                    load_state="running",
                    version=0,
                ),
                DeviceShift(
                    device_id="LOSS-DEVICE-1",
                    tenant_id="TENANT-A",
                    shift_name="Day Shift",
                    shift_start=time(9, 0),
                    shift_end=time(17, 0),
                    maintenance_break_minutes=0,
                    day_of_week=None,
                    is_active=True,
                ),
            ]
        )
        await session.commit()

        service = LiveProjectionService(session)

        async def fake_tariff_get(_tenant_id):
            return {"configured": True, "rate": 5.0, "currency": "INR", "stale": False, "cache": "miss"}

        monkeypatch.setattr(live_projection_module.TariffCache, "get", AsyncMock(side_effect=fake_tariff_get))
        monkeypatch.setattr(service, "_fetch_telemetry_window", AsyncMock(return_value=rows))
        monkeypatch.setattr(DashboardService, "_get_tariff", AsyncMock(return_value=(5.0, "INR")))

        await service.recompute_today_loss_projection("LOSS-DEVICE-1", "TENANT-A")
        loss_stats = await DashboardService(session, _tenant_ctx("TENANT-A")).get_device_loss_stats(
            "LOSS-DEVICE-1",
            "TENANT-A",
        )

    waste = compute_device_waste(
        device_id="LOSS-DEVICE-1",
        device_name="Loss Device",
        data_source_type="metered",
        rows=rows,
        threshold=1.0,
        overconsumption_threshold=20.0,
        tariff_rate=5.0,
        shifts=[{"shift_start": "09:00", "shift_end": "17:00", "is_active": True}],
    )

    assert loss_stats["today"]["idle_kwh"] == pytest.approx(waste.idle_energy_kwh, abs=1e-4)
    assert loss_stats["today"]["off_hours_kwh"] == pytest.approx(float(waste.offhours_energy_kwh or 0.0), abs=1e-4)
    assert loss_stats["today"]["overconsumption_kwh"] == pytest.approx(float(waste.overconsumption_energy_kwh or 0.0), abs=1e-4)
    assert loss_stats["today"]["total_loss_kwh"] == pytest.approx(
        waste.idle_energy_kwh + float(waste.offhours_energy_kwh or 0.0) + float(waste.overconsumption_energy_kwh or 0.0),
        abs=1e-4,
    )
