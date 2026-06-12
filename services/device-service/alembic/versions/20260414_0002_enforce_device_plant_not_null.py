"""Enforce non-null plant assignment for devices.

Revision ID: 20260414_0002
Revises: 20260414_0001_fla_threshold_redesign
Create Date: 2026-04-14
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260414_0002"
down_revision = "20260414_0001_fla_threshold_redesign"
branch_labels = None
depends_on = None


def _plant_column_nullable(inspector) -> bool:
    for column in inspector.get_columns("devices"):
        if column["name"] == "plant_id":
            return bool(column.get("nullable", True))
    raise RuntimeError("devices.plant_id column not found")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _plant_column_nullable(inspector):
        return

    orphan_rows = bind.execute(
        sa.text(
            """
            SELECT device_id, tenant_id
            FROM devices
            WHERE plant_id IS NULL
            ORDER BY tenant_id, device_id
            LIMIT 10
            """
        )
    ).fetchall()
    if orphan_rows:
        sample = ", ".join(f"{row.tenant_id}/{row.device_id}" for row in orphan_rows)
        raise RuntimeError(
            "Migration 20260414_0002 cannot enforce devices.plant_id NOT NULL while orphan devices exist. "
            "Repair or remove every device with plant_id IS NULL before rerunning. "
            f"Sample rows: {sample}"
        )

    op.alter_column(
        "devices",
        "plant_id",
        existing_type=sa.String(length=36),
        nullable=False,
        existing_comment="References plants.id in auth-service. Soft reference - no DB-level FK constraint across service boundaries.",
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _plant_column_nullable(inspector):
        return

    op.alter_column(
        "devices",
        "plant_id",
        existing_type=sa.String(length=36),
        nullable=True,
        existing_comment="References plants.id in auth-service. Soft reference - no DB-level FK constraint across service boundaries.",
    )
