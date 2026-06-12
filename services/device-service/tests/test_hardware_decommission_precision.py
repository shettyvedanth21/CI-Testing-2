from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

BASE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
SERVICES_DIR = REPO_ROOT / "services"
for path in (REPO_ROOT, SERVICES_DIR, BASE_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.environ.setdefault("DATABASE_URL", "mysql+aiomysql://test:test@127.0.0.1:3306/test_db")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-at-least-32-characters-long")

from app.schemas.device import DeviceHardwareInstallationDecommission
from app.services.device_errors import HardwareInstallationConflictError
from app.services.hardware_inventory import HardwareInventoryService
from services.shared.tenant_context import TenantContext


def _ctx() -> TenantContext:
    return TenantContext(
        tenant_id="ORG-1",
        user_id="user-1",
        role="org_admin",
        plant_ids=["PLANT-1"],
        is_super_admin=False,
    )


@pytest.mark.asyncio
async def test_implicit_decommission_timestamp_is_clamped_to_commission_time():
    session = SimpleNamespace(commit=AsyncMock())
    service = HardwareInventoryService(session, _ctx())

    commissioned_at = datetime.now(timezone.utc) + timedelta(milliseconds=500)
    installation = SimpleNamespace(
        id=1,
        commissioned_at=commissioned_at,
        decommissioned_at=None,
        hardware_unit_id="HWU00000001",
        active_hardware_unit_key="HWU00000001",
        active_device_role_key="AD00000001::main_meter",
        notes="installed",
    )

    service._installations = SimpleNamespace(
        get_by_id=AsyncMock(return_value=installation),
        update=AsyncMock(side_effect=lambda row: row),
    )
    service._require_hardware_unit = AsyncMock(return_value=SimpleNamespace(hardware_unit_id="HWU00000001"))

    result = await service.decommission_installation(
        1,
        DeviceHardwareInstallationDecommission(notes="Immediate retirement"),
    )

    assert result.decommissioned_at == commissioned_at
    assert result.active_hardware_unit_key is None
    assert result.active_device_role_key is None
    assert result.notes == "Immediate retirement"
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_explicit_backdated_decommission_timestamp_is_still_rejected():
    session = SimpleNamespace(commit=AsyncMock())
    service = HardwareInventoryService(session, _ctx())

    commissioned_at = datetime(2026, 4, 9, 15, 51, 17, tzinfo=timezone.utc)
    installation = SimpleNamespace(
        id=1,
        commissioned_at=commissioned_at,
        decommissioned_at=None,
        hardware_unit_id="HWU00000001",
    )

    service._installations = SimpleNamespace(
        get_by_id=AsyncMock(return_value=installation),
        update=AsyncMock(),
    )
    service._require_hardware_unit = AsyncMock(return_value=SimpleNamespace(hardware_unit_id="HWU00000001"))

    with pytest.raises(HardwareInstallationConflictError, match="cannot be earlier"):
        await service.decommission_installation(
            1,
            DeviceHardwareInstallationDecommission(
                decommissioned_at=datetime(2026, 4, 9, 15, 51, 16, tzinfo=timezone.utc),
            ),
        )
