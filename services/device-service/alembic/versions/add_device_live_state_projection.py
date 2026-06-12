"""Add device live-state projection table.

Revision ID: add_device_live_state
Revises: add_dashboard_snapshots
Create Date: 2026-03-19
"""

from alembic import op
import sqlalchemy as sa


revision = "add_device_live_state"
down_revision = "add_dashboard_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "device_live_state" in inspector.get_table_names():
        return

    op.create_table(
        "device_live_state",
        sa.Column("device_id", sa.String(length=50), nullable=False),
        sa.Column("last_telemetry_ts", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sample_ts", sa.DateTime(timezone=True), nullable=True),
        sa.Column("runtime_status", sa.String(length=32), nullable=False, server_default="stopped"),
        sa.Column("load_state", sa.String(length=32), nullable=False, server_default="unknown"),
        sa.Column("health_score", sa.Float(), nullable=True),
        sa.Column("uptime_percentage", sa.Float(), nullable=True),
        sa.Column("today_energy_kwh", sa.Numeric(14, 6), nullable=False, server_default="0"),
        sa.Column("today_idle_kwh", sa.Numeric(14, 6), nullable=False, server_default="0"),
        sa.Column("today_offhours_kwh", sa.Numeric(14, 6), nullable=False, server_default="0"),
        sa.Column("today_overconsumption_kwh", sa.Numeric(14, 6), nullable=False, server_default="0"),
        sa.Column("today_loss_kwh", sa.Numeric(14, 6), nullable=False, server_default="0"),
        sa.Column("today_loss_cost_inr", sa.Numeric(14, 4), nullable=False, server_default="0"),
        sa.Column("month_energy_kwh", sa.Numeric(14, 6), nullable=False, server_default="0"),
        sa.Column("month_energy_cost_inr", sa.Numeric(14, 4), nullable=False, server_default="0"),
        sa.Column("today_running_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("today_effective_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("day_bucket", sa.Date(), nullable=True),
        sa.Column("month_bucket", sa.Date(), nullable=True),
        sa.Column("last_energy_kwh", sa.Numeric(14, 6), nullable=True),
        sa.Column("last_power_kw", sa.Numeric(14, 6), nullable=True),
        sa.Column("last_current_a", sa.Numeric(14, 6), nullable=True),
        sa.Column("last_voltage_v", sa.Numeric(14, 6), nullable=True),
        sa.Column("version", sa.BigInteger(), nullable=False, server_default="0"),
        # Keep default MySQL-compatible across strict SQL modes.
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["device_id"], ["devices.device_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("device_id"),
    )
    op.create_index("ix_device_live_state_runtime_status", "device_live_state", ["runtime_status"])
    op.create_index("ix_device_live_state_updated_at", "device_live_state", ["updated_at"])
    op.create_index("ix_device_live_state_day_bucket", "device_live_state", ["day_bucket"])
    op.create_index("ix_device_live_state_month_bucket", "device_live_state", ["month_bucket"])
    op.create_index("ix_device_live_state_version", "device_live_state", ["version"])


def downgrade() -> None:
    op.drop_index("ix_device_live_state_version", table_name="device_live_state")
    op.drop_index("ix_device_live_state_month_bucket", table_name="device_live_state")
    op.drop_index("ix_device_live_state_day_bucket", table_name="device_live_state")
    op.drop_index("ix_device_live_state_updated_at", table_name="device_live_state")
    op.drop_index("ix_device_live_state_runtime_status", table_name="device_live_state")
    op.drop_table("device_live_state")
