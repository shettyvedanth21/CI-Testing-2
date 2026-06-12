"""Add normalized hardware inventory and installation history.

Revision ID: 20260407_0002_hardware_inventory_foundation
Revises: 20260407_0001_prefixed_device_ids
Create Date: 2026-04-07
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision = "20260407_0002_hardware_inventory_foundation"
down_revision = "20260407_0001_prefixed_device_ids"
branch_labels = None
depends_on = None


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _table_exists(table_name: str) -> bool:
    return table_name in _inspector().get_table_names()


def _index_names(table_name: str) -> set[str]:
    return {index["name"] for index in _inspector().get_indexes(table_name)}


def _create_index_if_missing(name: str, table_name: str, columns: list[str]) -> None:
    if name not in _index_names(table_name):
        op.create_index(name, table_name, columns)


def _ensure_hardware_units_table() -> None:
    if not _table_exists("hardware_units"):
        op.create_table(
            "hardware_units",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("hardware_unit_id", sa.String(length=100), nullable=False),
            sa.Column("tenant_id", sa.String(length=50), nullable=False),
            sa.Column("plant_id", sa.String(length=36), nullable=False),
            sa.Column("unit_type", sa.String(length=100), nullable=False),
            sa.Column("manufacturer", sa.String(length=255), nullable=True),
            sa.Column("model", sa.String(length=255), nullable=True),
            sa.Column("serial_number", sa.String(length=255), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="available"),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("tenant_id", "hardware_unit_id", name="uq_hardware_units_tenant_unit_id"),
        )

    _create_index_if_missing("ix_hardware_units_tenant_id", "hardware_units", ["tenant_id"])
    _create_index_if_missing("ix_hardware_units_plant_id", "hardware_units", ["plant_id"])
    _create_index_if_missing("ix_hardware_units_status", "hardware_units", ["status"])
    _create_index_if_missing("ix_hardware_units_unit_type", "hardware_units", ["unit_type"])
    _create_index_if_missing(
        "ix_hardware_units_tenant_plant_type",
        "hardware_units",
        ["tenant_id", "plant_id", "unit_type"],
    )


def _ensure_device_hardware_installations_table() -> None:
    if not _table_exists("device_hardware_installations"):
        op.create_table(
            "device_hardware_installations",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("tenant_id", sa.String(length=50), nullable=False),
            sa.Column("plant_id", sa.String(length=36), nullable=False),
            sa.Column("device_id", sa.String(length=50), nullable=False),
            sa.Column("hardware_unit_id", sa.String(length=100), nullable=False),
            sa.Column("installation_role", sa.String(length=100), nullable=False),
            sa.Column("commissioned_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("decommissioned_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("active_hardware_unit_key", sa.String(length=100), nullable=True),
            sa.Column("active_device_role_key", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["device_id", "tenant_id"],
                ["devices.device_id", "devices.tenant_id"],
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["tenant_id", "hardware_unit_id"],
                ["hardware_units.tenant_id", "hardware_units.hardware_unit_id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "tenant_id",
                "active_hardware_unit_key",
                name="uq_device_hardware_installations_active_unit",
            ),
            sa.UniqueConstraint(
                "tenant_id",
                "active_device_role_key",
                name="uq_device_hardware_installations_active_role",
            ),
        )

    _create_index_if_missing(
        "ix_device_hardware_installations_tenant_id",
        "device_hardware_installations",
        ["tenant_id"],
    )
    _create_index_if_missing(
        "ix_device_hardware_installations_plant_id",
        "device_hardware_installations",
        ["plant_id"],
    )
    _create_index_if_missing(
        "ix_device_hardware_installations_device_id",
        "device_hardware_installations",
        ["device_id"],
    )
    _create_index_if_missing(
        "ix_device_hardware_installations_hardware_unit_id",
        "device_hardware_installations",
        ["hardware_unit_id"],
    )
    _create_index_if_missing(
        "ix_device_hardware_installations_device_history",
        "device_hardware_installations",
        ["tenant_id", "device_id", "commissioned_at"],
    )
    _create_index_if_missing(
        "ix_device_hardware_installations_hardware_history",
        "device_hardware_installations",
        ["tenant_id", "hardware_unit_id", "commissioned_at"],
    )


def upgrade() -> None:
    _ensure_hardware_units_table()
    _ensure_device_hardware_installations_table()


def downgrade() -> None:
    op.drop_index(
        "ix_device_hardware_installations_hardware_history",
        table_name="device_hardware_installations",
    )
    op.drop_index(
        "ix_device_hardware_installations_device_history",
        table_name="device_hardware_installations",
    )
    op.drop_index(
        "ix_device_hardware_installations_hardware_unit_id",
        table_name="device_hardware_installations",
    )
    op.drop_index(
        "ix_device_hardware_installations_device_id",
        table_name="device_hardware_installations",
    )
    op.drop_index(
        "ix_device_hardware_installations_plant_id",
        table_name="device_hardware_installations",
    )
    op.drop_index(
        "ix_device_hardware_installations_tenant_id",
        table_name="device_hardware_installations",
    )
    op.drop_table("device_hardware_installations")

    op.drop_index("ix_hardware_units_tenant_plant_type", table_name="hardware_units")
    op.drop_index("ix_hardware_units_unit_type", table_name="hardware_units")
    op.drop_index("ix_hardware_units_status", table_name="hardware_units")
    op.drop_index("ix_hardware_units_plant_id", table_name="hardware_units")
    op.drop_index("ix_hardware_units_tenant_id", table_name="hardware_units")
    op.drop_table("hardware_units")
