"""Reviewed remediation workflow for legacy hardware data."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Connection

from app.schemas.device import ALLOWED_HARDWARE_UNIT_TYPES, ALLOWED_INSTALLATION_ROLES


@dataclass(frozen=True, slots=True)
class HardwareUnitLegacyRow:
    id: int
    hardware_unit_id: str
    tenant_id: str
    plant_id: str
    unit_type: str
    unit_name: str
    manufacturer: str | None
    model: str | None
    serial_number: str | None
    status: str


@dataclass(frozen=True, slots=True)
class InstallationLegacyRow:
    id: int
    tenant_id: str
    plant_id: str
    device_id: str
    hardware_unit_id: str
    installation_role: str
    commissioned_at: str
    decommissioned_at: str | None
    notes: str | None


@dataclass(frozen=True, slots=True)
class ReviewedRemediation:
    table_name: str
    row_id: int
    current_value: str
    proposed_value: str | None
    reason: str
    ambiguous: bool


@dataclass(frozen=True, slots=True)
class _ReviewedValue:
    expected_value: str
    canonical_value: str
    reason: str


_REVIEWED_HARDWARE_UNIT_FIXES: dict[int, _ReviewedValue] = {
    4: _ReviewedValue(
        expected_value="EPS32",
        canonical_value="esp32",
        reason=(
            "Exact transpose typo of allowed value 'esp32'. The row is retired and has no "
            "installation history, so correcting the inventory category does not alter any "
            "recorded hardware lifecycle."
        ),
    ),
}


def fetch_invalid_hardware_units(connection: Connection) -> list[HardwareUnitLegacyRow]:
    statement = text(
        """
        SELECT
            id,
            hardware_unit_id,
            tenant_id,
            plant_id,
            unit_type,
            unit_name,
            manufacturer,
            model,
            serial_number,
            status
        FROM hardware_units
        WHERE unit_type NOT IN :allowed_values
        ORDER BY id
        """
    ).bindparams(bindparam("allowed_values", expanding=True))
    result = connection.execute(
        statement,
        {"allowed_values": tuple(sorted(ALLOWED_HARDWARE_UNIT_TYPES))},
    )
    return [HardwareUnitLegacyRow(*row) for row in result.fetchall()]


def fetch_invalid_installations(connection: Connection) -> list[InstallationLegacyRow]:
    statement = text(
        """
        SELECT
            id,
            tenant_id,
            plant_id,
            device_id,
            hardware_unit_id,
            installation_role,
            CAST(commissioned_at AS CHAR),
            CAST(decommissioned_at AS CHAR),
            notes
        FROM device_hardware_installations
        WHERE installation_role NOT IN :allowed_values
        ORDER BY id
        """
    ).bindparams(bindparam("allowed_values", expanding=True))
    result = connection.execute(
        statement,
        {"allowed_values": tuple(sorted(ALLOWED_INSTALLATION_ROLES))},
    )
    return [InstallationLegacyRow(*row) for row in result.fetchall()]


def group_rows_by_value(rows: Iterable[HardwareUnitLegacyRow | InstallationLegacyRow]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = row.unit_type if isinstance(row, HardwareUnitLegacyRow) else row.installation_role
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def build_hardware_unit_review_plan(
    rows: Iterable[HardwareUnitLegacyRow],
) -> list[ReviewedRemediation]:
    plan: list[ReviewedRemediation] = []
    for row in rows:
        reviewed = _REVIEWED_HARDWARE_UNIT_FIXES.get(row.id)
        if reviewed is None:
            plan.append(
                ReviewedRemediation(
                    table_name="hardware_units",
                    row_id=row.id,
                    current_value=row.unit_type,
                    proposed_value=None,
                    reason=(
                        "No reviewed canonical mapping exists for this saved unit_type. "
                        "The value conflicts with surrounding context and cannot be corrected safely without human review."
                    ),
                    ambiguous=True,
                )
            )
            continue

        if row.unit_type != reviewed.expected_value:
            raise ValueError(
                f"Reviewed remediation for hardware_units.id={row.id} expected "
                f"{reviewed.expected_value!r} but found {row.unit_type!r}"
            )

        plan.append(
            ReviewedRemediation(
                table_name="hardware_units",
                row_id=row.id,
                current_value=row.unit_type,
                proposed_value=reviewed.canonical_value,
                reason=reviewed.reason,
                ambiguous=False,
            )
        )
    return plan


def build_installation_review_plan(
    rows: Iterable[InstallationLegacyRow],
) -> list[ReviewedRemediation]:
    return [
        ReviewedRemediation(
            table_name="device_hardware_installations",
            row_id=row.id,
            current_value=row.installation_role,
            proposed_value=None,
            reason=(
                "No reviewed canonical mapping exists for this saved installation_role. "
                "The value is too ambiguous to rewrite safely without corroborating evidence."
            ),
            ambiguous=True,
        )
        for row in rows
    ]


def apply_reviewed_hardware_unit_fixes(
    connection: Connection,
    *,
    plan: Iterable[ReviewedRemediation],
) -> int:
    applied = 0
    for item in plan:
        if item.table_name != "hardware_units" or item.ambiguous or item.proposed_value is None:
            continue

        result = connection.execute(
            text(
                """
                UPDATE hardware_units
                SET unit_type = :proposed_value,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :row_id
                  AND unit_type = :current_value
                """
            ),
            {
                "proposed_value": item.proposed_value,
                "row_id": item.row_id,
                "current_value": item.current_value,
            },
        )
        if int(result.rowcount or 0) != 1:
            raise RuntimeError(
                f"Expected to update exactly one hardware_units row for id={item.row_id}, "
                f"but updated {int(result.rowcount or 0)}"
            )
        applied += 1
    return applied
