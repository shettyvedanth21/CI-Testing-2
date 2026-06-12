"""add recent telemetry samples

Revision ID: 20260506_0002_add_recent_telemetry_samples
Revises: 20260506_0001
Create Date: 2026-05-06 12:15:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260506_0002_add_recent_telemetry_samples"
down_revision = "20260506_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "device_recent_telemetry_samples",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("device_id", sa.String(length=50), nullable=False),
        sa.Column("tenant_id", sa.String(length=10), nullable=False),
        sa.Column("sample_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("projection_version", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("runtime_status", sa.String(length=32), nullable=False, server_default="stopped"),
        sa.Column("load_state", sa.String(length=32), nullable=False, server_default="unknown"),
        sa.Column("current_band", sa.String(length=32), nullable=False, server_default="unknown"),
        sa.Column("telemetry_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["device_id", "tenant_id"],
            ["devices.device_id", "devices.tenant_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_device_recent_telemetry_device_sample",
        "device_recent_telemetry_samples",
        ["tenant_id", "device_id", "sample_ts"],
        unique=False,
    )
    op.create_index(
        "ix_device_recent_telemetry_device_projection",
        "device_recent_telemetry_samples",
        ["tenant_id", "device_id", "projection_version"],
        unique=False,
    )
    op.create_index(
        "ix_device_recent_telemetry_samples_tenant_id",
        "device_recent_telemetry_samples",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "ix_device_recent_telemetry_samples_sample_ts",
        "device_recent_telemetry_samples",
        ["sample_ts"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_device_recent_telemetry_samples_sample_ts", table_name="device_recent_telemetry_samples")
    op.drop_index("ix_device_recent_telemetry_samples_tenant_id", table_name="device_recent_telemetry_samples")
    op.drop_index("ix_device_recent_telemetry_device_projection", table_name="device_recent_telemetry_samples")
    op.drop_index("ix_device_recent_telemetry_device_sample", table_name="device_recent_telemetry_samples")
    op.drop_table("device_recent_telemetry_samples")
