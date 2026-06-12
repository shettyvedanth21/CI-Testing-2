"""Machine anomaly activity foundation tables.

Revision ID: 20260522_0001_machine_anomaly_activity
Revises: 20260521_0001_machine_degradation_score
Create Date: 2026-05-22
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260522_0001_machine_anomaly_activity"
down_revision = "20260521_0001_machine_degradation_score"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "machine_anomaly_baselines" not in existing_tables:
        op.create_table(
            "machine_anomaly_baselines",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.String(length=10), nullable=False),
            sa.Column("device_id", sa.String(length=50), nullable=False),
            sa.Column("field_name", sa.String(length=32), nullable=False),
            sa.Column("time_window", sa.String(length=16), nullable=False, server_default="5min"),
            sa.Column("baseline_mean", sa.Float(), nullable=True),
            sa.Column("baseline_std", sa.Float(), nullable=True),
            sa.Column("baseline_median", sa.Float(), nullable=True),
            sa.Column("baseline_mad", sa.Float(), nullable=True),
            sa.Column("baseline_p05", sa.Float(), nullable=True),
            sa.Column("baseline_p95", sa.Float(), nullable=True),
            sa.Column("reading_count", sa.Integer(), nullable=True),
            sa.Column("quality_score", sa.Float(), nullable=True),
            sa.Column("learned_from_ts", sa.DateTime(timezone=True), nullable=True),
            sa.Column("learned_to_ts", sa.DateTime(timezone=True), nullable=True),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="candidate"),
            sa.Column("baseline_version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(
                ["device_id", "tenant_id"],
                ["devices.device_id", "devices.tenant_id"],
                ondelete="CASCADE",
            ),
            sa.UniqueConstraint("tenant_id", "device_id", "field_name", "time_window", "baseline_version", name="uq_mab_tenant_device_field_window_version"),
            sa.CheckConstraint(
                "status IN ('active','candidate','retired')",
                name="ck_mab_status",
            ),
            sa.CheckConstraint(
                "quality_score IS NULL OR (quality_score >= 0 AND quality_score <= 1)",
                name="ck_mab_quality_range",
            ),
            sa.CheckConstraint(
                "baseline_std IS NULL OR baseline_std >= 0",
                name="ck_mab_std_non_negative",
            ),
            sa.CheckConstraint(
                "baseline_mad IS NULL OR baseline_mad >= 0",
                name="ck_mab_mad_non_negative",
            ),
            sa.CheckConstraint(
                "time_window IN ('5min','1min')",
                name="ck_mab_time_window",
            ),
        )
        op.create_index("ix_mab_tenant_device_status", "machine_anomaly_baselines", ["tenant_id", "device_id", "status"])
        op.create_index("ix_mab_tenant_device_field", "machine_anomaly_baselines", ["tenant_id", "device_id", "field_name"])

    if "machine_anomaly_events" not in existing_tables:
        op.create_table(
            "machine_anomaly_events",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.String(length=10), nullable=False),
            sa.Column("device_id", sa.String(length=50), nullable=False),
            sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("duration_seconds", sa.Integer(), nullable=True),
            sa.Column("signal_field", sa.String(length=32), nullable=False),
            sa.Column("signal_value", sa.Float(), nullable=True),
            sa.Column("baseline_mean", sa.Float(), nullable=True),
            sa.Column("baseline_std", sa.Float(), nullable=True),
            sa.Column("z_score", sa.Float(), nullable=True),
            sa.Column("anomaly_type", sa.String(length=32), nullable=False, server_default="deviation"),
            sa.Column("severity", sa.String(length=16), nullable=False, server_default="mild"),
            sa.Column("confidence", sa.Float(), nullable=True),
            sa.Column("supply_related", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("startup_adjacent", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("mode_change", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("recurring", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("time_window", sa.String(length=16), nullable=False, server_default="5min"),
            sa.Column("correlated_signals_json", sa.Text(), nullable=True),
            sa.Column("baseline_version", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(
                ["device_id", "tenant_id"],
                ["devices.device_id", "devices.tenant_id"],
                ondelete="CASCADE",
            ),
            sa.CheckConstraint(
                "severity IN ('mild','strong','severe')",
                name="ck_mae_severity",
            ),
            sa.CheckConstraint(
                "anomaly_type IN ('deviation','persistent','trend')",
                name="ck_mae_anomaly_type",
            ),
            sa.CheckConstraint(
                "time_window IN ('5min','1min')",
                name="ck_mae_time_window",
            ),
            sa.CheckConstraint(
                "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
                name="ck_mae_confidence_range",
            ),
            sa.CheckConstraint(
                "duration_seconds IS NULL OR duration_seconds >= 0",
                name="ck_mae_duration_non_negative",
            ),
            sa.CheckConstraint(
                "baseline_std IS NULL OR baseline_std >= 0",
                name="ck_mae_std_non_negative",
            ),
        )
        op.create_index("ix_mae_tenant_device_occurred", "machine_anomaly_events", ["tenant_id", "device_id", "occurred_at"])
        op.create_index("ix_mae_tenant_occurred", "machine_anomaly_events", ["tenant_id", "occurred_at"])
        op.create_index("ix_mae_tenant_device_severity", "machine_anomaly_events", ["tenant_id", "device_id", "severity"])
        op.create_index("ix_mae_device_occurred", "machine_anomaly_events", ["device_id", "occurred_at"])

    if "machine_anomaly_daily_counts" not in existing_tables:
        op.create_table(
            "machine_anomaly_daily_counts",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.String(length=10), nullable=False),
            sa.Column("device_id", sa.String(length=50), nullable=False),
            sa.Column("date", sa.Date(), nullable=False),
            sa.Column("total_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("mild_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("strong_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("severe_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("supply_related_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("top_signal", sa.String(length=32), nullable=True),
            sa.Column("avg_confidence", sa.Float(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(
                ["device_id", "tenant_id"],
                ["devices.device_id", "devices.tenant_id"],
                ondelete="CASCADE",
            ),
            sa.UniqueConstraint("tenant_id", "device_id", "date", name="uq_madc_tenant_device_date"),
            sa.CheckConstraint(
                "total_count >= 0 AND mild_count >= 0 AND strong_count >= 0 AND severe_count >= 0 AND supply_related_count >= 0",
                name="ck_madc_counts_non_negative",
            ),
            sa.CheckConstraint(
                "total_count >= mild_count + strong_count + severe_count",
                name="ck_madc_total_consistency",
            ),
            sa.CheckConstraint(
                "avg_confidence IS NULL OR (avg_confidence >= 0 AND avg_confidence <= 1)",
                name="ck_madc_confidence_range",
            ),
        )
        op.create_index("ix_madc_tenant_device_date", "machine_anomaly_daily_counts", ["tenant_id", "device_id", "date"])
        op.create_index("ix_madc_tenant_date", "machine_anomaly_daily_counts", ["tenant_id", "date"])

    if "machine_anomaly_weekly_counts" not in existing_tables:
        op.create_table(
            "machine_anomaly_weekly_counts",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.String(length=10), nullable=False),
            sa.Column("device_id", sa.String(length=50), nullable=False),
            sa.Column("week_start_date", sa.Date(), nullable=False),
            sa.Column("total_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("mild_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("strong_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("severe_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("week_over_week_change", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(
                ["device_id", "tenant_id"],
                ["devices.device_id", "devices.tenant_id"],
                ondelete="CASCADE",
            ),
            sa.UniqueConstraint("tenant_id", "device_id", "week_start_date", name="uq_mawc_tenant_device_week"),
            sa.CheckConstraint(
                "total_count >= 0 AND mild_count >= 0 AND strong_count >= 0 AND severe_count >= 0",
                name="ck_mawc_counts_non_negative",
            ),
            sa.CheckConstraint(
                "total_count >= mild_count + strong_count + severe_count",
                name="ck_mawc_total_consistency",
            ),
        )
        op.create_index("ix_mawc_tenant_device_week", "machine_anomaly_weekly_counts", ["tenant_id", "device_id", "week_start_date"])
        op.create_index("ix_mawc_tenant_week", "machine_anomaly_weekly_counts", ["tenant_id", "week_start_date"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "machine_anomaly_weekly_counts" in existing_tables:
        op.drop_index("ix_mawc_tenant_week", table_name="machine_anomaly_weekly_counts")
        op.drop_index("ix_mawc_tenant_device_week", table_name="machine_anomaly_weekly_counts")
        op.drop_table("machine_anomaly_weekly_counts")

    if "machine_anomaly_daily_counts" in existing_tables:
        op.drop_index("ix_madc_tenant_date", table_name="machine_anomaly_daily_counts")
        op.drop_index("ix_madc_tenant_device_date", table_name="machine_anomaly_daily_counts")
        op.drop_table("machine_anomaly_daily_counts")

    if "machine_anomaly_events" in existing_tables:
        op.drop_index("ix_mae_device_occurred", table_name="machine_anomaly_events")
        op.drop_index("ix_mae_tenant_device_severity", table_name="machine_anomaly_events")
        op.drop_index("ix_mae_tenant_occurred", table_name="machine_anomaly_events")
        op.drop_index("ix_mae_tenant_device_occurred", table_name="machine_anomaly_events")
        op.drop_table("machine_anomaly_events")

    if "machine_anomaly_baselines" in existing_tables:
        op.drop_index("ix_mab_tenant_device_field", table_name="machine_anomaly_baselines")
        op.drop_index("ix_mab_tenant_device_status", table_name="machine_anomaly_baselines")
        op.drop_table("machine_anomaly_baselines")
