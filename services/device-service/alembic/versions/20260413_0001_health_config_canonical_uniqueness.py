"""Enforce one health config per device tenant canonical parameter.

Revision ID: 20260413_0001
Revises: 20260412_0002
Create Date: 2026-04-13
"""

from __future__ import annotations

from collections import defaultdict
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision = "20260413_0001"
down_revision = "20260412_0002"
branch_labels = None
depends_on = None


CANONICAL_PARAMETER_ALIASES: dict[str, tuple[str, ...]] = {
    "current": ("current_a", "phase_current"),
    "power": ("active_power", "active_power_kw", "business_power_w", "power_kw", "kw"),
    "power_factor": ("pf", "cos_phi", "powerfactor", "pf_business", "raw_power_factor"),
    "voltage": ("voltage_v",),
}
ALIASES_TO_CANONICAL = {
    alias.casefold(): canonical
    for canonical, aliases in CANONICAL_PARAMETER_ALIASES.items()
    for alias in aliases
}


def _canonical_parameter_name(parameter_name: str | None) -> str:
    normalized = str(parameter_name or "").strip().casefold()
    return ALIASES_TO_CANONICAL.get(normalized, normalized)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("parameter_health_config")}
    indexes = {index["name"] for index in inspector.get_indexes("parameter_health_config")}
    unique_constraints = {constraint["name"] for constraint in inspector.get_unique_constraints("parameter_health_config")}

    if "canonical_parameter_name" not in columns:
        op.add_column(
            "parameter_health_config",
            sa.Column("canonical_parameter_name", sa.String(length=100), nullable=True),
        )

    # Backfill tenant scope from devices where older rows are missing tenant_id.
    bind.execute(
        sa.text(
            """
            UPDATE parameter_health_config phc
            JOIN devices d ON d.device_id = phc.device_id
            SET phc.tenant_id = d.tenant_id
            WHERE phc.tenant_id IS NULL
              AND d.tenant_id IS NOT NULL
            """
        )
    )

    rows = bind.execute(
        sa.text(
            """
            SELECT id, tenant_id, device_id, parameter_name, created_at, updated_at
            FROM parameter_health_config
            ORDER BY id ASC
            """
        )
    ).mappings().all()

    grouped_ids: dict[tuple[str, str, str], list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        canonical = _canonical_parameter_name(row["parameter_name"])
        bind.execute(
            sa.text(
                """
                UPDATE parameter_health_config
                SET canonical_parameter_name = :canonical
                WHERE id = :config_id
                """
            ),
            {"canonical": canonical, "config_id": row["id"]},
        )
        tenant_key = str(row["tenant_id"] or "")
        device_key = str(row["device_id"] or "")
        grouped_ids[(tenant_key, device_key, canonical)].append(dict(row))

    duplicate_ids: list[int] = []
    for group_rows in grouped_ids.values():
        if len(group_rows) < 2:
            continue
        ordered = sorted(
            group_rows,
            key=lambda row: (
                row["updated_at"] or row["created_at"] or 0,
                row["created_at"] or 0,
                row["id"],
            ),
            reverse=True,
        )
        duplicate_ids.extend(int(row["id"]) for row in ordered[1:])

    if duplicate_ids:
        bind.execute(
            sa.text(
                """
                DELETE FROM parameter_health_config
                WHERE id IN :duplicate_ids
                """
            ).bindparams(sa.bindparam("duplicate_ids", expanding=True)),
            {"duplicate_ids": duplicate_ids},
        )

    op.alter_column(
        "parameter_health_config",
        "canonical_parameter_name",
        existing_type=sa.String(length=100),
        nullable=False,
    )
    if "ix_parameter_health_config_canonical_parameter_name" not in indexes:
        op.create_index(
            "ix_parameter_health_config_canonical_parameter_name",
            "parameter_health_config",
            ["canonical_parameter_name"],
            unique=False,
        )
    if "uq_parameter_health_config_tenant_device_canonical" not in unique_constraints:
        op.create_unique_constraint(
            "uq_parameter_health_config_tenant_device_canonical",
            "parameter_health_config",
            ["tenant_id", "device_id", "canonical_parameter_name"],
        )


def downgrade() -> None:
    op.drop_constraint(
        "uq_parameter_health_config_tenant_device_canonical",
        "parameter_health_config",
        type_="unique",
    )
    op.drop_index(
        "ix_parameter_health_config_canonical_parameter_name",
        table_name="parameter_health_config",
    )
    op.drop_column("parameter_health_config", "canonical_parameter_name")
