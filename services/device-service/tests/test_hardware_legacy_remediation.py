from __future__ import annotations

import os
import sys
from pathlib import Path

from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, Text, create_engine, select


BASE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
SERVICES_DIR = REPO_ROOT / "services"
for path in (REPO_ROOT, SERVICES_DIR, BASE_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.environ["DATABASE_URL"] = "mysql+aiomysql://test:test@127.0.0.1:3306/test_db"

from app.services.hardware_legacy_remediation import (
    HardwareUnitLegacyRow,
    InstallationLegacyRow,
    apply_reviewed_hardware_unit_fixes,
    build_hardware_unit_review_plan,
    build_installation_review_plan,
)


def test_build_review_plan_only_marks_reviewed_eps32_row_as_unambiguous():
    hardware_plan = build_hardware_unit_review_plan(
        [
            HardwareUnitLegacyRow(
                id=4,
                hardware_unit_id="HWU00000004",
                tenant_id="ORG-1",
                plant_id="PLANT-1",
                unit_type="EPS32",
                unit_name="ES",
                manufacturer="IN",
                model="243",
                serial_number="224",
                status="retired",
            ),
            HardwareUnitLegacyRow(
                id=6,
                hardware_unit_id="HWU00000006",
                tenant_id="ORG-1",
                plant_id="PLANT-1",
                unit_type="sensor",
                unit_name="CT1",
                manufacturer="Sellac",
                model="g6",
                serial_number="122003993",
                status="available",
            ),
        ]
    )
    installation_plan = build_installation_review_plan(
        [
            InstallationLegacyRow(
                id=3,
                tenant_id="ORG-1",
                plant_id="PLANT-1",
                device_id="AD00000002",
                hardware_unit_id="HWU00000006",
                installation_role="contrp;;er",
                commissioned_at="2026-04-07 16:37:00",
                decommissioned_at=None,
                notes=None,
            )
        ]
    )

    assert hardware_plan[0].ambiguous is False
    assert hardware_plan[0].proposed_value == "esp32"
    assert hardware_plan[1].ambiguous is True
    assert hardware_plan[1].proposed_value is None
    assert installation_plan[0].ambiguous is True
    assert installation_plan[0].proposed_value is None


def test_apply_reviewed_hardware_unit_fixes_updates_only_guarded_rows():
    engine = create_engine("sqlite:///:memory:", future=True)
    metadata = MetaData()
    hardware_units = Table(
        "hardware_units",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("hardware_unit_id", String(100), nullable=False),
        Column("tenant_id", String(50), nullable=False),
        Column("plant_id", String(36), nullable=False),
        Column("unit_type", String(100), nullable=False),
        Column("unit_name", String(255), nullable=False),
        Column("manufacturer", String(255)),
        Column("model", String(255)),
        Column("serial_number", String(255)),
        Column("status", String(32), nullable=False),
        Column("created_at", DateTime, nullable=True),
        Column("updated_at", DateTime, nullable=True),
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        connection.execute(
            hardware_units.insert(),
            [
                {
                    "id": 4,
                    "hardware_unit_id": "HWU00000004",
                    "tenant_id": "ORG-1",
                    "plant_id": "PLANT-1",
                    "unit_type": "EPS32",
                    "unit_name": "ES",
                    "manufacturer": "IN",
                    "model": "243",
                    "serial_number": "224",
                    "status": "retired",
                    "created_at": None,
                    "updated_at": None,
                },
                {
                    "id": 6,
                    "hardware_unit_id": "HWU00000006",
                    "tenant_id": "ORG-1",
                    "plant_id": "PLANT-1",
                    "unit_type": "sensor",
                    "unit_name": "CT1",
                    "manufacturer": "Sellac",
                    "model": "g6",
                    "serial_number": "122003993",
                    "status": "available",
                    "created_at": None,
                    "updated_at": None,
                },
            ],
        )

        plan = build_hardware_unit_review_plan(
            [
                HardwareUnitLegacyRow(
                    id=4,
                    hardware_unit_id="HWU00000004",
                    tenant_id="ORG-1",
                    plant_id="PLANT-1",
                    unit_type="EPS32",
                    unit_name="ES",
                    manufacturer="IN",
                    model="243",
                    serial_number="224",
                    status="retired",
                ),
                HardwareUnitLegacyRow(
                    id=6,
                    hardware_unit_id="HWU00000006",
                    tenant_id="ORG-1",
                    plant_id="PLANT-1",
                    unit_type="sensor",
                    unit_name="CT1",
                    manufacturer="Sellac",
                    model="g6",
                    serial_number="122003993",
                    status="available",
                ),
            ]
        )
        applied = apply_reviewed_hardware_unit_fixes(connection, plan=plan)
        rows = connection.execute(select(hardware_units.c.id, hardware_units.c.unit_type).order_by(hardware_units.c.id)).all()

    assert applied == 1
    assert rows == [(4, "esp32"), (6, "sensor")]
