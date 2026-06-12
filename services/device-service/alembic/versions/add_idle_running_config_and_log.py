"""Add idle threshold config and idle running log

Revision ID: add_idle_running_config_and_log
Revises: add_data_source_type
Create Date: 2026-03-07
"""

from alembic import op
import sqlalchemy as sa


revision = "add_idle_running_config_and_log"
down_revision = "add_data_source_type"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    device_cols = {c["name"] for c in inspector.get_columns("devices")}
    if "idle_current_threshold" not in device_cols:
        op.add_column(
            "devices",
            sa.Column("idle_current_threshold", sa.Numeric(precision=10, scale=4), nullable=True),
        )

    existing_tables = set(inspector.get_table_names())
    if "idle_running_log" not in existing_tables:
        op.create_table(
            "idle_running_log",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("device_id", sa.String(length=50), sa.ForeignKey("devices.device_id", ondelete="CASCADE"), nullable=False),
            sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
            sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
            sa.Column("idle_duration_sec", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("idle_energy_kwh", sa.Numeric(precision=12, scale=6), nullable=False, server_default="0"),
            sa.Column("idle_cost", sa.Numeric(precision=12, scale=4), nullable=False, server_default="0"),
            sa.Column("currency", sa.String(length=10), nullable=False, server_default="INR"),
            sa.Column("tariff_rate_used", sa.Numeric(precision=10, scale=4), nullable=False, server_default="0"),
            sa.Column("pf_estimated", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.UniqueConstraint("device_id", "period_start", name="uq_idle_log_device_day"),
        )

    existing_indexes = {i["name"] for i in inspector.get_indexes("idle_running_log")} if "idle_running_log" in inspector.get_table_names() else set()
    if "idx_idle_log_device_period" not in existing_indexes:
        op.create_index("idx_idle_log_device_period", "idle_running_log", ["device_id", "period_start"])


def downgrade() -> None:
    op.drop_index("idx_idle_log_device_period", table_name="idle_running_log")
    op.drop_table("idle_running_log")
    op.drop_column("devices", "idle_current_threshold")
