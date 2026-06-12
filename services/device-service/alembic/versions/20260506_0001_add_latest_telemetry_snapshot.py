"""Add latest telemetry snapshot table for projection-backed detail reads.

Revision ID: 20260506_0001
Revises: 20260426_0001_maintenance_log_foundation
Create Date: 2026-05-06
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260506_0001"
down_revision = "20260426_0001_maintenance_log_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "device_latest_telemetry_snapshot" in inspector.get_table_names():
        return

    op.create_table(
        "device_latest_telemetry_snapshot",
        sa.Column("device_id", sa.String(length=50), nullable=False),
        sa.Column("tenant_id", sa.String(length=10), nullable=False),
        sa.Column("sample_ts", sa.DateTime(timezone=True), nullable=True),
        sa.Column("projection_version", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("snapshot_version", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("runtime_status", sa.String(length=32), nullable=False, server_default="stopped"),
        sa.Column("load_state", sa.String(length=32), nullable=False, server_default="unknown"),
        sa.Column("current_band", sa.String(length=32), nullable=False, server_default="unknown"),
        sa.Column("last_power_kw", sa.Numeric(14, 6), nullable=True),
        sa.Column("last_current_a", sa.Numeric(14, 6), nullable=True),
        sa.Column("last_voltage_v", sa.Numeric(14, 6), nullable=True),
        sa.Column("numeric_fields_json", sa.Text(), nullable=True),
        sa.Column("source_fields_json", sa.Text(), nullable=True),
        sa.Column("normalization_version", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(
            ["device_id", "tenant_id"],
            ["devices.device_id", "devices.tenant_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("device_id", "tenant_id"),
    )
    op.create_index(
        "ix_device_latest_snapshot_sample_ts",
        "device_latest_telemetry_snapshot",
        ["sample_ts"],
    )
    op.create_index(
        "ix_device_latest_snapshot_projection_version",
        "device_latest_telemetry_snapshot",
        ["projection_version"],
    )
    op.create_index(
        "ix_device_latest_snapshot_updated_at",
        "device_latest_telemetry_snapshot",
        ["updated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_device_latest_snapshot_updated_at", table_name="device_latest_telemetry_snapshot")
    op.drop_index("ix_device_latest_snapshot_projection_version", table_name="device_latest_telemetry_snapshot")
    op.drop_index("ix_device_latest_snapshot_sample_ts", table_name="device_latest_telemetry_snapshot")
    op.drop_table("device_latest_telemetry_snapshot")
