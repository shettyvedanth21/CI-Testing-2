"""Add insufficient_signals to status constraint, signal_completeness column,
and align weekly anomaly count columns with daily.

Adds:
- ck_mhl_status_values: include 'insufficient_signals' (was mapped to 'learning')
- machine_health_latest.signal_completeness: nullable Float
- machine_anomaly_weekly_counts: supply_related_count, top_signal, avg_confidence, signal_breakdown_json
- ck_mawc_counts_non_negative: include supply_related_count >= 0
- ck_mawc_confidence_range: avg_confidence range check

Revision ID: 20260522_0004_mh_constraint_and_columns
Revises: 20260522_0003_fleet_scale_indexes
Create Date: 2026-05-22
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260522_0004_mh_constraint_and_columns"
down_revision = "20260522_0003_fleet_scale_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "machine_health_latest" in existing_tables:
        existing_columns = {c["name"] for c in inspector.get_columns("machine_health_latest")}
        if "status" in existing_columns:
            op.alter_column(
                "machine_health_latest",
                "status",
                type_=sa.String(24),
                existing_type=sa.String(16),
            )

        existing_constraints = {
            c["name"] for c in inspector.get_check_constraints("machine_health_latest")
        }
        if "ck_mhl_status_values" in existing_constraints:
            op.drop_constraint("ck_mhl_status_values", "machine_health_latest", type_="check")
        op.create_check_constraint(
            "ck_mhl_status_values",
            "machine_health_latest",
            "status IN ('healthy','watch','warning','critical','learning','insufficient_signals','unavailable')",
        )

        if "signal_completeness" not in existing_columns:
            op.add_column(
                "machine_health_latest",
                sa.Column("signal_completeness", sa.Float(), nullable=True),
            )

    if "machine_health_history" in existing_tables:
        existing_columns = {c["name"] for c in inspector.get_columns("machine_health_history")}
        if "status" in existing_columns:
            op.alter_column(
                "machine_health_history",
                "status",
                type_=sa.String(24),
                existing_type=sa.String(16),
            )

    if "machine_anomaly_weekly_counts" in existing_tables:
        existing_columns = {
            c["name"] for c in inspector.get_columns("machine_anomaly_weekly_counts")
        }

        if "supply_related_count" not in existing_columns:
            op.add_column(
                "machine_anomaly_weekly_counts",
                sa.Column("supply_related_count", sa.Integer(), nullable=False, server_default="0"),
            )

        if "top_signal" not in existing_columns:
            op.add_column(
                "machine_anomaly_weekly_counts",
                sa.Column("top_signal", sa.String(32), nullable=True),
            )

        if "avg_confidence" not in existing_columns:
            op.add_column(
                "machine_anomaly_weekly_counts",
                sa.Column("avg_confidence", sa.Float(), nullable=True),
            )

        if "signal_breakdown_json" not in existing_columns:
            op.add_column(
                "machine_anomaly_weekly_counts",
                sa.Column("signal_breakdown_json", sa.Text(), nullable=True),
            )

        existing_constraints = {
            c["name"] for c in inspector.get_check_constraints("machine_anomaly_weekly_counts")
        }
        if "ck_mawc_counts_non_negative" in existing_constraints:
            op.drop_constraint("ck_mawc_counts_non_negative", "machine_anomaly_weekly_counts", type_="check")
        op.create_check_constraint(
            "ck_mawc_counts_non_negative",
            "machine_anomaly_weekly_counts",
            "total_count >= 0 AND mild_count >= 0 AND strong_count >= 0 AND severe_count >= 0 AND supply_related_count >= 0",
        )

        if "ck_mawc_confidence_range" not in existing_constraints:
            op.create_check_constraint(
                "ck_mawc_confidence_range",
                "machine_anomaly_weekly_counts",
                "avg_confidence IS NULL OR (avg_confidence >= 0 AND avg_confidence <= 1)",
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "machine_anomaly_weekly_counts" in existing_tables:
        existing_constraints = {
            c["name"] for c in inspector.get_check_constraints("machine_anomaly_weekly_counts")
        }
        if "ck_mawc_confidence_range" in existing_constraints:
            op.drop_constraint("ck_mawc_confidence_range", "machine_anomaly_weekly_counts", type_="check")

        if "ck_mawc_counts_non_negative" in existing_constraints:
            op.drop_constraint("ck_mawc_counts_non_negative", "machine_anomaly_weekly_counts", type_="check")
        op.create_check_constraint(
            "ck_mawc_counts_non_negative",
            "machine_anomaly_weekly_counts",
            "total_count >= 0 AND mild_count >= 0 AND strong_count >= 0 AND severe_count >= 0",
        )

        existing_columns = {
            c["name"] for c in inspector.get_columns("machine_anomaly_weekly_counts")
        }
        if "signal_breakdown_json" in existing_columns:
            op.drop_column("machine_anomaly_weekly_counts", "signal_breakdown_json")
        if "avg_confidence" in existing_columns:
            op.drop_column("machine_anomaly_weekly_counts", "avg_confidence")
        if "top_signal" in existing_columns:
            op.drop_column("machine_anomaly_weekly_counts", "top_signal")
        if "supply_related_count" in existing_columns:
            op.drop_column("machine_anomaly_weekly_counts", "supply_related_count")

    if "machine_health_latest" in existing_tables:
        existing_columns = {c["name"] for c in inspector.get_columns("machine_health_latest")}
        if "signal_completeness" in existing_columns:
            op.drop_column("machine_health_latest", "signal_completeness")

        op.execute(
            "UPDATE machine_health_latest SET status='learning' WHERE status='insufficient_signals'"
        )

        existing_constraints = {
            c["name"] for c in inspector.get_check_constraints("machine_health_latest")
        }
        if "ck_mhl_status_values" in existing_constraints:
            op.drop_constraint("ck_mhl_status_values", "machine_health_latest", type_="check")
        op.create_check_constraint(
            "ck_mhl_status_values",
            "machine_health_latest",
            "status IN ('healthy','watch','warning','critical','learning','unavailable')",
        )

        if "status" in existing_columns:
            op.alter_column(
                "machine_health_latest",
                "status",
                type_=sa.String(16),
                existing_type=sa.String(24),
            )

    if "machine_health_history" in existing_tables:
        existing_columns = {c["name"] for c in inspector.get_columns("machine_health_history")}

        op.execute(
            "UPDATE machine_health_history SET status='learning' WHERE status='insufficient_signals'"
        )

        if "status" in existing_columns:
            op.alter_column(
                "machine_health_history",
                "status",
                type_=sa.String(16),
                existing_type=sa.String(24),
            )
