"""Add Maintenance Log foundation table.

Revision ID: 20260426_0001_maintenance_log_foundation
Revises: 20260423_0001_device_mqtt_credentials
Create Date: 2026-04-26
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260426_0001_maintenance_log_foundation"
down_revision = "20260423_0001_device_mqtt_credentials"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "maintenance_logs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(length=10), nullable=False),
        sa.Column("device_id", sa.String(length=50), nullable=False),
        sa.Column("maintenance_date", sa.Date(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("cost", sa.Numeric(12, 2), nullable=False, server_default="0.00"),
        sa.Column("performed_by", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=True),
        sa.Column("next_due_date", sa.Date(), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["device_id", "tenant_id"],
            ["devices.device_id", "devices.tenant_id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_maintenance_logs_tenant_device_date",
        "maintenance_logs",
        ["tenant_id", "device_id", "maintenance_date"],
        unique=False,
    )
    op.create_index(
        "ix_maintenance_logs_tenant_device_next_due",
        "maintenance_logs",
        ["tenant_id", "device_id", "next_due_date"],
        unique=False,
    )
    op.create_index(
        "ix_maintenance_logs_tenant_status",
        "maintenance_logs",
        ["tenant_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_maintenance_logs_tenant_status", table_name="maintenance_logs")
    op.drop_index("ix_maintenance_logs_tenant_device_next_due", table_name="maintenance_logs")
    op.drop_index("ix_maintenance_logs_tenant_device_date", table_name="maintenance_logs")
    op.drop_table("maintenance_logs")
