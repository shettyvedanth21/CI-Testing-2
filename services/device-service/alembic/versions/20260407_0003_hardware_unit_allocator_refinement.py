"""Refine hardware units to generated IDs and permanent inventory fields.

Revision ID: 20260407_0003_hardware_unit_allocator_refinement
Revises: 20260407_0002_hardware_inventory_foundation
Create Date: 2026-04-07
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision = "20260407_0003_hardware_unit_allocator_refinement"
down_revision = "20260407_0002_hardware_inventory_foundation"
branch_labels = None
depends_on = None

_HARDWARE_UNIT_PREFIX = "HWU"


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _table_exists(table_name: str) -> bool:
    return table_name in _inspector().get_table_names()


def _column_names(table_name: str) -> set[str]:
    return {column["name"] for column in _inspector().get_columns(table_name)}


def upgrade() -> None:
    if _table_exists("hardware_units"):
        hardware_unit_columns = _column_names("hardware_units")
        if "unit_name" not in hardware_unit_columns:
            with op.batch_alter_table("hardware_units") as batch_op:
                batch_op.add_column(sa.Column("unit_name", sa.String(length=255), nullable=True))

        op.execute(
            sa.text(
                """
                UPDATE hardware_units
                SET unit_name = COALESCE(NULLIF(unit_name, ''), hardware_unit_id)
                """
            )
        )
        op.execute(
            sa.text(
                """
                UPDATE hardware_units
                SET status = 'available'
                WHERE status NOT IN ('available', 'retired')
                """
            )
        )

        with op.batch_alter_table("hardware_units") as batch_op:
            batch_op.alter_column("unit_name", existing_type=sa.String(length=255), nullable=False)
            if "metadata_json" in hardware_unit_columns:
                batch_op.drop_column("metadata_json")

    if not _table_exists("hardware_unit_sequences"):
        op.create_table(
            "hardware_unit_sequences",
            sa.Column("prefix", sa.String(length=3), nullable=False),
            sa.Column("next_value", sa.BigInteger(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("prefix"),
        )

    op.execute(
        sa.text(
            """
            INSERT INTO hardware_unit_sequences (prefix, next_value, updated_at)
            SELECT :prefix, 1, CURRENT_TIMESTAMP
            WHERE NOT EXISTS (
                SELECT 1 FROM hardware_unit_sequences WHERE prefix = :prefix
            )
            """
        ).bindparams(prefix=_HARDWARE_UNIT_PREFIX)
    )


def downgrade() -> None:
    if _table_exists("hardware_units"):
        hardware_unit_columns = _column_names("hardware_units")
        with op.batch_alter_table("hardware_units") as batch_op:
            if "metadata_json" not in hardware_unit_columns:
                batch_op.add_column(sa.Column("metadata_json", sa.JSON(), nullable=True))
            if "unit_name" in hardware_unit_columns:
                batch_op.drop_column("unit_name")

    if _table_exists("hardware_unit_sequences"):
        op.drop_table("hardware_unit_sequences")
