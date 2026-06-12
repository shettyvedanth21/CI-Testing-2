"""Add fleet-scale performance indexes for Machine Health tables.

Adds:
- created_at indexes on 5 high-volume tables for efficient retention cleanup
- (tenant_id, device_id, signal_field, occurred_at) on anomaly events for dedup
- (tenant_id, device_id, signal_field, severity, ended_at) on anomaly events for open-event match

Revision ID: 20260522_0003_fleet_scale_indexes
Revises: 20260522_0002_anomaly_signal_breakdown_json
Create Date: 2026-05-22
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260522_0003_fleet_scale_indexes"
down_revision = "20260522_0002_anomaly_signal_breakdown_json"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "machine_health_history" in existing_tables:
        existing_indexes = {idx["name"] for idx in inspector.get_indexes("machine_health_history")}
        if "ix_mhh_created_at" not in existing_indexes:
            op.create_index("ix_mhh_created_at", "machine_health_history", ["created_at"])

    if "machine_health_feature_windows" in existing_tables:
        existing_indexes = {idx["name"] for idx in inspector.get_indexes("machine_health_feature_windows")}
        if "ix_mhfw_created_at" not in existing_indexes:
            op.create_index("ix_mhfw_created_at", "machine_health_feature_windows", ["created_at"])

    if "machine_anomaly_events" in existing_tables:
        existing_indexes = {idx["name"] for idx in inspector.get_indexes("machine_anomaly_events")}
        if "ix_mae_tenant_device_signal_occurred" not in existing_indexes:
            op.create_index(
                "ix_mae_tenant_device_signal_occurred",
                "machine_anomaly_events",
                ["tenant_id", "device_id", "signal_field", "occurred_at"],
            )
        if "ix_mae_tenant_device_signal_severity_ended" not in existing_indexes:
            op.create_index(
                "ix_mae_tenant_device_signal_severity_ended",
                "machine_anomaly_events",
                ["tenant_id", "device_id", "signal_field", "severity", "ended_at"],
            )
        if "ix_mae_created_at" not in existing_indexes:
            op.create_index("ix_mae_created_at", "machine_anomaly_events", ["created_at"])

    if "machine_anomaly_daily_counts" in existing_tables:
        existing_indexes = {idx["name"] for idx in inspector.get_indexes("machine_anomaly_daily_counts")}
        if "ix_madc_created_at" not in existing_indexes:
            op.create_index("ix_madc_created_at", "machine_anomaly_daily_counts", ["created_at"])

    if "machine_anomaly_weekly_counts" in existing_tables:
        existing_indexes = {idx["name"] for idx in inspector.get_indexes("machine_anomaly_weekly_counts")}
        if "ix_mawc_created_at" not in existing_indexes:
            op.create_index("ix_mawc_created_at", "machine_anomaly_weekly_counts", ["created_at"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "machine_health_history" in existing_tables:
        existing_indexes = {idx["name"] for idx in inspector.get_indexes("machine_health_history")}
        if "ix_mhh_created_at" in existing_indexes:
            op.drop_index("ix_mhh_created_at", table_name="machine_health_history")

    if "machine_health_feature_windows" in existing_tables:
        existing_indexes = {idx["name"] for idx in inspector.get_indexes("machine_health_feature_windows")}
        if "ix_mhfw_created_at" in existing_indexes:
            op.drop_index("ix_mhfw_created_at", table_name="machine_health_feature_windows")

    if "machine_anomaly_events" in existing_tables:
        existing_indexes = {idx["name"] for idx in inspector.get_indexes("machine_anomaly_events")}
        if "ix_mae_tenant_device_signal_occurred" in existing_indexes:
            op.drop_index("ix_mae_tenant_device_signal_occurred", table_name="machine_anomaly_events")
        if "ix_mae_tenant_device_signal_severity_ended" in existing_indexes:
            op.drop_index("ix_mae_tenant_device_signal_severity_ended", table_name="machine_anomaly_events")
        if "ix_mae_created_at" in existing_indexes:
            op.drop_index("ix_mae_created_at", table_name="machine_anomaly_events")

    if "machine_anomaly_daily_counts" in existing_tables:
        existing_indexes = {idx["name"] for idx in inspector.get_indexes("machine_anomaly_daily_counts")}
        if "ix_madc_created_at" in existing_indexes:
            op.drop_index("ix_madc_created_at", table_name="machine_anomaly_daily_counts")

    if "machine_anomaly_weekly_counts" in existing_tables:
        existing_indexes = {idx["name"] for idx in inspector.get_indexes("machine_anomaly_weekly_counts")}
        if "ix_mawc_created_at" in existing_indexes:
            op.drop_index("ix_mawc_created_at", table_name="machine_anomaly_weekly_counts")
