"""Add data_source_type to devices table

Revision ID: add_data_source_type
Revises: add_device_performance_trends
Create Date: 2026-03-07
"""

from alembic import op
import sqlalchemy as sa


revision = "add_data_source_type"
down_revision = "add_device_performance_trends"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    device_cols = {c["name"] for c in inspector.get_columns("devices")}
    if "data_source_type" not in device_cols:
        op.add_column(
            "devices",
            sa.Column("data_source_type", sa.String(length=20), nullable=False, server_default="metered"),
        )
    existing_indexes = {i["name"] for i in inspector.get_indexes("devices")}
    if "ix_devices_data_source_type" not in existing_indexes:
        op.create_index("ix_devices_data_source_type", "devices", ["data_source_type"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_indexes = {i["name"] for i in inspector.get_indexes("devices")}
    if "ix_devices_data_source_type" in existing_indexes:
        op.drop_index("ix_devices_data_source_type", table_name="devices")
    device_cols = {c["name"] for c in inspector.get_columns("devices")}
    if "data_source_type" in device_cols:
        op.drop_column("devices", "data_source_type")
