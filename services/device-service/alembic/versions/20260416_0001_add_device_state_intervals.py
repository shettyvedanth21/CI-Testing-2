"""Add durable device state interval logging table.

Revision ID: 20260416_0001
Revises: 20260415_0002
Create Date: 2026-04-16
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260416_0001"
down_revision = "20260415_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "device_state_intervals",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(length=10), nullable=False),
        sa.Column("device_id", sa.String(length=50), nullable=False),
        sa.Column("state_type", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_sec", sa.Integer(), nullable=True),
        sa.Column("is_open", sa.Boolean(), nullable=False),
        sa.Column("opened_by_sample_ts", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by_sample_ts", sa.DateTime(timezone=True), nullable=True),
        sa.Column("opened_reason", sa.String(length=64), nullable=True),
        sa.Column("closed_reason", sa.String(length=64), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state_type IN ('idle', 'overconsumption', 'runtime_on')",
            name="ck_device_state_intervals_state_type",
        ),
        sa.ForeignKeyConstraint(
            ["device_id", "tenant_id"],
            ["devices.device_id", "devices.tenant_id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_device_state_intervals_device_state_started",
        "device_state_intervals",
        ["tenant_id", "device_id", "state_type", "started_at"],
        unique=False,
    )
    op.create_index(
        "ix_device_state_intervals_device_open",
        "device_state_intervals",
        ["tenant_id", "device_id", "is_open"],
        unique=False,
    )
    op.create_index(
        "ix_device_state_intervals_tenant_started",
        "device_state_intervals",
        ["tenant_id", "started_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_device_state_intervals_tenant_started", table_name="device_state_intervals")
    op.drop_index("ix_device_state_intervals_device_open", table_name="device_state_intervals")
    op.drop_index("ix_device_state_intervals_device_state_started", table_name="device_state_intervals")
    op.drop_table("device_state_intervals")
