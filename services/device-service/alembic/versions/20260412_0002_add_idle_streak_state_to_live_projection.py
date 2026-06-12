"""Add idle streak state to device live projection.

Revision ID: 20260412_0002
Revises: 20260412_0001
Create Date: 2026-04-12
"""

from alembic import op
import sqlalchemy as sa


revision = "20260412_0002"
down_revision = "20260412_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("device_live_state", sa.Column("idle_streak_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "device_live_state",
        sa.Column("idle_streak_duration_sec", sa.Integer(), nullable=False, server_default="0"),
    )
    op.alter_column("device_live_state", "idle_streak_duration_sec", server_default=None)


def downgrade() -> None:
    op.drop_column("device_live_state", "idle_streak_duration_sec")
    op.drop_column("device_live_state", "idle_streak_started_at")
