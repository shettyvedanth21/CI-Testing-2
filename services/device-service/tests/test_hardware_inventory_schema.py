from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


BASE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
SERVICES_DIR = REPO_ROOT / "services"
for path in (REPO_ROOT, SERVICES_DIR, BASE_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.environ["DATABASE_URL"] = "mysql+aiomysql://test:test@127.0.0.1:3306/test_db"

from app.models.device import DeviceHardwareInstallation, HardwareUnit
from app.schemas.device import DeviceHardwareInstallationCreate, HardwareUnitCreate


def test_hardware_installation_fk_matches_hardware_unit_unique_key_order():
    fk = next(
        constraint
        for constraint in DeviceHardwareInstallation.__table__.foreign_key_constraints
        if constraint.referred_table.name == "hardware_units"
    )

    local_columns = [column.parent.name for column in fk.elements]
    remote_columns = [column.column.name for column in fk.elements]

    assert local_columns == ["tenant_id", "hardware_unit_id"]
    assert remote_columns == ["tenant_id", "hardware_unit_id"]


def test_hardware_unit_schema_uses_unit_name_without_metadata_json():
    columns = HardwareUnit.__table__.columns.keys()

    assert "unit_name" in columns
    assert "metadata_json" not in columns


def test_hardware_contract_rejects_invalid_controlled_values():
    with pytest.raises(ValueError):
        HardwareUnitCreate(
            plant_id="PLANT-1",
            unit_type="custom_sensor",
            unit_name="Custom Sensor",
        )

    with pytest.raises(ValueError):
        DeviceHardwareInstallationCreate(
            hardware_unit_id="HWU00000001",
            installation_role="custom_slot",
        )
