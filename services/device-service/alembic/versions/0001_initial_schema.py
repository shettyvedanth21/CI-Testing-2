"""Create initial device-service schema.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-03-11
"""

from alembic import op
import sqlalchemy as sa


revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "devices",
        sa.Column("device_id", sa.String(length=50), nullable=False),
        sa.Column("tenant_id", sa.String(length=50), nullable=True),
        sa.Column("device_name", sa.String(length=255), nullable=False),
        sa.Column("device_type", sa.String(length=100), nullable=False),
        sa.Column("manufacturer", sa.String(length=255), nullable=True),
        sa.Column("model", sa.String(length=255), nullable=True),
        sa.Column("location", sa.String(length=500), nullable=True),
        sa.Column("phase_type", sa.String(length=20), nullable=True),
        sa.Column("data_source_type", sa.String(length=20), nullable=False, server_default="metered"),
        sa.Column("idle_current_threshold", sa.Numeric(precision=10, scale=4), nullable=True),
        sa.Column("legacy_status", sa.String(length=50), nullable=False, server_default="active"),
        sa.Column("last_seen_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("device_id"),
    )
    op.create_index("ix_devices_tenant_id", "devices", ["tenant_id"])
    op.create_index("ix_devices_device_type", "devices", ["device_type"])
    op.create_index("ix_devices_phase_type", "devices", ["phase_type"])
    op.create_index("ix_devices_data_source_type", "devices", ["data_source_type"])
    op.create_index("ix_devices_legacy_status", "devices", ["legacy_status"])
    op.create_index("ix_devices_last_seen_timestamp", "devices", ["last_seen_timestamp"])

    op.create_table(
        "device_shifts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("device_id", sa.String(length=50), nullable=False),
        sa.Column("tenant_id", sa.String(length=50), nullable=True),
        sa.Column("shift_name", sa.String(length=100), nullable=False),
        sa.Column("shift_start", sa.Time(), nullable=False),
        sa.Column("shift_end", sa.Time(), nullable=False),
        sa.Column("maintenance_break_minutes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("day_of_week", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["device_id"], ["devices.device_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_device_shifts_device_id", "device_shifts", ["device_id"])
    op.create_index("ix_device_shifts_tenant_id", "device_shifts", ["tenant_id"])

    op.create_table(
        "parameter_health_config",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("device_id", sa.String(length=50), nullable=False),
        sa.Column("tenant_id", sa.String(length=50), nullable=True),
        sa.Column("parameter_name", sa.String(length=100), nullable=False),
        sa.Column("normal_min", sa.Float(), nullable=True),
        sa.Column("normal_max", sa.Float(), nullable=True),
        sa.Column("max_min", sa.Float(), nullable=True),
        sa.Column("max_max", sa.Float(), nullable=True),
        sa.Column("weight", sa.Float(), nullable=False, server_default="0"),
        sa.Column("ignore_zero_value", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["device_id"], ["devices.device_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_parameter_health_config_device_id", "parameter_health_config", ["device_id"])
    op.create_index("ix_parameter_health_config_tenant_id", "parameter_health_config", ["tenant_id"])

    op.create_table(
        "device_properties",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("device_id", sa.String(length=50), nullable=False),
        sa.Column("property_name", sa.String(length=100), nullable=False),
        sa.Column("data_type", sa.String(length=20), nullable=False, server_default="float"),
        sa.Column("is_numeric", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["device_id"], ["devices.device_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("device_id", "property_name", name="uq_device_property_name"),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index("ix_device_properties_device_id", "device_properties", ["device_id"])

    op.create_table(
        "device_performance_trends",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("device_id", sa.String(length=50), nullable=False),
        sa.Column("bucket_start_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bucket_end_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bucket_timezone", sa.String(length=64), nullable=False, server_default="Asia/Kolkata"),
        sa.Column("interval_minutes", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("health_score", sa.Float(), nullable=True),
        sa.Column("uptime_percentage", sa.Float(), nullable=True),
        sa.Column("planned_minutes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("effective_minutes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("break_minutes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("points_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_valid", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["device_id"], ["devices.device_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("device_id", "bucket_start_utc", name="uq_perf_trend_device_bucket"),
    )
    op.create_index("ix_device_performance_trends_device_id", "device_performance_trends", ["device_id"])
    op.create_index("ix_device_performance_trends_bucket_start_utc", "device_performance_trends", ["bucket_start_utc"])
    op.create_index("ix_device_performance_trends_created_at", "device_performance_trends", ["created_at"])

    op.create_table(
        "idle_running_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("device_id", sa.String(length=50), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idle_duration_sec", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("idle_energy_kwh", sa.Numeric(precision=12, scale=6), nullable=False, server_default="0"),
        sa.Column("idle_cost", sa.Numeric(precision=12, scale=4), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(length=10), nullable=False, server_default="INR"),
        sa.Column("tariff_rate_used", sa.Numeric(precision=10, scale=4), nullable=False, server_default="0"),
        sa.Column("pf_estimated", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["device_id"], ["devices.device_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("device_id", "period_start", name="uq_idle_log_device_day"),
    )
    op.create_index("idx_idle_log_device_period", "idle_running_log", ["device_id", "period_start"])


def downgrade() -> None:
    op.drop_index("idx_idle_log_device_period", table_name="idle_running_log")
    op.drop_table("idle_running_log")
    op.drop_index("ix_device_performance_trends_created_at", table_name="device_performance_trends")
    op.drop_index("ix_device_performance_trends_bucket_start_utc", table_name="device_performance_trends")
    op.drop_index("ix_device_performance_trends_device_id", table_name="device_performance_trends")
    op.drop_table("device_performance_trends")
    op.drop_index("ix_device_properties_device_id", table_name="device_properties")
    op.drop_table("device_properties")
    op.drop_index("ix_parameter_health_config_tenant_id", table_name="parameter_health_config")
    op.drop_index("ix_parameter_health_config_device_id", table_name="parameter_health_config")
    op.drop_table("parameter_health_config")
    op.drop_index("ix_device_shifts_tenant_id", table_name="device_shifts")
    op.drop_index("ix_device_shifts_device_id", table_name="device_shifts")
    op.drop_table("device_shifts")
    op.drop_index("ix_devices_last_seen_timestamp", table_name="devices")
    op.drop_index("ix_devices_legacy_status", table_name="devices")
    op.drop_index("ix_devices_data_source_type", table_name="devices")
    op.drop_index("ix_devices_phase_type", table_name="devices")
    op.drop_index("ix_devices_device_type", table_name="devices")
    op.drop_index("ix_devices_tenant_id", table_name="devices")
    op.drop_table("devices")
