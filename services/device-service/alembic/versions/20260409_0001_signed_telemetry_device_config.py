"""Add signed telemetry normalization config to devices

Revision ID: 20260409_0001_signed_telemetry_device_config
Revises: 20260407_0003_hardware_unit_allocator_refinement
Create Date: 2026-04-09
"""

from typing import Union

from alembic import op
import sqlalchemy as sa


revision = "20260409_0001_signed_telemetry_device_config"
down_revision = "20260407_0003_hardware_unit_allocator_refinement"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    device_cols = {c["name"] for c in inspector.get_columns("devices")}
    existing_indexes = {i["name"] for i in inspector.get_indexes("devices")}

    if "energy_flow_mode" not in device_cols:
        op.add_column(
            "devices",
            sa.Column(
                "energy_flow_mode",
                sa.String(length=32),
                nullable=False,
                server_default="consumption_only",
            ),
        )
    if "polarity_mode" not in device_cols:
        op.add_column(
            "devices",
            sa.Column(
                "polarity_mode",
                sa.String(length=32),
                nullable=False,
                server_default="normal",
            ),
        )

    if "ix_devices_energy_flow_mode" not in existing_indexes:
        op.create_index("ix_devices_energy_flow_mode", "devices", ["energy_flow_mode"])
    if "ix_devices_polarity_mode" not in existing_indexes:
        op.create_index("ix_devices_polarity_mode", "devices", ["polarity_mode"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_indexes = {i["name"] for i in inspector.get_indexes("devices")}
    device_cols = {c["name"] for c in inspector.get_columns("devices")}

    if "ix_devices_polarity_mode" in existing_indexes:
        op.drop_index("ix_devices_polarity_mode", table_name="devices")
    if "ix_devices_energy_flow_mode" in existing_indexes:
        op.drop_index("ix_devices_energy_flow_mode", table_name="devices")
    if "polarity_mode" in device_cols:
        op.drop_column("devices", "polarity_mode")
    if "energy_flow_mode" in device_cols:
        op.drop_column("devices", "energy_flow_mode")
